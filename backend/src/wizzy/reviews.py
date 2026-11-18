"""First-step reviews: drafts and pending evidence, never implicit mastery."""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.orm import Session

from wizzy.db import get_db, run
from wizzy.errors import DomainError, data
from wizzy.media import consent_student
from wizzy.pagination import decode_cursor, page
from wizzy.questions import scoped_question
from wizzy.schemas import ReviewAttempt, ReviewDraft, ReviewEnrollment, ReviewReschedule
from wizzy.security import Principal, audit, current, guardian

router = APIRouter(prefix='/api/v1', tags=['review workbench'])


def checked(view):
    error = view.get('error')
    if error == 'invalid_review':
        raise DomainError(422, error, '复习内容或改期参数无效')
    if error in ('source_conflict', 'revision_conflict', 'task_conflict', 'idempotency_conflict'):
        raise DomainError(409, error, '来源、任务或版本已变化，请刷新后重试')
    if error:
        raise DomainError(404, 'not_found', '复习任务不存在')
    return view


def timestamps(task):
    for item in [task, *task['attempts'], *task['reschedules']]:
        item['created_at'] = datetime.fromisoformat(item['created_at'])
    return task


def scoped_task(db, actor, task_id):
    view = checked(run(db, 'SELECT public.read_review_task(:id)', id=task_id).scalar_one())
    scoped_question(db, actor, UUID(view['question_id']))
    return checked(run(db, 'SELECT public.read_review_task(:id)', id=task_id).scalar_one())


def child(actor):
    if actor.audience != 'child':
        raise DomainError(403, 'forbidden', '复习作答必须由绑定儿童提交')


def written(db, request, actor, view, action):
    checked(view)
    if view['created']:
        audit(db, request, actor, action, UUID(view['task']['id']))
    return data(request, timestamps(view['task']))


@router.post('/questions/{question_id}/review-tasks', status_code=201)
def enroll(question_id: UUID, payload: ReviewEnrollment, request: Request,
           actor: Principal = Depends(current), db: Session = Depends(get_db, scope='function')):
    guardian(actor)
    scoped_question(db, actor, question_id)
    view = run(db, 'SELECT public.create_review_task(:id,:version)', id=question_id,
               version=payload.expected_effective_revision).scalar_one()
    return written(db, request, actor, view, 'review.enrolled')


@router.get('/students/{student_id}/review-tasks')
def tasks(student_id: UUID, request: Request, limit: int = Query(20, ge=1, le=100),
          cursor: str | None = None, due_only: bool = False, actor: Principal = Depends(current),
          db: Session = Depends(get_db, scope='function')):
    after_date, after_id = decode_cursor(cursor)
    consent_student(db, actor, student_id)
    rows = run(db, 'SELECT public.read_student_review_tasks(:id,:limit,:date,:after,:due)',
               id=student_id, limit=limit, date=after_date, after=after_id, due=due_only).scalar_one()
    return data(request, page([timestamps(item) for item in rows], limit))


@router.get('/review-tasks/{task_id}')
def task(task_id: UUID, request: Request, actor: Principal = Depends(current),
         db: Session = Depends(get_db, scope='function')):
    return data(request, timestamps(scoped_task(db, actor, task_id)))


@router.post('/review-tasks/{task_id}/draft')
@router.patch('/review-tasks/{task_id}/draft')
def draft(task_id: UUID, payload: ReviewDraft, request: Request,
          actor: Principal = Depends(current), db: Session = Depends(get_db, scope='function')):
    child(actor)
    scoped_task(db, actor, task_id)
    view = run(db, 'SELECT public.save_review_draft(:id,:version,:answer)', id=task_id,
               version=payload.expected_draft_revision, answer=payload.student_answer).scalar_one()
    return written(db, request, actor, view, 'review.draft_saved')


@router.post('/review-tasks/{task_id}/attempts', status_code=201)
def attempt(task_id: UUID, payload: ReviewAttempt, request: Request,
            idempotency_key: UUID = Header(), actor: Principal = Depends(current),
            db: Session = Depends(get_db, scope='function')):
    child(actor)
    scoped_task(db, actor, task_id)
    view = run(db, 'SELECT public.submit_review_attempt(:id,:version,:outcome,:key)',
               id=task_id, version=payload.expected_draft_revision, outcome=payload.outcome,
               key=idempotency_key).scalar_one()
    return written(db, request, actor, view, 'review.attempted')


@router.post('/review-tasks/{task_id}/reschedules', status_code=201)
def reschedule(task_id: UUID, payload: ReviewReschedule, request: Request,
               idempotency_key: UUID = Header(), actor: Principal = Depends(current),
               db: Session = Depends(get_db, scope='function')):
    guardian(actor)
    scoped_task(db, actor, task_id)
    view = run(db, 'SELECT public.reschedule_review_task(:id,:version,:date,:reason,:key)',
               id=task_id, version=payload.expected_schedule_revision, date=payload.due_date,
               reason=payload.reason, key=idempotency_key).scalar_one()
    return written(db, request, actor, view, 'review.rescheduled')
