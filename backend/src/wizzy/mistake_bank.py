"""Group graded homework into one mistake record per question, then print it again later."""
import json
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, ConfigDict, Field
from pypdf import PdfReader, PdfWriter
from sqlalchemy import text
from sqlalchemy.orm import Session

from wizzy.db import get_db, many, one, run
from wizzy.errors import DomainError, data
from wizzy.homework_results import page_answers
from wizzy.media import consent_student
from wizzy.security import Principal, current, guardian

router = APIRouter(prefix='/api/v1', tags=['mistake bank'])
SHANGHAI = ZoneInfo('Asia/Shanghai')
FONT = Path(__file__).resolve().parent / 'assets' / 'NotoSansSC-Regular.ttf'
SUBJECTS = {'math': '数学', 'english': '英语', 'chinese': '语文', 'mixed': '多学科', 'unknown': '学科待确认'}


class SheetBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    item_ids: list[UUID] = Field(min_length=1, max_length=40)
    due_in_days: int | None = Field(default=None, ge=1, le=90, strict=True)
    due_date: date | None = None


def normalize(value):
    return ''.join(unicodedata.normalize('NFKC', value or '').split()).casefold()


def stem_key(question):
    if question.get('item_type') in ('word', 'phrase'):
        answer = normalize(question.get('answer'))
        return ('vocabulary|' + question['item_type'] + '|' + answer)[:400] if answer else ''
    reasoning = normalize(question.get('reasoning') or '')
    if len(reasoning) >= 12:
        return reasoning[:400]
    return normalize((question.get('number') or '') + '|' + (question.get('answer') or ''))[:400]


def display_stem(question):
    number = (question.get('number') or '').strip()
    reasoning = (question.get('reasoning') or '').strip()
    return f'{number} {reasoning[:80]}'.strip()[:500]


def project(content, page_count):
    questions = []
    for page in page_answers(content, int(page_count or 0)):
        for question in page['questions']:
            if question['verdict'] not in ('correct', 'incorrect'):
                continue
            if question['grading_basis'] == 'none' or (question['grading_basis'] == 'spelling_only' and question['verdict'] == 'correct'):
                continue
            key = stem_key(question)
            if not key:
                continue
            questions.append({
                'position': page['position'], 'subject': page['subject'], 'number': question['number'],
                'verdict': question['verdict'], 'student_answer': question['student_answer'],
                'reasoning': question['reasoning'], 'answer': question['answer'],
                'stem_key': key, 'stem': display_stem(question),
            })
    return questions


def sync_submission(db, submission_id):
    row = db.execute(text('SELECT public.latest_homework_output(:id)'), {'id': submission_id}).scalar_one()
    if not row:
        return 0
    questions = project(row['content'], row['page_count'])
    return db.execute(text('SELECT public.apply_mistake_bank(:id,:output,CAST(:questions AS jsonb))'),
                      {'id': submission_id, 'output': row['output_id'],
                       'questions': json.dumps(questions, ensure_ascii=False)}).scalar_one()


def sync_student(db, student_id):
    pending = db.execute(text('SELECT public.pending_mistake_submissions(:id)'), {'id': student_id}).scalars().all()
    for submission_id in pending:
        sync_submission(db, submission_id)


def day_text(value):
    return value.astimezone(SHANGHAI).strftime('%Y年%m月%d日')


def story(attempts):
    """Timeline plus whether the written answer stayed the same."""
    lines = []
    for index, attempt in enumerate(attempts, 1):
        verdict = '错' if attempt['verdict'] == 'incorrect' else '对'
        written = (attempt['student_answer'] or '').strip() or '未识别到作答'
        lines.append(f"{day_text(attempt['submitted_at'])} 第{index}次，{verdict}，写成{written}")
    keys = [normalize(attempt['student_answer']) for attempt in attempts]
    summary = ''
    if len(attempts) >= 2:
        if len(set(keys)) == 1:
            summary = '这几次提交写成的答案相同。'
        else:
            change = next(index for index, key in enumerate(keys) if key != keys[0])
            summary = f'前{change}次答案相同，第{change + 1}次变了。'
    repeated = Counter(normalize(attempt['student_answer']) for attempt in attempts
                       if attempt['verdict'] == 'incorrect' and normalize(attempt['student_answer']))
    peak = max(repeated.values(), default=0)
    note = f'同一种写法出现了 {peak} 次，这一步可能还没弄懂。' if peak >= 2 else ''
    return lines, summary, peak, note


def worksheet_lines(items, kind):
    lines = ['错题再做', '请独立完成，做完后拍照交给小知。'] if kind == 'questions' else ['错题再做 · 答案', '这一页给家长核对。']
    for index, item in enumerate(items, 1):
        lines.append(f"{index}. {item['stem']}")
        if kind == 'questions':
            lines.extend(['作答：', ''])
        else:
            lines.append('参考答案：' + ((item.get('reference_answer') or '').strip() or '本题暂无参考答案'))
        lines.append('')
    return lines


