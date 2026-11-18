"""Versioned calendars; single-lesson changes never mutate the weekly template."""
from collections import defaultdict
from datetime import date, timedelta
from itertools import combinations
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from wizzy.db import get_db, many, one, run
from wizzy.errors import DomainError, data
from wizzy.media import consent_student
from wizzy.pagination import decode_cursor, page
from wizzy.schemas import LessonOverride, TermCreate, TimetablePut
from wizzy.security import Principal, audit, current, guardian

router = APIRouter(prefix='/api/v1', tags=['timetables'])
SLOT_COLUMNS = 'id,weekday,period,subject,weeks,start_time,end_time,room'
LESSON_FIELDS = ('period', 'subject', 'start_time', 'end_time', 'room')


def read_term(db, actor, term_id, *, writing=False):
    row = one(db, 'SELECT * FROM terms WHERE id=:id', id=term_id)
    if not row:
        raise DomainError(404, 'not_found', '学期不存在')
    consent_student(db, actor, row['student_id'])
    # Same lock order as withdrawal: student, then term.
    # Reads pin append-only snapshots by revision, without requiring child UPDATE access.
    row = one(db, 'SELECT * FROM terms WHERE id=:id ' +
              ('FOR NO KEY UPDATE' if writing else ''), id=term_id)
    if not row:
        raise DomainError(404, 'not_found', '学期不存在')
    return dict(row)


def normal_times(row):
    for field in ('start_time', 'end_time'):
        if row[field] is not None:
            row[field] = row[field].strftime('%H:%M')
    return row


def read_schedule(db, term):
    slots = many(db, f'''SELECT {SLOT_COLUMNS} FROM timetable_slots
        WHERE term_id=:id AND template_revision=:revision ORDER BY position''',
        id=term['id'], revision=term['template_revision'])
    history = many(db, '''SELECT id,revision,template_revision,slot_id,source_date,action,
        target_date,period,subject,start_time,end_time,room,reason FROM lesson_overrides
        WHERE term_id=:id AND revision<=:revision ORDER BY revision''',
        id=term['id'], revision=term['revision'])
    return [normal_times(row) for row in slots], [normal_times(row) for row in history]


def week_number(term, day):
    offset = (term['start_date'].isoweekday() - term['week_starts_on']) % 7
    return ((day - term['start_date']).days + offset) // 7 + 1


def occurs(term, slot, day):
    week = week_number(term, day)
    return (term['start_date'] <= day <= term['end_date'] and
            slot['weekday'] == day.isoweekday() and
            (slot['weeks'] == 'all' or slot['weeks'] == ('odd' if week % 2 else 'even')))


def effective_overrides(history):
    return {(row['slot_id'], row['source_date']): row for row in history}


def instances(term, slots, history):
    changes = effective_overrides(history)
    result = []
    for offset in range((term['end_date'] - term['start_date']).days + 1):
        source = term['start_date'] + timedelta(days=offset)
        for slot in slots:
            if not occurs(term, slot, source):
                continue
            change = changes.get((slot['id'], source))
            if change and change['action'] == 'cancel':
                continue
            moved = change is not None and change['action'] == 'move'
            day = change['target_date'] if moved else source
            fields = {key: (change if moved else slot)[key] for key in LESSON_FIELDS}
            result.append({'id': f"{term['id']}:{slot['id']}:{source.isoformat()}",
                           'slot_id': slot['id'], 'source_date': source, 'date': day,
                           'week_number': week_number(term, day), 'overridden': moved, **fields})
    return sorted(result, key=lambda row: (row['date'], row['period'], row['id']))


def conflicts(items):
    days = defaultdict(list)
    for row in items:
        days[row['date']].append(row)
    warnings = []
    for day, rows in sorted(days.items()):
        for first, second in combinations(rows, 2):
            same_period = first['period'] == second['period']
            overlap = (first['start_time'] is not None and second['start_time'] is not None and
                       first['start_time'] < second['end_time'] and second['start_time'] < first['end_time'])
            if same_period or overlap:
                warnings.append({'date': day.isoformat(),
                                 'slot_ids': [str(first['slot_id']), str(second['slot_id'])],
                                 'instance_ids': [first['id'], second['id']],
                                 'reason': 'same_period' if same_period else 'time_overlap'})
    return warnings


