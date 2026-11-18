"""Guardian-only immutable report snapshots and explicit rebuilds."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from wizzy.db import get_db, run
from wizzy.errors import DomainError, data
from wizzy.pagination import decode_cursor, page
from wizzy.schemas import LearningReport
from wizzy.security import Principal, audit, current, guardian

router = APIRouter(prefix='/api/v1/reports', tags=['learning reports'])


def checked(view):
    error = view.get('error')
    if error == 'invalid_report':
        raise DomainError(422, error, '报告周期或分页参数无效')
    if error == 'report_too_large':
        raise DomainError(422, error, '周期内记录超过10000条，请联系支持人员')
    if error:
        raise DomainError(404, 'not_found', '报告或学生不存在')
    return view


def timestamps(view):
    view['created_at'] = datetime.fromisoformat(view['created_at'])
    if 'period_end' in view:
        view['period_end'] = datetime.fromisoformat(view['period_end'])
    return view


def written(db, request, actor, view, action):
    checked(view)
    if view['created']:
        audit(db, request, actor, action, UUID(view['report']['id']))
    return data(request, timestamps(view['report']))


@router.post('', status_code=201)
def generate(payload: LearningReport, request: Request, actor: Principal = Depends(current),
             db: Session = Depends(get_db, scope='function')):
    guardian(actor)
    view = run(db, 'SELECT public.generate_learning_report(:id,:kind,:starts)',
               id=payload.student_id, kind=payload.type, starts=payload.period_start).scalar_one()
    return written(db, request, actor, view, 'report.generated')


@router.get('')
def listing(student_id: UUID, request: Request, type: Literal['weekly', 'monthly'] | None = None,
            limit: int = Query(20, ge=1, le=100), cursor: str | None = None,
            actor: Principal = Depends(current), db: Session = Depends(get_db, scope='function')):
    guardian(actor)
    after_date, after_id = decode_cursor(cursor)
    view = checked(run(db, 'SELECT public.read_learning_reports(:id,:kind,:limit,:date,:after)',
                       id=student_id, kind=type, limit=limit, date=after_date, after=after_id).scalar_one())
    return data(request, page([timestamps(item) for item in view['items']], limit))


@router.get('/{report_id}')
def detail(report_id: UUID, request: Request, actor: Principal = Depends(current),
           db: Session = Depends(get_db, scope='function')):
    guardian(actor)
    view = checked(run(db, 'SELECT public.read_learning_report(:id)', id=report_id).scalar_one())
    return data(request, timestamps(view))


@router.get('/{report_id}/sources')
def sources(report_id: UUID, request: Request, limit: int = Query(20, ge=1, le=100), cursor: str | None = None,
            actor: Principal = Depends(current), db: Session = Depends(get_db, scope='function')):
    guardian(actor)
    after_date, after_id = decode_cursor(cursor)
    view = checked(run(db, 'SELECT public.read_learning_report_sources(:id,:limit,:date,:after)',
                       id=report_id, limit=limit, date=after_date, after=after_id).scalar_one())
    return data(request, page([timestamps(item) for item in view['items']], limit))


@router.post('/{report_id}/rebuild', status_code=201)
def rebuild(report_id: UUID, request: Request, actor: Principal = Depends(current),
            db: Session = Depends(get_db, scope='function')):
    guardian(actor)
    view = run(db, 'SELECT public.rebuild_learning_report(:id)', id=report_id).scalar_one()
    return written(db, request, actor, view, 'report.rebuilt')
