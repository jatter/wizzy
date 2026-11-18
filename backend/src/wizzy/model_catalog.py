"""Platform-only metadata. Registry configuration is not grading evidence."""
import json
import re
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator
from sqlalchemy.orm import Session

from wizzy.db import get_db, one
from wizzy.errors import DomainError, data
from wizzy.pagination import decode_cursor, page
from wizzy.security import digest, unauthorized

router = APIRouter(prefix='/api/v1/admin/models', tags=['model-catalog'])
Price = Annotated[StrictInt, Field(ge=0, le=9007199254740991)]


class ModelMetadata(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: UUID
    display_name: str = Field(min_length=1, max_length=120)
    purposes: list[Literal['ocr', 'structure', 'verify', 'tutor', 'report']] = Field(min_length=1, max_length=5)
    secret_ref: str | None = Field(default=None, max_length=240)
    contract_status: Literal['pending', 'approved', 'rejected', 'expired']
    contract_reference: str | None = Field(default=None, min_length=1, max_length=200)
    contract_expires_on: date | None = None
    input_price_micros: Price | None = None
    output_price_micros: Price | None = None
    enabled: StrictBool = False
    reason: str = Field(min_length=1, max_length=500)

    @field_validator('*', mode='before')
    @classmethod
    def clean_strings(cls, value):
        if isinstance(value, str):
            if any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError('不能包含控制字符')
            return value.strip()
        return value

    @field_validator('purposes')
    @classmethod
    def unique_purposes(cls, value):
        if len(set(value)) != len(value):
            raise ValueError('用途不得重复')
        return value

    @field_validator('secret_ref')
    @classmethod
    def reference_only(cls, value):
        if value is not None and not re.fullmatch(r'secret://providers/[a-z0-9][a-z0-9_-]*/[a-zA-Z0-9][a-zA-Z0-9_/-]*', value):
            raise ValueError('仅接受密钥服务引用，不接受明文密钥或网址')
        return value


class ModelCreate(ModelMetadata):
    provider: str = Field(pattern=r'^[a-z0-9][a-z0-9._-]{0,79}$')
    model_key: str = Field(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,119}$')
    fixed_version: str = Field(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,119}$')
    region: str = Field(pattern=r'^[a-z0-9][a-z0-9._-]{0,79}$')

    @field_validator('fixed_version')
    @classmethod
    def fixed_only(cls, value):
        if re.search(r'(^|[._/-])(latest|auto|default|stable|current|preview)([._/-]|$)', value.lower()):
            raise ValueError('必须登记固定版本，不得使用浮动别名')
        return value


class ModelRevision(ModelMetadata):
    expected_revision: Annotated[StrictInt, Field(ge=1, le=999999999)]


def perform(request, db, operation, *, payload=None, model_id=None, limit=50, cursor=None, q='', enabled=None):
    scheme, _, token = request.headers.get('authorization', '').partition(' ')
    if scheme.lower() != 'bearer' or not token or len(token) > 128:
        raise unauthorized()
    before, before_id = decode_cursor(cursor)
    body = payload.model_dump(mode='json') if payload else {}
    if isinstance(payload, ModelRevision) and 'secret_ref' not in payload.model_fields_set:
        del body['secret_ref']  # Omitted keeps the private reference; explicit null clears it.
    result = one(db, '''SELECT public.model_catalog_request(:hash,:op,:request,CAST(:payload AS jsonb),
        :model,:limit,:before,:before_id,:q,:enabled) AS result''', hash=digest(token), op=operation,
        request=request.state.request_id, payload=json.dumps(body), model=model_id, limit=limit,
        before=before, before_id=before_id, q=q, enabled=enabled)['result']
    status = result['status']
    if status == 401:
        raise unauthorized()
    if status == 403:
        # Preserve the SQL access.denied audit by committing this response.
        return JSONResponse(status_code=403, content={
            'error': {'code': 'forbidden', 'message': '缺少模型管理权限', 'field_errors': [], 'retryable': False},
            'request_id': request.state.request_id})
    if status not in (200, 201):
        messages = {404: ('not_found', '模型不存在'), 409: ('conflict', '操作键、模型身份或版本冲突，请刷新后重试')}
        code, message = messages.get(status, ('validation_error', '模型元数据不符合约定，请检查合同、密钥引用和字段'))
        raise DomainError(status, code, message)
    value = result['value']
    # SQL JSON trims trailing microsecond zeros; normalize every projection alike.
    for row in value if operation in ('list', 'history') else [value]:
        row['created_at'] = datetime.fromisoformat(row['created_at'])
        row['registered_at'] = datetime.fromisoformat(row['registered_at'])
    if operation in ('list', 'history'):
        value = page(value, limit, id_key='revision_id' if operation == 'history' else 'id',
                     timestamp_key='created_at' if operation == 'history' else 'registered_at')
    return data(request, value)


@router.get('')
def models(request: Request, limit: int = Query(default=50, ge=1, le=100), cursor: str | None = None,
           q: str = Query(default='', max_length=120), enabled: bool | None = None,
           db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'list', limit=limit, cursor=cursor, q=q.strip(), enabled=enabled)


@router.post('', status_code=201)
def create_model(payload: ModelCreate, request: Request,
                 db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'create', payload=payload)


@router.get('/{model_id}')
def model(model_id: UUID, request: Request, db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'detail', model_id=model_id)


@router.post('/{model_id}/revisions', status_code=201)
def revise_model(model_id: UUID, payload: ModelRevision, request: Request,
                 db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'revise', model_id=model_id, payload=payload)


@router.get('/{model_id}/revisions')
def history(model_id: UUID, request: Request, limit: int = Query(default=50, ge=1, le=100),
            cursor: str | None = None, db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'history', model_id=model_id, limit=limit, cursor=cursor)