def render_pdf(lines):
    if not FONT.is_file():
        raise DomainError(503, 'font_missing', '打印字体尚未就绪，请稍后重试', retryable=True)
    font = ImageFont.truetype(FONT, 36)
    small = ImageFont.truetype(FONT, 28)
    width, height, margin = 1240, 1754, 80
    usable = width - margin * 2

    def wrap(text, face):
        rows, current = [], ''
        for char in text or ' ':
            trial = current + char
            if face.getlength(trial) > usable and current:
                rows.append(current)
                current = char
            else:
                current = trial
        if current:
            rows.append(current)
        return rows or ['']

    wrapped = []
    for index, line in enumerate(lines):
        wrapped.extend((font if index < 2 else small, row) for row in wrap(line, font if index < 2 else small))
        if index < 2:
            wrapped.append((small, ''))
    pages, page, y = [], [], margin
    for face, row in wrapped:
        step = 56 if face is font else 44
        if y + step > height - margin:
            pages.append(page)
            page, y = [], margin
        page.append((face, row, y))
        y += step
    pages.append(page or [(font, lines[0] if lines else '', margin)])
    writer = PdfWriter()
    for page in pages:
        image = Image.new('RGB', (width, height), 'white')
        draw = ImageDraw.Draw(image)
        for face, row, top in page:
            if row:
                draw.text((margin, top), row, font=face, fill='#243044')
        buffer = BytesIO()
        image.save(buffer, format='PDF', resolution=150)
        buffer.seek(0)
        writer.add_page(PdfReader(buffer).pages[0])
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def repeated_peaks(db, item_ids):
    if not item_ids:
        return {}
    rows = db.execute(text('''SELECT item_id, student_answer FROM mistake_attempts
        WHERE item_id = ANY(:ids) AND verdict='incorrect' '''), {'ids': item_ids}).mappings()
    groups = defaultdict(Counter)
    for row in rows:
        key = normalize(row['student_answer'])
        if key:
            groups[row['item_id']][key] += 1
    return {item: max(counter.values()) for item, counter in groups.items()}


def list_items(db, student_id, actor):
    rows = many(db, '''SELECT i.id, i.subject, i.stem, i.reference_answer,
            count(*) FILTER (WHERE a.verdict='incorrect') AS incorrect_count,
            count(*) FILTER (WHERE a.verdict='correct') AS correct_count,
            (array_agg(a.verdict ORDER BY a.submitted_at DESC, a.id DESC))[1] AS latest_verdict,
            max(a.submitted_at) AS latest_at
        FROM mistake_items i JOIN mistake_attempts a ON a.item_id=i.id
        WHERE i.student_id=:student AND i.dismissed_at IS NULL
        GROUP BY i.id ORDER BY max(a.submitted_at) DESC, i.id''', student=student_id)
    peaks = repeated_peaks(db, [row['id'] for row in rows])
    items = []
    for row in rows:
        peak = peaks.get(row['id'], 0)
        item = {**row, 'subject_label': SUBJECTS.get(row['subject'], row['subject']),
                'latest_day': day_text(row['latest_at']), 'repeated_count': peak,
                'incorrect_count': int(row['incorrect_count']), 'correct_count': int(row['correct_count'])}
        if actor.audience == 'guardian' and peak >= 2:
            item['guardian_note'] = f'同一种写法出现了 {peak} 次，这一步可能还没弄懂。'
        items.append(item)
    return items


def chosen_due(body: SheetBody):
    if (body.due_date is None) == (body.due_in_days is None):
        raise DomainError(422, 'invalid_due', '请选择 3 天、7 天或一个具体日期')
    due = body.due_date or datetime.now(SHANGHAI).date() + timedelta(days=body.due_in_days)
    today = datetime.now(SHANGHAI).date()
    if due < today or due > today + timedelta(days=90):
        raise DomainError(422, 'invalid_due', '复习日期需要在今天起的 90 天内')
    return due


@router.get('/students/{student_id}/mistake-bank')
def mistake_bank(student_id: UUID, request: Request, actor: Principal = Depends(current),
                 db: Session = Depends(get_db, scope='function')):
    consent_student(db, actor, student_id)
    sync_student(db, student_id)
    return data(request, {'items': list_items(db, student_id, actor)})


