"""Family-scoped daily homework, manual confirmation and source-preserving imports."""
import hashlib
import json
from datetime import date, datetime
from difflib import SequenceMatcher
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from wizzy import daily_homework_schemas as s
from wizzy.db import get_db, many, one, run
from wizzy.errors import DomainError, data
from wizzy.media import consent_student, ready_asset
from wizzy.pagination import decode_cursor, page
from wizzy.security import Principal, audit, current, guardian, limited

router = APIRouter(prefix='/api/v1', tags=['daily homework'])
SUBJECTS = ('chinese', 'math', 'english', 'other')
EMPTY_SUBJECTS = dict.fromkeys(SUBJECTS, 'unknown')


def dump(value):
    return json.dumps(jsonable_encoder(value), ensure_ascii=False, sort_keys=True)


def conflict(message='内容已变化，请刷新后重新核对'):
    raise DomainError(409, 'revision_conflict', message)


def request_receipt(db, actor, student, request, payload):
    """Serialize a request key before mutation and reject changed-payload replay."""
    key = str(payload.client_request_id)
    digest = hashlib.sha256((request.url.path + dump(payload.model_dump(mode='json'))).encode()).hexdigest()
    run(db, 'SELECT pg_advisory_xact_lock(hashtextextended(:key,0))', key=f'{actor.tenant_id}:{key}')
    previous = one(db, 'SELECT * FROM homework_item_events WHERE tenant_id=:tenant AND client_request_id=:key',
                   tenant=actor.tenant_id, key=payload.client_request_id)
    if previous:
        if previous['student_id'] != student['id'] or previous['request_hash'] != digest or previous['deletion_epoch'] != student['deletion_epoch']:
            conflict('此操作编号已用于其他内容，请刷新后重试')
        return previous['receipt'], digest
    return None, digest


def event(db, actor, student, payload, digest, action, project, *, item=None, before=None, after=None, reason=''):
    identifier = uuid4()
    run(db, '''INSERT INTO homework_item_events(id,tenant_id,student_id,deletion_epoch,item_id,actor_id,
        actor_audience,action,client_request_id,request_hash,before_value,after_value,receipt,reason)
        VALUES(:id,:tenant,:student,:epoch,:item,:actor,:audience,:action,:key,:hash,
            CAST(:before AS jsonb),CAST(:after AS jsonb),'{}',:reason)''',
        id=identifier, tenant=actor.tenant_id, student=student['id'], epoch=student['deletion_epoch'],
        item=item, actor=actor.user_id, audience=actor.audience, action=action, key=payload.client_request_id,
        hash=digest, before=dump(before), after=dump(after), reason=reason)
    result = project()
    run(db, 'UPDATE homework_item_events SET receipt=CAST(:value AS jsonb) WHERE id=:id', value=dump(result), id=identifier)
    return result


def day_record(db, actor, student, assigned_date, *, create=False, lock=False):
    row = one(db, '''SELECT * FROM homework_days WHERE student_id=:student AND assigned_date=:date
        AND deletion_epoch=:epoch''' + (' FOR UPDATE' if lock else ''),
        student=student['id'], date=assigned_date, epoch=student['deletion_epoch'])
    if not row and create:
        run(db, '''INSERT INTO homework_days(id,tenant_id,student_id,deletion_epoch,assigned_date)
            VALUES(:id,:tenant,:student,:epoch,:date) ON CONFLICT DO NOTHING''',
            id=uuid4(), tenant=actor.tenant_id, student=student['id'], epoch=student['deletion_epoch'], date=assigned_date)
        return day_record(db, actor, student, assigned_date, lock=lock)
    return dict(row) if row else None


def pending_imports(db, student, assigned_date):
    return one(db, '''SELECT count(*) AS n FROM homework_imports
        WHERE student_id=:student AND deletion_epoch=:epoch AND status NOT IN ('applied','discarded','cancelled')
        AND (target_date IS NULL OR target_date=:date OR EXISTS(SELECT 1 FROM jsonb_array_elements(draft->'items') x
            WHERE x->>'assigned_date'=CAST(:date AS text)) OR EXISTS(SELECT 1 FROM jsonb_array_elements(draft->'no_homework') x
            WHERE x->>'assigned_date'=CAST(:date AS text)))''',
        student=student['id'], epoch=student['deletion_epoch'], date=assigned_date)['n']


