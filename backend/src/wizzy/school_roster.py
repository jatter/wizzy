"""School enrollment metadata, independent of household identities and access."""
import json
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from sqlalchemy.orm import Session

from wizzy.db import get_db, one
from wizzy.errors import DomainError, data
from wizzy.pagination import decode_cursor, page
from wizzy.schools import SchoolCreate, SchoolWrite
from wizzy.security import digest, unauthorized

router = APIRouter(prefix='/api/v1/admin/schools/{school_id}/roster', tags=['school-roster'])
EnrollmentStatus = Literal['enrolled', 'paused', 'left', 'graduated']


class EnrollmentWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: UUID
    display_name: str = Field(min_length=1, max_length=80)
    status: EnrollmentStatus
    class_id: UUID | None
    reason: str = Field(min_length=1, max_length=500)

    clean_strings = field_validator('*', mode='before')(SchoolWrite.clean_strings.__func__)


class EnrollmentCreate(EnrollmentWrite):
    code: str = Field(pattern=r'^[A-Z0-9][A-Z0-9_-]{1,39}$')
    academic_year: str = Field(pattern=r'^[0-9]{4}-[0-9]{4}$')

    canonical_code = field_validator('code', mode='before')(SchoolCreate.canonical_code.__func__)
    consecutive_years = field_validator('academic_year')(SchoolWrite.consecutive_years.__func__)


class EnrollmentRevision(EnrollmentWrite):
    expected_revision: Annotated[StrictInt, Field(ge=1, le=999999998)]


def perform(request, db, operation, school_id, *, payload=None, enrollment_id=None, academic_year=None,
            query=None, status_filter=None, limit=50, cursor=None):
    scheme, _, token = request.headers.get('authorization', '').partition(' ')
    if scheme.lower() != 'bearer' or not token or len(token) > 128:
        raise unauthorized()
    before, before_id = decode_cursor(cursor)
    result = one(db, '''SELECT public.school_roster_request(:hash,:op,:request,CAST(:payload AS jsonb),
        :school,:enrollment,:year,:search,:status,:limit,:before,:before_id) AS result''', hash=digest(token), op=operation,
        request=request.state.request_id, payload=json.dumps(payload.model_dump(mode='json') if payload else {}),
        school=school_id, enrollment=enrollment_id, year=academic_year, search=query, status=status_filter,
        limit=limit, before=before, before_id=before_id)['result']
    status = result['status']
    if status == 401:
        raise unauthorized()
    if status == 403:
        return JSONResponse(status_code=403, content={
            'error': {'code': 'forbidden', 'message': '缺少学生名册管理权限', 'field_errors': [], 'retryable': False},
            'request_id': request.state.request_id})
    if status not in (200, 201):
        code = result.get('code', {404: 'not_found', 422: 'validation_error'}.get(status, 'conflict'))
        message = {'not_found': '学校或在读记录不存在', 'validation_error': '学生登记字段不符合约定',
                   'operation_conflict': '操作键已用于其他请求', 'enrollment_exists': '本校学生代码在该学年已登记',
                   'enrollment_revision_conflict': '在读记录已被其他人修改，请载入最新版本',
                   'school_disabled': '学校已停用，请先启用学校',
                   'invalid_enrollment_class': '在读学生须选择同校同学年且组织路径已启用的班级'}.get(code, '操作冲突')
        raise DomainError(status, code, message)
    value = result['value']
    rows = value if operation in ('list', 'history') else [value]
    for row in rows:
        for key in ('created_at', 'registered_at'):
            row[key] = datetime.fromisoformat(row[key])
    if operation in ('list', 'history'):
        value = page(value, limit, id_key='revision_id' if operation == 'history' else 'id',
                     timestamp_key='created_at' if operation == 'history' else 'registered_at')
    return JSONResponse(status_code=status, content=data(request, value))


@router.get('')
def listing(school_id: UUID, request: Request, academic_year: str = Query(pattern=r'^[0-9]{4}-[0-9]{4}$'),
            q: str | None = Query(default=None, max_length=80), status: EnrollmentStatus | None = None,
            limit: int = Query(default=50, ge=1, le=100), cursor: str | None = None,
            db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'list', school_id, academic_year=academic_year, query=q,
                   status_filter=status, limit=limit, cursor=cursor)


@router.post('')
def create(school_id: UUID, payload: EnrollmentCreate, request: Request,
           db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'create', school_id, payload=payload)


@router.get('/{enrollment_id}')
def detail(school_id: UUID, enrollment_id: UUID, request: Request, db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'detail', school_id, enrollment_id=enrollment_id)


@router.get('/{enrollment_id}/revisions')
def history(school_id: UUID, enrollment_id: UUID, request: Request, limit: int = Query(default=50, ge=1, le=100),
            cursor: str | None = None, db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'history', school_id, enrollment_id=enrollment_id, limit=limit, cursor=cursor)


@router.post('/{enrollment_id}/revisions')
def revise(school_id: UUID, enrollment_id: UUID, payload: EnrollmentRevision, request: Request,
           db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'revise', school_id, enrollment_id=enrollment_id, payload=payload)
