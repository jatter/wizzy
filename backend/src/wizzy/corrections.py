"""Manual correction history; neither automatic grading nor mastery evidence."""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.orm import Session

from wizzy.db import get_db, run
from wizzy.errors import DomainError, data
from wizzy.pagination import decode_cursor, page
from wizzy.questions import scoped_question
from wizzy.schemas import CorrectionAttempt, CorrectionJudgment
from wizzy.security import Principal, audit, current, guardian

router = APIRouter(prefix='/api/v1/questions', tags=['correction attempts'])


def checked(view):
    error = view.get('error')
    if error == 'invalid_correction':
        raise DomainError(422, error, '订正或核对内容无效')
    if error in ('source_conflict', 'revision_conflict', 'idempotency_conflict'):
        raise DomainError(409, error, '原题、核对版本或操作键已变化，请刷新并核对请求')
    if error:
        raise DomainError(404, 'not_found', '题目、订正或可用图片不存在')
    return view


def written(db, request, actor, view, action):
    checked(view)
    if view['created']:
        audit(db, request, actor, action, UUID(view['attempt']['id']))
    view['attempt']['created_at'] = datetime.fromisoformat(view['attempt']['created_at'])
    return data(request, view['attempt'])


@router.post('/{question_id}/correction-attempts', status_code=201)
def submit_attempt(question_id: UUID, payload: CorrectionAttempt, request: Request,
                   idempotency_key: UUID = Header(), actor: Principal = Depends(current),
                   db: Session = Depends(get_db, scope='function')):
    scoped_question(db, actor, question_id)
    view = run(db, 'SELECT public.submit_correction_attempt(:id,:version,:answer,:media,:key)',
               id=question_id, version=payload.expected_effective_revision,
               answer=payload.student_answer, media=payload.media_id, key=idempotency_key).scalar_one()
    return written(db, request, actor, view, 'correction.submitted')


@router.get('/{question_id}/correction-attempts')
def attempts(question_id: UUID, request: Request, limit: int = Query(20, ge=1, le=100),
             cursor: str | None = None, actor: Principal = Depends(current),
             db: Session = Depends(get_db, scope='function')):
    after_date, after_id = decode_cursor(cursor)
    scoped_question(db, actor, question_id)
    view = checked(run(db, 'SELECT public.read_correction_attempts(:id,:limit,:date,:after)',
                       id=question_id, limit=limit, date=after_date, after=after_id).scalar_one())
    for item in view['items']:
        item['created_at'] = datetime.fromisoformat(item['created_at'])
    return data(request, page(view['items'], limit))


@router.post('/{question_id}/correction-attempts/{attempt_id}/judgments', status_code=201)
def judge_attempt(question_id: UUID, attempt_id: UUID, payload: CorrectionJudgment, request: Request,
                  idempotency_key: UUID = Header(), actor: Principal = Depends(current),
                  db: Session = Depends(get_db, scope='function')):
    guardian(actor)
    scoped_question(db, actor, question_id)
    view = run(db, 'SELECT public.judge_correction_attempt(:id,:attempt,:version,:verdict,:reason,:key)',
               id=question_id, attempt=attempt_id, version=payload.expected_judgment_revision,
               verdict=payload.verdict, reason=payload.reason, key=idempotency_key).scalar_one()
    return written(db, request, actor, view, 'correction.judged')