def item_projection(db, row, *, detail=False):
    result = dict(row)
    result.pop('tenant_id', None)
    result.pop('deletion_epoch', None)
    result.pop('deleted_at', None)
    result['sources'] = many(db, '''SELECT src.id,src.image_id,img.media_id,src.run_id,src.raw_text,src.bbox,
        src.source_mark,img.crop_rect,i.source_label FROM homework_item_sources src
        JOIN homework_import_images img ON img.id=src.image_id JOIN homework_imports i ON i.id=img.import_id
        WHERE src.item_id=:id ORDER BY img.position,src.id''', id=row['id'])
    if detail:
        result['events'] = many(db, '''SELECT id,actor_audience,action,reason,before_value,after_value,created_at
            FROM homework_item_events WHERE item_id=:id ORDER BY created_at,id''', id=row['id'])
    return result


def item_record(db, actor, identifier, *, lock=False, include_deleted=False):
    query = 'SELECT * FROM homework_items WHERE id=:id' + ('' if include_deleted else ' AND deleted_at IS NULL')
    row = one(db, query, id=identifier)
    if not row:
        raise DomainError(404, 'not_found', '作业不存在')
    student = consent_student(db, actor, row['student_id'])
    if student['deletion_epoch'] != row['deletion_epoch']:
        raise DomainError(404, 'not_found', '作业不存在')
    if lock:
        # Consistent order with import publication and consent withdrawal: student -> day -> item.
        one(db, 'SELECT id FROM homework_days WHERE id=:id FOR UPDATE', id=row['day_id'])
        row = one(db, query + ' FOR UPDATE', id=identifier)
        if not row:
            raise DomainError(404, 'not_found', '作业不存在')
    return dict(row), student


def read_item(db, identifier):
    row = one(db, '''SELECT i.*,d.assigned_date FROM homework_items i JOIN homework_days d ON d.id=i.day_id
        WHERE i.id=:id''', id=identifier)
    return item_projection(db, row, detail=True)


def read_day(db, actor, student, assigned_date):
    row = day_record(db, actor, student, assigned_date)
    items = many(db, '''SELECT i.*,d.assigned_date FROM homework_items i JOIN homework_days d ON d.id=i.day_id
        WHERE i.student_id=:student AND i.deletion_epoch=:epoch AND d.assigned_date=:date
        AND i.deleted_at IS NULL ORDER BY i.subject,i.sort_order,i.created_at,i.id''',
        student=student['id'], epoch=student['deletion_epoch'], date=assigned_date)
    required = [x for x in items if x['work_state'] != 'exempted' and x['item_type'] != 'preparation']
    preparation = [x for x in items if x['work_state'] != 'exempted' and x['item_type'] == 'preparation']
    completed = sum(x['work_state'] == 'completed' for x in required)
    verified = sum(x['work_state'] == 'completed' and x['verification_state'] == 'guardian_confirmed' for x in required)
    pending = pending_imports(db, student, assigned_date)
    complete = bool(row and row['scope_state'] == 'complete' and not pending)
    return {'id': row['id'] if row else None, 'student_id': student['id'], 'assigned_date': assigned_date,
            'revision': row['revision'] if row else 0, 'scope_state': 'complete' if complete else 'incomplete',
            'subjects': row['subjects'] if row else EMPTY_SUBJECTS, 'pending_imports': pending,
            'items': [item_projection(db, x) for x in items],
            'summary': {'required': len(required), 'completed': completed, 'verified': verified,
                'preparation_total': len(preparation), 'preparation_completed': sum(x['work_state'] == 'completed' for x in preparation),
                'all_verified': bool(complete and required and verified == len(required)),
                'no_homework': bool(complete and not required and all(row['subjects'].get(x) == 'confirmed_none' for x in SUBJECTS))}}


def invalidate_day(db, day_id, subject=None):
    if subject:
        run(db, '''UPDATE homework_days SET revision=revision+1,scope_state='incomplete',
            subjects=jsonb_set(subjects,ARRAY[:subject], '"has_items"'::jsonb),confirmed_by=NULL,confirmed_at=NULL
            WHERE id=:id''', subject=subject, id=day_id)
    else:
        run(db, "UPDATE homework_days SET revision=revision+1,scope_state='incomplete',confirmed_by=NULL,confirmed_at=NULL WHERE id=:id", id=day_id)