def check_conflicts(term, slots, history, confirmed):
    warnings = conflicts(instances(term, slots, history))
    if warnings and not confirmed:
        raise DomainError(409, 'schedule_conflict', '存在课程冲突，请核对并显式确认', field_errors=warnings)
    return warnings


def view(db, term):
    slots, history = read_schedule(db, term)
    return {'term': term, 'slots': slots, 'overrides': history,
            'conflicts': conflicts(instances(term, slots, history))}


def check_revision(term, expected):
    if term['revision'] != expected:
        raise DomainError(409, 'revision_conflict', '课表已更新，请刷新后重试')


def append_version(db, actor, term, kind, confirmed):
    revision = term['revision'] + 1
    run(db, '''INSERT INTO timetable_versions(tenant_id,student_id,deletion_epoch,term_id,
        revision,kind,actor_id,confirm_conflicts) VALUES(:tenant,:student,:epoch,:term,:revision,:kind,:actor,:confirmed)''',
        tenant=term['tenant_id'], student=term['student_id'], epoch=term['deletion_epoch'],
        term=term['id'], revision=revision, kind=kind, actor=actor.user_id, confirmed=confirmed)
    run(db, '''UPDATE terms SET revision=:revision,
        template_revision=CASE WHEN :kind='template' THEN :revision ELSE template_revision END WHERE id=:id''',
        id=term['id'], revision=revision, kind=kind)
    term['revision'] = revision
    if kind == 'template':
        term['template_revision'] = revision
    return revision


@router.post('/students/{student_id}/terms', status_code=201)
def create_term(student_id: UUID, body: TermCreate, request: Request,
                actor: Principal = Depends(current), db: Session = Depends(get_db, scope='function')):
    guardian(actor)
    student = consent_student(db, actor, student_id)
    term_id = uuid4()
    term = one(db, '''INSERT INTO terms(id,tenant_id,student_id,deletion_epoch,name,start_date,end_date,week_starts_on)
        VALUES(:id,:tenant,:student,:epoch,:name,:start_date,:end_date,:week_starts_on) RETURNING *''',
        id=term_id, tenant=actor.tenant_id, student=student_id, epoch=student['deletion_epoch'], **body.model_dump())
    audit(db, request, actor, 'timetable.term_created', term_id)
    return data(request, term)


@router.get('/students/{student_id}/terms')
def list_terms(student_id: UUID, request: Request, limit: int = Query(20, ge=1, le=100),
               cursor: str | None = None, actor: Principal = Depends(current),
               db: Session = Depends(get_db, scope='function')):
    consent_student(db, actor, student_id)
    after_date, after_id = decode_cursor(cursor)
    rows = many(db, '''SELECT * FROM terms WHERE student_id=:student
        AND (CAST(:after_date AS timestamptz) IS NULL OR (created_at,id)>(CAST(:after_date AS timestamptz),CAST(:after_id AS uuid)))
        ORDER BY created_at,id LIMIT :limit''', student=student_id,
        after_date=after_date, after_id=after_id, limit=limit+1)
    return data(request, page(rows, limit))


@router.get('/timetables/{term_id}')
def get_timetable(term_id: UUID, request: Request, actor: Principal = Depends(current),
                  db: Session = Depends(get_db, scope='function')):
    return data(request, view(db, read_term(db, actor, term_id)))


