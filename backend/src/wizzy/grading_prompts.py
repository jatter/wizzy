"""Grading prompts ordered by call priority. Keys and child work never enter this table."""
import json
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator
from sqlalchemy.orm import Session

from wizzy.db import get_db, one
from wizzy.errors import DomainError, data
from wizzy.security import digest, unauthorized

router = APIRouter(prefix='/api/v1/admin/grading-prompts', tags=['grading-prompts'])


class GradingPromptWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=4000)
    priority: StrictInt = Field(ge=1, le=9999)
    enabled: StrictBool

    @field_validator('name', mode='before')
    @classmethod
    def clean_name(cls, value):
        if isinstance(value, str):
            if any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError('不能包含控制字符')
            return value.strip()
        return value

    @field_validator('content', mode='before')
    @classmethod
    def clean_content(cls, value):
        if isinstance(value, str):
            value = value.replace('\r\n', '\n').replace('\r', '\n')
            if any((ord(c) < 32 and c not in '\n\t') or ord(c) == 127 for c in value):
                raise ValueError('不能包含控制字符')
            return value.strip(' \t\n')
        return value


def bearer(request):
    scheme, _, token = request.headers.get('authorization', '').partition(' ')
    if scheme.lower() != 'bearer' or not token or len(token) > 128:
        raise unauthorized()
    return digest(token)


def call(db, request, token_hash, operation, payload=None, prompt_id=None):
    return one(db, '''SELECT public.grading_prompt_request(:hash,:op,:request,CAST(:payload AS jsonb),:id) AS result''',
               hash=token_hash, op=operation, request=request.state.request_id,
               payload=json.dumps(payload or {}), id=prompt_id)['result']


def respond(request, result):
    status = result['status']
    if status == 401:
        raise unauthorized()
    if status == 403:
        return JSONResponse(status_code=403, content={
            'error': {'code': 'forbidden', 'message': '缺少模型管理权限', 'field_errors': [], 'retryable': False},
            'request_id': request.state.request_id})
    if status not in (200, 201):
        messages = {404: ('not_found', '提示词不存在'), 409: ('grading_prompt_conflict', '相同名称的提示词已存在')}
        code, message = messages.get(status, ('validation_error', '提示词不符合约定，请检查字段'))
        raise DomainError(status, code, message)
    return None


def projection(value):
    for key in ('created_at', 'updated_at'):
        if value.get(key):
            value[key] = datetime.fromisoformat(value[key])
    return value


def write_payload(payload: GradingPromptWrite):
    return {'name': payload.name, 'content': payload.content, 'priority': payload.priority, 'enabled': payload.enabled}


@router.get('')
def grading_prompts(request: Request, db: Session = Depends(get_db, scope='function')):
    result = call(db, request, bearer(request), 'list')
    if denied := respond(request, result):
        return denied
    return data(request, {'items': [projection(item) for item in result['value']]})


@router.post('', status_code=201)
def create_grading_prompt(payload: GradingPromptWrite, request: Request,
                          db: Session = Depends(get_db, scope='function')):
    result = call(db, request, bearer(request), 'create', write_payload(payload))
    if denied := respond(request, result):
        return denied
    return data(request, projection(result['value']))


@router.post('/{prompt_id}')
def update_grading_prompt(prompt_id: UUID, payload: GradingPromptWrite, request: Request,
                          db: Session = Depends(get_db, scope='function')):
    result = call(db, request, bearer(request), 'update', write_payload(payload), prompt_id)
    if denied := respond(request, result):
        return denied
    return data(request, projection(result['value']))


@router.post('/{prompt_id}/delete')
def delete_grading_prompt(prompt_id: UUID, request: Request, db: Session = Depends(get_db, scope='function')):
    result = call(db, request, bearer(request), 'delete', prompt_id=prompt_id)
    if denied := respond(request, result):
        return denied
    return data(request, result['value'])