def insert_item(db, actor, student, day, item):
    identifier = uuid4()
    run(db, '''INSERT INTO homework_items(id,tenant_id,student_id,deletion_epoch,day_id,subject,item_type,
        title,raw_text,locator,due_at,sort_order) VALUES(:id,:tenant,:student,:epoch,:day,:subject,:type,:title,
        :raw,CAST(:locator AS jsonb),:due,(SELECT coalesce(max(sort_order),0)+1 FROM homework_items WHERE day_id=:day))''',
        id=identifier, tenant=actor.tenant_id, student=student['id'], epoch=student['deletion_epoch'], day=day['id'],
        subject=item.subject, type=item.item_type, title=item.title, raw=item.raw_text, locator=dump(item.locator), due=item.due_at)
    invalidate_day(db, day['id'], item.subject)
    return identifier


@router.get('/students/{student_id}/homework-days')
def days(student_id: UUID, request: Request, date_from: date = Query(alias='from'), date_to: date = Query(alias='to'),
         actor: Principal = Depends(current), db: Session = Depends(get_db, scope='function')):
    student = consent_student(db, actor, student_id)
    if not 0 <= (date_to - date_from).days <= 30:
        raise DomainError(422, 'validation_error', '每次查询范围应为1至31天')
    rows = many(db, '''SELECT assigned_date FROM homework_days WHERE student_id=:student AND deletion_epoch=:epoch
        AND assigned_date BETWEEN :start AND :end ORDER BY assigned_date DESC''',
        student=student_id, epoch=student['deletion_epoch'], start=date_from, end=date_to)
    return data(request, {'items': [read_day(db, actor, student, x['assigned_date']) for x in rows]})


@router.get('/students/{student_id}/homework-backlog')
def backlog(student_id: UUID, request: Request, limit: int = Query(default=20, ge=1, le=100),
            actor: Principal = Depends(current), db: Session = Depends(get_db, scope='function')):
    student = consent_student(db, actor, student_id)
    rows = many(db, '''SELECT i.*,d.assigned_date FROM homework_items i JOIN homework_days d ON d.id=i.day_id
        WHERE i.student_id=:student AND i.deletion_epoch=:epoch AND i.deleted_at IS NULL
        AND i.work_state NOT IN ('completed','exempted') AND d.assigned_date<:today
        ORDER BY d.assigned_date,i.sort_order,i.id LIMIT :limit''', student=student_id,
        epoch=student['deletion_epoch'], today=datetime.now(ZoneInfo('Asia/Shanghai')).date(), limit=limit)
    return data(request, {'items': [item_projection(db, x) for x in rows]})


@router.get('/students/{student_id}/homework-days/{assigned_date}')
def day(student_id: UUID, assigned_date: date, request: Request, actor: Principal = Depends(current),
        db: Session = Depends(get_db, scope='function')):
    student = consent_student(db, actor, student_id)
    return data(request, read_day(db, actor, student, assigned_date))


@router.post('/students/{student_id}/homework-days/{assigned_date}/items', status_code=201)
def create_item(student_id: UUID, assigned_date: date, body: s.ItemCreate, request: Request,
                actor: Principal = Depends(current), db: Session = Depends(get_db, scope='function')):
    guardian(actor)
    student = consent_student(db, actor, student_id)
    receipt, digest = request_receipt(db, actor, student, request, body)
    if receipt is not None:
        return data(request, receipt)
    day = day_record(db, actor, student, assigned_date, create=True, lock=True)
    identifier = insert_item(db, actor, student, day, body)
    result = event(db, actor, student, body, digest, 'created', lambda: read_item(db, identifier), item=identifier,
                   after=body.model_dump(mode='json'))
    audit(db, request, actor, 'homework_item.created', identifier)
    return data(request, result)


@router.get('/homework-items/{item_id}')
def get_item(item_id: UUID, request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope='function')):
    item_record(db, actor, item_id)
    return data(request, read_item(db, item_id))


