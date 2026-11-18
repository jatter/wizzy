"""School metadata is not an organization or household access grant."""
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
from wizzy.security import digest, unauthorized

router = APIRouter(prefix='/api/v1/admin/schools', tags=['school-directory'])


class SchoolWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: UUID
    name: str = Field(min_length=1, max_length=120)
    region: str = Field(min_length=1, max_length=120)
    academic_year: str = Field(pattern=r'^[0-9]{4}-[0-9]{4}$')
    status: Literal['active', 'disabled']
    reason: str = Field(min_length=1, max_length=500)

    @field_validator('*', mode='before')
    @classmethod
    def clean_strings(cls, value):
        if isinstance(value, str):
            if any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError('不能包含控制字符')
            return value.strip()
        return value

    @field_validator('academic_year')
    @classmethod
    def consecutive_years(cls, value):
        first, last = map(int, value.split('-'))
        if not 2000 <= first <= 9998 or last != first + 1:
            raise ValueError('学年需为相邻年份，例如 2026-2027')
        return value


class SchoolCreate(SchoolWrite):
    code: str = Field(pattern=r'^[A-Z0-9][A-Z0-9_-]{1,39}$')

    @field_validator('code', mode='before')
    @classmethod
    def canonical_code(cls, value):
        if isinstance(value, str):
            if any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError('不能包含控制字符')
            return value.strip().upper()
        return value


class SchoolRevision(SchoolWrite):
    expected_revision: Annotated[StrictInt, Field(ge=1, le=999999998)]


def perform(request, db, operation, *, payload=None, school_id=None, limit=50, cursor=None, q='', status=None):
    scheme, _, token = request.headers.get('authorization', '').partition(' ')
    if scheme.lower() != 'bearer' or not token or len(token) > 128:
        raise unauthorized()
    before, before_id = decode_cursor(cursor)
    result = one(db, '''SELECT public.school_directory_request(:hash,:op,:request,CAST(:payload AS jsonb),
        :school,:limit,:before,:before_id,:q,:status) AS result''', hash=digest(token), op=operation,
        request=request.state.request_id, payload=json.dumps(payload.model_dump(mode='json') if payload else {}),
        school=school_id, limit=limit, before=before, before_id=before_id, q=q, status=status)['result']
    http_status = result['status']
    if http_status == 401:
        raise unauthorized()
    if http_status == 403:
        return JSONResponse(status_code=403, content={
            'error': {'code': 'forbidden', 'message': '缺少学校管理权限', 'field_errors': [], 'retryable': False},
            'request_id': request.state.request_id})
    if http_status not in (200, 201):
        code = result.get('code', {404: 'not_found', 422: 'validation_error'}.get(http_status, 'conflict'))
        message = {'not_found': '学校不存在', 'validation_error': '学校字段不符合约定',
                   'operation_conflict': '操作键已用于其他请求', 'school_code_conflict': '学校代码已存在',
                   'school_revision_conflict': '学校已被其他人修改，请载入最新版本'}.get(code, '操作冲突')
        raise DomainError(http_status, code, message)
    value = result['value']
    for row in value if operation in ('list', 'history') else [value]:
        row['created_at'] = datetime.fromisoformat(row['created_at'])
        row['registered_at'] = datetime.fromisoformat(row['registered_at'])
    if operation in ('list', 'history'):
        value = page(value, limit, id_key='revision_id' if operation == 'history' else 'id',
                     timestamp_key='created_at' if operation == 'history' else 'registered_at')
    return JSONResponse(status_code=http_status, content=data(request, value))


@router.get('')
def schools(request: Request, limit: int = Query(default=50, ge=1, le=100), cursor: str | None = None,
            q: str = Query(default='', max_length=120), status: Literal['active', 'disabled'] | None = None,
            db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'list', limit=limit, cursor=cursor, q=q.strip(), status=status)


@router.post('')
def create_school(payload: SchoolCreate, request: Request, db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'create', payload=payload)


@router.get('/{school_id}')
def school(school_id: UUID, request: Request, db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'detail', school_id=school_id)


@router.get('/{school_id}/revisions')
def history(school_id: UUID, request: Request, limit: int = Query(default=50, ge=1, le=100),
            cursor: str | None = None, db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'history', school_id=school_id, limit=limit, cursor=cursor)


@router.post('/{school_id}/revisions')
def revise_school(school_id: UUID, payload: SchoolRevision, request: Request,
                  db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'revise', school_id=school_id, payload=payload)
