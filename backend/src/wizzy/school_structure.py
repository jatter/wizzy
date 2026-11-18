"""School organization metadata, without implicit student or household access."""
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

router = APIRouter(prefix='/api/v1/admin/schools/{school_id}/structure', tags=['school-structure'])


class StructureWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: UUID
    name: str = Field(min_length=1, max_length=120)
    status: Literal['active', 'disabled']
    parent_id: UUID | None
    reason: str = Field(min_length=1, max_length=500)

    clean_strings = field_validator('*', mode='before')(SchoolWrite.clean_strings.__func__)


class StructureCreate(StructureWrite):
    code: str = Field(pattern=r'^[A-Z0-9][A-Z0-9_-]{1,39}$')
    kind: Literal['campus', 'department', 'grade', 'class']
    academic_year: str = Field(pattern=r'^[0-9]{4}-[0-9]{4}$')

    canonical_code = field_validator('code', mode='before')(SchoolCreate.canonical_code.__func__)
    consecutive_years = field_validator('academic_year')(SchoolWrite.consecutive_years.__func__)


class StructureRevision(StructureWrite):
    expected_revision: Annotated[StrictInt, Field(ge=1, le=999999998)]


def perform(request, db, operation, school_id, *, payload=None, node_id=None, academic_year=None, limit=50, cursor=None):
    scheme, _, token = request.headers.get('authorization', '').partition(' ')
    if scheme.lower() != 'bearer' or not token or len(token) > 128:
        raise unauthorized()
    before, before_id = decode_cursor(cursor)
    result = one(db, '''SELECT public.school_structure_request(:hash,:op,:request,CAST(:payload AS jsonb),
        :school,:node,:year,:limit,:before,:before_id) AS result''', hash=digest(token), op=operation,
        request=request.state.request_id, payload=json.dumps(payload.model_dump(mode='json') if payload else {}),
        school=school_id, node=node_id, year=academic_year, limit=limit, before=before, before_id=before_id)['result']
    status = result['status']
    if status == 401:
        raise unauthorized()
    if status == 403:
        return JSONResponse(status_code=403, content={
            'error': {'code': 'forbidden', 'message': '缺少学校管理权限', 'field_errors': [], 'retryable': False},
            'request_id': request.state.request_id})
    if status not in (200, 201):
        code = result.get('code', {404: 'not_found', 422: 'validation_error'}.get(status, 'conflict'))
        message = {'not_found': '学校或组织节点不存在', 'validation_error': '组织字段不符合约定',
                   'operation_conflict': '操作键已用于其他请求', 'structure_code_conflict': '本学年组织代码已存在',
                   'structure_revision_conflict': '组织已被其他人修改，请载入最新版本',
                   'school_disabled': '学校已停用，请先启用学校', 'structure_capacity': '本学年组织节点已达上限',
                   'invalid_structure_parent': '上级组织的学校、学年或类型不符合要求',
                   'structure_cycle': '不能移动到自身或下级组织', 'structure_depth': '组织层级不能超过八层',
                   }.get(code, '操作冲突')
        raise DomainError(status, code, message)
    value = result['value']
    rows = value['items'] if operation == 'tree' else value if operation == 'history' else [value]
    for row in rows:
        for key in ('created_at', 'registered_at'):
            row[key] = datetime.fromisoformat(row[key])
    if operation == 'history':
        value = page(value, limit, id_key='revision_id')
    return JSONResponse(status_code=status, content=data(request, value))


@router.get('')
def tree(school_id: UUID, request: Request,
         academic_year: str = Query(pattern=r'^[0-9]{4}-[0-9]{4}$'),
         db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'tree', school_id, academic_year=academic_year)


@router.post('')
def create(school_id: UUID, payload: StructureCreate, request: Request,
           db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'create', school_id, payload=payload)


@router.get('/{node_id}')
def detail(school_id: UUID, node_id: UUID, request: Request, db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'detail', school_id, node_id=node_id)


@router.get('/{node_id}/revisions')
def history(school_id: UUID, node_id: UUID, request: Request, limit: int = Query(default=50, ge=1, le=100),
            cursor: str | None = None, db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'history', school_id, node_id=node_id, limit=limit, cursor=cursor)


@router.post('/{node_id}/revisions')
def revise(school_id: UUID, node_id: UUID, payload: StructureRevision, request: Request,
           db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'revise', school_id, payload=payload, node_id=node_id)