@router.put('/homework-items/{item_id}')
def update_item(item_id: UUID, body: s.ItemUpdate, request: Request, actor: Principal = Depends(current),
                db: Session = Depends(get_db, scope='function')):
    guardian(actor)
    row, student = item_record(db, actor, item_id, lock=True, include_deleted=True)
    receipt, digest = request_receipt(db, actor, student, request, body)
    if receipt is not None:
        return data(request, receipt)
    if row['deleted_at'] is not None:
        raise DomainError(404, 'not_found', '作业不存在')
    if row['revision'] != body.expected_revision:
        conflict()
    if (body.deleted or body.exempted is not None) and not body.reason.strip():
        raise DomainError(422, 'reason_required', '删除、免做或恢复免做需要填写原因')
    fields = {key: value for key, value in body.model_dump(exclude_unset=True).items()
              if key in ('subject', 'item_type', 'title', 'raw_text', 'locator', 'due_at')}
    if any(value is None for key, value in fields.items() if key != 'due_at'):
        raise DomainError(422, 'validation_error', '内容字段不能设为空值')
    for key, value in fields.items():
        if key == 'locator':
            run(db, 'UPDATE homework_items SET locator=CAST(:value AS jsonb) WHERE id=:id', value=dump(value), id=item_id)
        else:
            run(db, f'UPDATE homework_items SET {key}=:value WHERE id=:id', value=value, id=item_id)
    if body.exempted is not None:
        run(db, 'UPDATE homework_items SET work_state=:state WHERE id=:id',
            state='exempted' if body.exempted else 'pending', id=item_id)
    if body.deleted:
        run(db, 'UPDATE homework_items SET deleted_at=clock_timestamp() WHERE id=:id', id=item_id)
    run(db, "UPDATE homework_items SET revision=revision+1,verification_state='unverified',completion_actor=NULL WHERE id=:id", id=item_id)
    invalidate_day(db, row['day_id'], fields.get('subject'))
    result = event(db, actor, student, body, digest, 'updated', lambda: read_item(db, item_id), item=item_id,
                   before=row, after=body.model_dump(mode='json', exclude_unset=True), reason=body.reason)
    return data(request, result)


@router.post('/homework-items/{item_id}/progress')
def progress(item_id: UUID, body: s.Progress, request: Request, actor: Principal = Depends(current),
             db: Session = Depends(get_db, scope='function')):
    row, student = item_record(db, actor, item_id, lock=True)
    receipt, digest = request_receipt(db, actor, student, request, body)
    if receipt is not None:
        return data(request, receipt)
    if body.state == 'exempted' or row['work_state'] == 'exempted':
        raise DomainError(403, 'guardian_required', '免做及恢复免做请由家长修改')
    if row['revision'] != body.expected_revision:
        conflict()
    run(db, '''UPDATE homework_items SET work_state=:state,verification_state='unverified',completion_actor=:actor,
        revision=revision+1 WHERE id=:id''', state=body.state,
        actor=actor.audience if body.state == 'completed' else None, id=item_id)
    result = event(db, actor, student, body, digest, 'progress', lambda: read_item(db, item_id), item=item_id,
                   before={'work_state': row['work_state']}, after={'work_state': body.state}, reason=body.reason)
    return data(request, result)


@router.post('/homework-items/{item_id}/verify')
def verify(item_id: UUID, body: s.Verify, request: Request, actor: Principal = Depends(current),
           db: Session = Depends(get_db, scope='function')):
    guardian(actor)
    row, student = item_record(db, actor, item_id, lock=True)
    receipt, digest = request_receipt(db, actor, student, request, body)
    if receipt is not None:
        return data(request, receipt)
    if row['revision'] != body.expected_revision:
        conflict()
    if row['work_state'] == 'exempted':
        conflict('免做事项无需核验，请先恢复为应做')
    run(db, '''UPDATE homework_items SET work_state=:state,verification_state=:verified,completion_actor=:actor,
        revision=revision+1 WHERE id=:id''', state='completed' if body.completed else 'pending',
        verified='guardian_confirmed' if body.completed else 'unverified', actor='guardian' if body.completed else None, id=item_id)
    result = event(db, actor, student, body, digest, 'verified' if body.completed else 'confirmed_incomplete',
                   lambda: read_item(db, item_id), item=item_id, before={'work_state': row['work_state']},
                   after={'completed': body.completed}, reason=body.reason)
    return data(request, result)


@router.put('/students/{student_id}/homework-days/{assigned_date}/scope')
def scope(student_id: UUID, assigned_date: date, body: s.Scope, request: Request, actor: Principal = Depends(current),
          db: Session = Depends(get_db, scope='function')):
    guardian(actor)
    student = consent_student(db, actor, student_id)
    receipt, digest = request_receipt(db, actor, student, request, body)
    if receipt is not None:
        return data(request, receipt)
    original = day_record(db, actor, student, assigned_date, lock=True)
    if (original['revision'] if original else 0) != body.expected_revision:
        conflict()
    row = original or day_record(db, actor, student, assigned_date, create=True, lock=True)
    subjects = body.subjects.model_dump()
    items = many(db, 'SELECT subject FROM homework_items WHERE day_id=:id AND deleted_at IS NULL', id=row['id'])
    for subject in SUBJECTS:
        has_items = any(x['subject'] == subject for x in items)
        if subjects[subject] == 'confirmed_none' and has_items or subjects[subject] == 'has_items' and not has_items:
            conflict('科目的无作业声明与清单内容冲突，请先核对')
    if body.scope_state == 'complete' and ('unknown' in subjects.values() or pending_imports(db, student, assigned_date)):
        conflict('还有未核对科目或待处理导入，请先处理后确认完整')
    run(db, '''UPDATE homework_days SET subjects=CAST(:subjects AS jsonb),scope_state=:state,revision=revision+1,
        confirmed_by=:actor,confirmed_at=clock_timestamp() WHERE id=:id''',
        subjects=dump(subjects), state=body.scope_state, actor=actor.user_id, id=row['id'])
    result = event(db, actor, student, body, digest, 'scope', lambda: read_day(db, actor, student, assigned_date),
                   before=original, after=body.model_dump(mode='json'))
    return data(request, result)


