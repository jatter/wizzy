"""Finite shared-task plans, with live unverified progress and explicit estimates."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.orm import Session

from wizzy.db import get_db, run
from wizzy.errors import DomainError, data
from wizzy.pagination import decode_cursor, page
from wizzy.reviews import timestamps as task_timestamps
from wizzy.schemas import ReviewPlan
from wizzy.security import Principal, audit, current, guardian

router = APIRouter(prefix='/api/v1/review-plans', tags=['review plans'])


def checked(view):
    error = view.get('error')
    if error == 'invalid_plan':
        raise DomainError(422, error, '复习周期、题量或时长无效')
    if error in ('idempotency_conflict', 'source_conflict'):
        raise DomainError(409, error, '请求参数或报告来源已变化，请刷新后重试')
    if error:
        raise DomainError(404, 'not_found', '计划、报告或学生不存在')
    return view


def timestamps(plan):
    plan['created_at'] = datetime.fromisoformat(plan['created_at'])
    for entry in plan['entries']:
        if entry['task'] is not None:
            task_timestamps(entry['task'])
    return plan


@router.post('', status_code=201)
def generate(payload: ReviewPlan, request: Request, idempotency_key: UUID = Header(),
             actor: Principal = Depends(current), db: Session = Depends(get_db, scope='function')):
    guardian(actor)
    view = checked(run(db, 'SELECT public.generate_review_plan(:id,:kind,:starts,:questions,:minutes,:each,:report,:key)',
                       id=payload.student_id, kind=payload.type, starts=payload.period_start,
                       questions=payload.max_questions, minutes=payload.max_minutes,
                       each=payload.minutes_per_question, report=payload.report_id, key=idempotency_key).scalar_one())
    if view['created']:
        audit(db, request, actor, 'review_plan.generated', UUID(view['plan']['id']))
    return data(request, timestamps(view['plan']))


@router.get('')
def listing(student_id: UUID, request: Request, type: Literal['weekly', 'monthly'] | None = None,
            limit: int = Query(20, ge=1, le=100), cursor: str | None = None,
            actor: Principal = Depends(current), db: Session = Depends(get_db, scope='function')):
    after_date, after_id = decode_cursor(cursor)
    view = checked(run(db, 'SELECT public.read_review_plans(:id,:kind,:limit,:date,:after)',
                       id=student_id, kind=type, limit=limit, date=after_date, after=after_id).scalar_one())
    for item in view['items']:
        item['created_at'] = datetime.fromisoformat(item['created_at'])
    return data(request, page(view['items'], limit))


@router.get('/{plan_id}')
def detail(plan_id: UUID, request: Request, actor: Principal = Depends(current),
           db: Session = Depends(get_db, scope='function')):
    view = checked(run(db, 'SELECT public.read_review_plan(:id)', id=plan_id).scalar_one())
    return data(request, timestamps(view))