@router.put('/timetables/{term_id}')
def put_timetable(term_id: UUID, body: TimetablePut, request: Request,
                  actor: Principal = Depends(current), db: Session = Depends(get_db, scope='function')):
    guardian(actor)
    term = read_term(db, actor, term_id, writing=True)
    check_revision(term, body.expected_revision)
    previous, history = read_schedule(db, term)
    slots = [slot.model_dump() for slot in body.slots]
    before, after = {row['id']: row for row in previous}, {row['id']: row for row in slots}
    for change in effective_overrides(history).values():
        if change['action'] != 'restore' and before.get(change['slot_id']) != after.get(change['slot_id']):
            raise DomainError(409, 'active_override_conflict', '请先恢复该课程的单次调整，再修改或删除模板课程')
    check_conflicts(term, slots, history, body.confirm_conflicts)
    revision = append_version(db, actor, term, 'template', body.confirm_conflicts)
    for position, slot in enumerate(slots):
        run(db, '''INSERT INTO timetable_slots(tenant_id,student_id,deletion_epoch,term_id,template_revision,
            position,id,weekday,period,subject,weeks,start_time,end_time,room)
            VALUES(:tenant,:student,:epoch,:term,:revision,:position,:id,:weekday,:period,:subject,:weeks,:start_time,:end_time,:room)''',
            tenant=term['tenant_id'], student=term['student_id'], epoch=term['deletion_epoch'],
            term=term_id, revision=revision, position=position, **slot)
    audit(db, request, actor, 'timetable.template_saved', term_id)
    return data(request, view(db, term))


@router.get('/timetables/{term_id}/instances')
def get_instances(term_id: UUID, start_date: date, end_date: date, request: Request,
                  actor: Principal = Depends(current), db: Session = Depends(get_db, scope='function')):
    if not 0 <= (end_date-start_date).days < 93:
        raise DomainError(422, 'invalid_date_range', '查询起止必须有序且不超过93天')
    term = read_term(db, actor, term_id)
    slots, history = read_schedule(db, term)
    items = [row for row in instances(term, slots, history) if start_date <= row['date'] <= end_date]
    return data(request, {'items': items, 'timezone': term['timezone'], 'revision': term['revision'],
                          'conflicts': conflicts(items)})


@router.post('/lesson-overrides', status_code=201)
def create_override(body: LessonOverride, request: Request, actor: Principal = Depends(current),
                    db: Session = Depends(get_db, scope='function')):
    guardian(actor)
    term = read_term(db, actor, body.term_id, writing=True)
    check_revision(term, body.expected_revision)
    slots, history = read_schedule(db, term)
    original = next((row for row in slots if row['id'] == body.slot_id), None)
    if original is None or not occurs(term, original, body.source_date):
        raise DomainError(422, 'invalid_lesson_source', '源日期没有该模板课程')
    if body.target_date is not None and not term['start_date'] <= body.target_date <= term['end_date']:
        raise DomainError(422, 'invalid_lesson_target', '目标日期必须在同一学期内')
    replacement = body.replacement.model_dump() if body.replacement else dict.fromkeys(LESSON_FIELDS)
    change = {'id': uuid4(), 'slot_id': body.slot_id, 'source_date': body.source_date,
              'action': body.action, 'target_date': body.target_date, 'reason': body.reason, **replacement}
    check_conflicts(term, slots, history+[change], body.confirm_conflicts)
    template_revision = term['template_revision']
    revision = append_version(db, actor, term, 'override', body.confirm_conflicts)
    run(db, '''INSERT INTO lesson_overrides(id,tenant_id,student_id,deletion_epoch,term_id,revision,
        template_revision,slot_id,source_date,action,target_date,period,subject,start_time,end_time,room,reason)
        VALUES(:id,:tenant,:student,:epoch,:term,:revision,:template_revision,:slot_id,:source_date,
            :action,:target_date,:period,:subject,:start_time,:end_time,:room,:reason)''',
        tenant=term['tenant_id'], student=term['student_id'], epoch=term['deletion_epoch'],
        term=term['id'], revision=revision, template_revision=template_revision, **change)
    audit(db, request, actor, 'timetable.lesson_'+body.action, change['id'])
    return data(request, view(db, term))