def import_record(db, actor, identifier, *, lock=False):
    guardian(actor)
    row = one(db, 'SELECT * FROM homework_imports WHERE id=:id', id=identifier)
    if not row:
        raise DomainError(404, 'not_found', '导入记录不存在')
    student = consent_student(db, actor, row['student_id'])
    if row['deletion_epoch'] != student['deletion_epoch']:
        raise DomainError(404, 'not_found', '导入记录不存在')
    if lock:
        row = one(db, 'SELECT * FROM homework_imports WHERE id=:id FOR UPDATE', id=identifier)
    return dict(row), student


def import_projection(db, identifier):
    row = dict(one(db, 'SELECT * FROM homework_imports WHERE id=:id', id=identifier))
    for key in ('tenant_id', 'deletion_epoch', 'created_by'):
        row.pop(key, None)
    row['images'] = many(db, '''SELECT id,media_id,position,crop_rect,status,error_message
        FROM homework_import_images WHERE import_id=:id ORDER BY position''', id=identifier)
    row['runs'] = many(db, '''SELECT id,image_id,attempt,state,prompt_version,result,usage,cost_state,error_message,created_at,finished_at
        FROM homework_import_runs WHERE import_id=:id ORDER BY created_at,id''', id=identifier)
    row['duplicate_images'] = many(db, '''SELECT a.id AS image_id,b.import_id AS previous_import_id FROM homework_import_images a
        JOIN media_assets ma ON ma.id=a.media_id JOIN media_assets mb ON mb.sha256=ma.sha256
        JOIN homework_import_images b ON b.media_id=mb.id AND b.import_id<>a.import_id
            AND b.student_id=a.student_id AND b.tenant_id=a.tenant_id
        WHERE a.import_id=:id AND b.deletion_epoch=a.deletion_epoch ORDER BY b.import_id LIMIT 30''', id=identifier)
    row['suggestions'] = []
    for candidate in row['draft'].get('items', []):
        if candidate['id'] in row['applied_ids']:
            continue
        assigned = candidate.get('assigned_date') or row['target_date']
        if not assigned:
            continue
        existing = many(db, '''SELECT i.id,i.title,i.subject,i.item_type,i.locator FROM homework_items i
            JOIN homework_days d ON d.id=i.day_id WHERE i.student_id=:student AND i.deletion_epoch=:epoch
            AND d.assigned_date=:date AND i.subject=:subject AND i.deleted_at IS NULL LIMIT 100''',
            student=row['student_id'], epoch=one(db, 'SELECT deletion_epoch FROM homework_imports WHERE id=:id', id=identifier)['deletion_epoch'],
            date=assigned, subject=candidate['subject'])
        for item in existing:
            kind = 'exact' if candidate_key(item) == candidate_key(candidate) else 'possible_conflict'
            book = (candidate.get('locator') or {}).get('book_alias')
            same_book = book and book == item['locator'].get('book_alias')
            if kind == 'exact' or same_book or SequenceMatcher(None, candidate['title'], item['title']).ratio() >= .75:
                row['suggestions'].append({'candidate_id': candidate['id'], 'kind': kind,
                    'item_id': item['id'], 'title': item['title'], 'assigned_date': assigned})
    return row