@router.get('/students/{student_id}/mistake-bank/{item_id}')
def mistake_detail(student_id: UUID, item_id: UUID, request: Request, actor: Principal = Depends(current),
                   db: Session = Depends(get_db, scope='function')):
    consent_student(db, actor, student_id)
    item = one(db, 'SELECT * FROM mistake_items WHERE id=:id AND student_id=:student AND dismissed_at IS NULL',
               id=item_id, student=student_id)
    if not item:
        raise DomainError(404, 'not_found', '错题不存在')
    attempts = many(db, '''SELECT id, submission_id, page_position, question_number, submitted_at,
            student_answer, verdict, reasoning FROM mistake_attempts WHERE item_id=:id
            ORDER BY submitted_at, id''', id=item_id)
    lines, summary, peak, note = story(attempts)
    payload = {'id': item['id'], 'stem': item['stem'], 'subject_label': SUBJECTS.get(item['subject'], item['subject']),
               'reference_answer': item['reference_answer'], 'summary': summary, 'timeline': lines,
               'attempts': [{**attempt, 'day': day_text(attempt['submitted_at']),
                             'verdict_label': '正确' if attempt['verdict'] == 'correct' else '错误'} for attempt in attempts]}
    if actor.audience == 'guardian' and note:
        payload['guardian_note'] = note
        payload['repeated_count'] = peak
    return data(request, payload)


@router.post('/students/{student_id}/mistake-bank/{item_id}/dismiss')
def dismiss_item(student_id: UUID, item_id: UUID, request: Request, actor: Principal = Depends(current),
                 db: Session = Depends(get_db, scope='function')):
    guardian(actor)
    consent_student(db, actor, student_id)
    if not run(db, 'SELECT public.dismiss_mistake_item(:id)', id=item_id).scalar_one():
        raise DomainError(404, 'not_found', '错题不存在')
    return data(request, {'dismissed': True})


def sheet_payload(db, sheet):
    items = many(db, 'SELECT id, stem, reference_answer FROM mistake_items WHERE id = ANY(:ids)', ids=list(sheet['item_ids']))
    by_id = {item['id']: item for item in items}
    attempts = many(db, '''SELECT item_id, submitted_at FROM mistake_attempts WHERE item_id = ANY(:ids)''',
                    ids=list(sheet['item_ids'])) if sheet['item_ids'] else []
    ordered = []
    for item_id in sheet['item_ids']:
        item = by_id.get(item_id)
        if not item:
            continue
        returned = any(row['item_id'] == item_id and row['submitted_at'] > sheet['created_at'] for row in attempts)
        ordered.append({**item, 'returned': returned})
    returned = [item['returned'] for item in ordered]
    state = '已交回' if returned and all(returned) else '还有题没掌握' if any(returned) else '待再做'
    return {'id': sheet['id'], 'due_date': sheet['due_date'], 'created_at': sheet['created_at'],
            'state': state, 'items': ordered}


@router.get('/students/{student_id}/mistake-sheets')
def sheets(student_id: UUID, request: Request, actor: Principal = Depends(current),
           db: Session = Depends(get_db, scope='function')):
    consent_student(db, actor, student_id)
    rows = many(db, '''SELECT id, due_date, created_at, item_ids FROM mistake_sheets
        WHERE student_id=:student ORDER BY due_date, created_at''', student=student_id)
    return data(request, {'items': [sheet_payload(db, row) for row in rows]})


@router.post('/students/{student_id}/mistake-sheets', status_code=201)
def create_sheet(student_id: UUID, body: SheetBody, request: Request, actor: Principal = Depends(current),
                 db: Session = Depends(get_db, scope='function')):
    guardian(actor)
    consent_student(db, actor, student_id)
    due = chosen_due(body)
    sheet_id = run(db, 'SELECT public.create_mistake_sheet(:student,:items,:due)',
                   student=student_id, items=body.item_ids, due=due).scalar_one()
    if not sheet_id:
        raise DomainError(422, 'invalid_sheet', '这些题暂时不能放进同一份复习卷')
    sheet = one(db, 'SELECT id, due_date, created_at, item_ids FROM mistake_sheets WHERE id=:id', id=sheet_id)
    return data(request, sheet_payload(db, sheet))


@router.get('/mistake-sheets/{sheet_id}/pdf')
def sheet_pdf(sheet_id: UUID, request: Request, kind: str = Query('questions'),
              actor: Principal = Depends(current), db: Session = Depends(get_db, scope='function')):
    if kind not in ('questions', 'answers'):
        raise DomainError(422, 'invalid_kind', '请选择题目卷或答案卷')
    if kind == 'answers':
        guardian(actor)
    sheet = one(db, 'SELECT id, student_id, due_date, created_at, item_ids FROM mistake_sheets WHERE id=:id', id=sheet_id)
    if not sheet:
        raise DomainError(404, 'not_found', '复习卷不存在')
    consent_student(db, actor, sheet['student_id'])
    payload = sheet_payload(db, sheet)
    lines = [f"复习日期 {sheet['due_date'].isoformat()}", *worksheet_lines(payload['items'], kind)]
    filename = 'review-answers.pdf' if kind == 'answers' else 'review-questions.pdf'
    return Response(render_pdf(lines), media_type='application/pdf',
                    headers={'Content-Disposition': f'attachment; filename="{filename}"'})