@router.post('/homework-imports', status_code=202)
def create_import(body: s.ImportCreate, request: Request, actor: Principal = Depends(current),
                  db: Session = Depends(get_db, scope='function')):
    guardian(actor)
    student = consent_student(db, actor, body.student_id)
    receipt, digest = request_receipt(db, actor, student, request, body)
    if receipt is not None:
        return data(request, receipt)
    limited(request, 'homework-import', str(actor.tenant_id), maximum=20)
    # Serialize budget and source publication for one student.
    run(db, 'SELECT pg_advisory_xact_lock(hashtextextended(:key,0))', key=f'homework-import-budget:{actor.tenant_id}')
    used = one(db, '''SELECT count(*) AS n FROM homework_import_images img JOIN homework_imports i ON i.id=img.import_id
        WHERE i.tenant_id=:tenant AND i.created_at>=date_trunc('month',clock_timestamp() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai' ''',
        tenant=actor.tenant_id)['n']
    if used + len(body.images) > request.app.state.settings.monthly_page_limit:
        raise DomainError(429, 'import_limit_exceeded', '本月作业清单识别图片额度已用完，可继续手工录入')
    for image in body.images:
        media = ready_asset(db, actor, image.media_id)
        if media['student_id'] != body.student_id:
            raise DomainError(404, 'not_found', '图片不属于当前孩子')
    identifier = uuid4()
    run(db, '''INSERT INTO homework_imports(id,tenant_id,student_id,deletion_epoch,created_by,source_kind,source_label,target_date)
        VALUES(:id,:tenant,:student,:epoch,:actor,:kind,:label,:date)''', id=identifier, tenant=actor.tenant_id,
        student=body.student_id, epoch=student['deletion_epoch'], actor=actor.user_id, kind=body.source_kind,
        label=body.source_label, date=body.target_date)
    for position, image in enumerate(body.images, 1):
        run(db, '''INSERT INTO homework_import_images(id,tenant_id,student_id,deletion_epoch,import_id,media_id,position,crop_rect)
            VALUES(:id,:tenant,:student,:epoch,:import_id,:media,:position,CAST(:crop AS jsonb))''',
            id=uuid4(), tenant=actor.tenant_id, student=body.student_id, epoch=student['deletion_epoch'],
            import_id=identifier, media=image.media_id, position=position, crop=dump(image.crop_rect))
    result = event(db, actor, student, body, digest, 'import_created', lambda: import_projection(db, identifier))
    audit(db, request, actor, 'homework_import.created', identifier)
    return data(request, result)


@router.get('/homework-imports')
def imports(student_id: UUID, request: Request, limit: int = Query(default=20, ge=1, le=100), cursor: str | None = None,
            actor: Principal = Depends(current), db: Session = Depends(get_db, scope='function')):
    guardian(actor)
    student = consent_student(db, actor, student_id)
    after, identifier = decode_cursor(cursor)
    rows = many(db, '''SELECT id,student_id,target_date,source_label,status,revision,created_at FROM homework_imports
        WHERE student_id=:student AND deletion_epoch=:epoch
        AND (CAST(:after AS timestamptz) IS NULL OR (created_at,id)<(:after,:id))
        ORDER BY created_at DESC,id DESC LIMIT :limit''', student=student_id, epoch=student['deletion_epoch'],
        after=after, id=identifier, limit=limit+1)
    return data(request, page(rows, limit))


@router.get('/homework-imports/{import_id}')
def get_import(import_id: UUID, request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope='function')):
    import_record(db, actor, import_id)
    return data(request, import_projection(db, import_id))


def validate_draft(db, row, draft):
    image_ids = {x['id'] for x in many(db, 'SELECT id FROM homework_import_images WHERE import_id=:id', id=row['id'])}
    entries = [*draft.items, *draft.no_homework]
    identifiers = [x.id for x in entries]
    if len(set(identifiers)) != len(identifiers) or any(x.image_id not in image_ids for x in entries):
        raise DomainError(422, 'invalid_draft', '候选编号重复或来源图片不属于本次导入')
    applied = row['applied_ids']
    previous = {x['id']: x for x in [*row['draft']['items'], *row['draft']['no_homework']]}
    current = {x.id: x.model_dump(mode='json') for x in entries}
    for key in applied:
        if previous.get(key) != current.get(key):
            conflict('已入清单的条目请在每日清单中修改，不能覆盖其来源草稿')


@router.put('/homework-imports/{import_id}/draft')
def save_draft(import_id: UUID, body: s.DraftSave, request: Request, actor: Principal = Depends(current),
               db: Session = Depends(get_db, scope='function')):
    row, student = import_record(db, actor, import_id, lock=True)
    receipt, digest = request_receipt(db, actor, student, request, body)
    if receipt is not None:
        return data(request, receipt)
    if row['revision'] != body.expected_revision or row['status'] in ('queued', 'processing', 'cancelled', 'discarded'):
        conflict('识别尚未结束或记录已变化，请刷新后修改')
    validate_draft(db, row, body.draft)
    run(db, 'UPDATE homework_imports SET draft=CAST(:draft AS jsonb),revision=revision+1 WHERE id=:id',
        draft=dump(body.draft), id=import_id)
    result = event(db, actor, student, body, digest, 'draft_saved', lambda: import_projection(db, import_id))
    return data(request, result)


def candidate_key(item):
    return dump({'subject': item['subject'], 'item_type': item['item_type'], 'title': ''.join(item['title'].split()),
                 'locator': item.get('locator', {})})


@router.post('/homework-imports/{import_id}/apply')
def apply_import(import_id: UUID, body: s.Apply, request: Request, actor: Principal = Depends(current),
                 db: Session = Depends(get_db, scope='function')):
    row, student = import_record(db, actor, import_id, lock=True)
    receipt, digest = request_receipt(db, actor, student, request, body)
    if receipt is not None:
        return data(request, receipt)
    if row['revision'] != body.expected_revision or row['status'] in ('queued', 'processing', 'cancelled', 'discarded'):
        conflict()
    draft = s.Draft.model_validate(row['draft'])
    applied = dict(row['applied_ids'])
    selected = [x for x in draft.items if x.id not in applied and x.decision in ('include', 'merge')]
    no_homework = [x for x in draft.no_homework if x.id not in applied and x.confirmed and not x.excluded]
    if not selected and not no_homework and not any(x.decision == 'exclude' and x.id not in applied for x in draft.items) and not any(x.excluded and x.id not in applied for x in draft.no_homework):
        conflict('请先选择要入清单的条目、排除项或确认无作业声明')
    dates = sorted({x.assigned_date for x in [*selected, *no_homework] if x.assigned_date is not None})
    days = {}
    for assigned in dates:
        original = day_record(db, actor, student, assigned, lock=True)
        expected = body.day_revisions.get(assigned)
        if expected is None or expected != (original['revision'] if original else 0):
            conflict('目标日期清单已变化，请刷新该日期后重新确认')
        days[assigned] = original or day_record(db, actor, student, assigned, create=True, lock=True)
    for candidate in selected:
        if not candidate.assigned_date or not candidate.date_confirmed:
            conflict('每项作业都需要确认完整布置日期')
        day = days[candidate.assigned_date]
        if day['subjects'].get(candidate.subject) == 'confirmed_none' and not candidate.conflict_confirmed:
            conflict('该科原来声明无作业，请确认冲突后入清单')
        if any(x.subject == candidate.subject and x.assigned_date == candidate.assigned_date for x in no_homework):
            conflict('同一科目不能同时发布作业和无作业声明')
        if candidate.decision == 'merge':
            if not candidate.merge_item_id:
                conflict('请选择要合并的已有作业')
            existing, _ = item_record(db, actor, candidate.merge_item_id, lock=True)
            if existing['student_id'] != student['id'] or existing['day_id'] != day['id'] or existing['subject'] != candidate.subject:
                conflict('只能合并同一孩子、同一日期和同一学科的作业')
            identifier = existing['id']
        else:
            existing_items = many(db, 'SELECT subject,item_type,title,locator FROM homework_items WHERE day_id=:id AND deleted_at IS NULL', id=day['id'])
            exact_duplicate = any(candidate_key(x) == candidate_key(candidate.model_dump(mode='json')) for x in existing_items)
            if exact_duplicate and not candidate.conflict_confirmed:
                conflict('发现相同作业，请选择合并或明确确认仍需新增')
            identifier = insert_item(db, actor, student, day, candidate)
        run_row = one(db, '''SELECT id FROM homework_import_runs WHERE image_id=:image AND state='succeeded'
            ORDER BY attempt DESC LIMIT 1''', image=candidate.image_id)
        run(db, '''INSERT INTO homework_item_sources(id,tenant_id,student_id,deletion_epoch,item_id,image_id,run_id,
            candidate_id,raw_text,bbox,source_mark) VALUES(:id,:tenant,:student,:epoch,:item,:image,:run,:candidate,
            :raw,CAST(:bbox AS jsonb),:mark) ON CONFLICT DO NOTHING''', id=uuid4(), tenant=actor.tenant_id,
            student=student['id'], epoch=student['deletion_epoch'], item=identifier, image=candidate.image_id,
            run=run_row['id'] if run_row else None, candidate=candidate.id, raw=candidate.raw_text,
            bbox=dump(candidate.bbox), mark=candidate.source_mark)
        applied[candidate.id] = str(identifier)
    for claim in no_homework:
        if not claim.assigned_date:
            conflict('无作业声明也需要确认完整日期')
        day = days[claim.assigned_date]
        if one(db, 'SELECT 1 FROM homework_items WHERE day_id=:day AND subject=:subject AND deleted_at IS NULL', day=day['id'], subject=claim.subject):
            conflict('该科已有作业，请先核对矛盾后再声明无作业')
        run(db, '''UPDATE homework_days SET subjects=jsonb_set(subjects,ARRAY[:subject], '"confirmed_none"'::jsonb),
            revision=revision+1,scope_state='incomplete' WHERE id=:id''', subject=claim.subject, id=day['id'])
        applied[claim.id] = 'confirmed_none'
    for candidate in draft.items:
        if candidate.decision == 'exclude':
            applied[candidate.id] = 'excluded'
    for claim in draft.no_homework:
        if claim.excluded:
            applied[claim.id] = 'excluded'
    all_ids = {x.id for x in [*draft.items, *draft.no_homework]}
    has_failed = one(db, "SELECT 1 FROM homework_import_images WHERE import_id=:id AND status='failed'", id=import_id)
    status = 'applied' if all_ids and all_ids.issubset(applied) and not has_failed else 'partially_applied'
    run(db, 'UPDATE homework_imports SET applied_ids=CAST(:applied AS jsonb),status=:status,revision=revision+1 WHERE id=:id',
        applied=dump(applied), status=status, id=import_id)
    result = event(db, actor, student, body, digest, 'import_applied', lambda: import_projection(db, import_id),
                   after={'applied_ids': applied})
    audit(db, request, actor, 'homework_import.applied', import_id)
    return data(request, result)


@router.post('/homework-imports/{import_id}/retry')
def retry_import(import_id: UUID, body: s.Retry, request: Request, actor: Principal = Depends(current),
                 db: Session = Depends(get_db, scope='function')):
    row, student = import_record(db, actor, import_id, lock=True)
    receipt, digest = request_receipt(db, actor, student, request, body)
    if receipt is not None:
        return data(request, receipt)
    if row['revision'] != body.expected_revision or row['status'] in ('queued', 'processing', 'cancelled', 'discarded'):
        conflict()
    failures = many(db, "SELECT id,error_message FROM homework_import_images WHERE import_id=:id AND status='failed'", id=import_id)
    if not failures:
        conflict('没有失败图片需要重试')
    selected = {str(identifier) for identifier in body.image_ids} if body.image_ids is not None else {str(image['id']) for image in failures}
    if not selected.issubset({str(image['id']) for image in failures}):
        conflict('只能重试本批导入中失败的图片')
    failures = [image for image in failures if str(image['id']) in selected]
    if not body.confirm_unknown_cost and any('call_outcome_unknown' in (x['error_message'] or '') for x in failures):
        conflict('上次调用结果未知，可能已经计费，请确认后重试')
    if one(db, 'SELECT count(*) AS n FROM homework_import_runs WHERE import_id=:id', id=import_id)['n'] >= 45:
        raise DomainError(429, 'retry_limit_exceeded', '本批识别重试次数已达上限，请手工维护清单')
    for image in failures:
        run(db, "UPDATE homework_import_images SET status='queued',error_message=NULL WHERE import_id=:id AND id=:image AND status='failed'", id=import_id, image=image['id'])
    run(db, "UPDATE homework_imports SET status='queued',revision=revision+1 WHERE id=:id", id=import_id)
    return data(request, event(db, actor, student, body, digest, 'import_retried', lambda: import_projection(db, import_id)))


@router.post('/homework-imports/{import_id}/discard')
def discard_import(import_id: UUID, body: s.Versioned, request: Request, actor: Principal = Depends(current),
                   db: Session = Depends(get_db, scope='function')):
    row, student = import_record(db, actor, import_id, lock=True)
    receipt, digest = request_receipt(db, actor, student, request, body)
    if receipt is not None:
        return data(request, receipt)
    if row['revision'] != body.expected_revision or row['status'] == 'processing':
        conflict('识别处理中请稍后再结束导入')
    run(db, "UPDATE homework_imports SET status='discarded',revision=revision+1 WHERE id=:id", id=import_id)
    run(db, "UPDATE homework_import_images SET status='discarded' WHERE import_id=:id AND status='queued'", id=import_id)
    return data(request, event(db, actor, student, body, digest, 'import_discarded', lambda: import_projection(db, import_id)))


def cancel_imports(db, student_id):
    """Called within consent withdrawal's locked student transaction."""
    run(db, "UPDATE homework_imports SET status='cancelled',draft='{}',revision=revision+1 WHERE student_id=:id", id=student_id)
    run(db, "UPDATE homework_import_images SET status='cancelled' WHERE student_id=:id", id=student_id)
