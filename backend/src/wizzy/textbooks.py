"""Textbook rights approval does not publish or expose textbook content."""
import json
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from sqlalchemy.orm import Session

from wizzy.db import get_db, one
from wizzy.errors import DomainError, data
from wizzy.pagination import decode_cursor, page
from wizzy.security import digest, unauthorized

router = APIRouter(prefix='/api/v1/admin/textbook-editions', tags=['textbook-rights'])
State = Literal['draft', 'pending_review', 'approved', 'withdrawn']


class CleanModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    @field_validator('*', mode='before')
    @classmethod
    def clean_strings(cls, value):
        if isinstance(value, str):
            if any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError('不能包含控制字符')
            return value.strip()
        return value


class Rights(CleanModel):
    basis: Literal['publisher_contract', 'open_license', 'public_domain']
    holder: str = Field(min_length=1, max_length=200)
    reference: str = Field(min_length=1, max_length=200)
    scopes: list[Literal['storage', 'ocr', 'retrieval', 'preview']] = Field(min_length=1, max_length=4)
    regions: list[Annotated[str, Field(pattern=r'^[A-Z]{2}(-[A-Z0-9]{1,8})?$')]] = Field(min_length=1, max_length=20)
    valid_from: date
    valid_until: date

    @field_validator('valid_from', 'valid_until', mode='before')
    @classmethod
    def strict_dates(cls, value):
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError('日期需为 YYYY-MM-DD')
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError('日期需为 YYYY-MM-DD')
        return value

    @model_validator(mode='after')
    def complete_rights(self):
        if self.valid_from > self.valid_until:
            raise ValueError('授权起始日期不能晚于截止日期')
        if 'storage' not in self.scopes or len(set(self.scopes)) != len(self.scopes):
            raise ValueError('用途需包含存储且不能重复')
        if len(set(self.regions)) != len(self.regions):
            raise ValueError('授权地区不能重复')
        return self


class TextbookWrite(CleanModel):
    operation_id: UUID
    title: str = Field(min_length=1, max_length=120)
    stage: Literal['primary', 'junior', 'senior']
    grade: Annotated[StrictInt, Field(ge=1, le=12)]
    subject: str = Field(min_length=1, max_length=120)
    publisher: str = Field(min_length=1, max_length=120)
    edition_label: str = Field(min_length=1, max_length=120)
    academic_year: str = Field(pattern=r'^[0-9]{4}-[0-9]{4}$')
    volume: Literal['first', 'second', 'full']
    rights: Rights | None
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode='after')
    def metadata_consistency(self):
        first, last = map(int, self.academic_year.split('-'))
        if not 2000 <= first <= 9998 or last != first + 1:
            raise ValueError('学年需为相邻年份')
        low, high = {'primary': (1, 6), 'junior': (7, 9), 'senior': (10, 12)}[self.stage]
        if not low <= self.grade <= high:
            raise ValueError('年级与学段不匹配')
        return self


class TextbookCreate(TextbookWrite):
    code: str = Field(pattern=r'^[A-Z0-9][A-Z0-9_-]{1,39}$')

    @field_validator('code', mode='before')
    @classmethod
    def canonical_code(cls, value):
        if isinstance(value, str):
            if any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError('不能包含控制字符')
            return value.strip().upper()
        return value


class TextbookRevision(TextbookWrite):
    expected_revision: Annotated[StrictInt, Field(ge=1, le=999999998)]


class TextbookAction(CleanModel):
    operation_id: UUID
    expected_revision: Annotated[StrictInt, Field(ge=1, le=999999998)]
    reason: str = Field(min_length=1, max_length=500)


def perform(request, db, operation, *, payload=None, edition_id=None, limit=50, cursor=None, q='', state=None):
    scheme, _, token = request.headers.get('authorization', '').partition(' ')
    if scheme.lower() != 'bearer' or not token or len(token) > 128:
        raise unauthorized()
    before, before_id = decode_cursor(cursor)
    result = one(db, '''SELECT public.textbook_request(:hash,:op,:request,CAST(:payload AS jsonb),
        :edition,:limit,:before,:before_id,:q,:state) AS result''', hash=digest(token), op=operation,
        request=request.state.request_id, payload=json.dumps(payload.model_dump(mode='json') if payload else {}),
        edition=edition_id, limit=limit, before=before, before_id=before_id, q=q, state=state)['result']
    status = result['status']
    if status == 401:
        raise unauthorized()
    if status == 403:
        return JSONResponse(status_code=403, content={
            'error': {'code': 'forbidden', 'message': '缺少教材管理权限', 'field_errors': [], 'retryable': False},
            'request_id': request.state.request_id})
    if status not in (200, 201):
        code = result.get('code', {404: 'not_found', 422: 'validation_error'}.get(status, 'conflict'))
        message = {'not_found': '教材版本不存在', 'validation_error': '教材字段不符合约定',
                   'operation_conflict': '操作键已用于其他请求', 'textbook_code_conflict': '教材代码已存在',
                   'textbook_revision_conflict': '教材已被其他人修改，请载入最新版本',
                   'textbook_state_conflict': '当前状态不允许此操作',
                   'independent_reviewer_required': '创建者、当前编辑者与提交人不能审核此版本',
                   'textbook_rights_incomplete': '请补齐有效授权后再提交或批准'}.get(code, '操作冲突')
        raise DomainError(status, code, message)
    value = result['value']
    for row in value if operation in ('list', 'history') else [value]:
        row['created_at'] = datetime.fromisoformat(row['created_at'])
        row['registered_at'] = datetime.fromisoformat(row['registered_at'])
    if operation in ('list', 'history'):
        value = page(value, limit, id_key='revision_id' if operation == 'history' else 'id',
                     timestamp_key='created_at' if operation == 'history' else 'registered_at')
    return JSONResponse(status_code=status, content=data(request, value))


@router.get('')
def textbooks(request: Request, limit: int = Query(default=50, ge=1, le=100), cursor: str | None = None,
              q: str = Query(default='', max_length=120), state: State | None = None,
              db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'list', limit=limit, cursor=cursor, q=q.strip(), state=state)


@router.post('')
def create_textbook(payload: TextbookCreate, request: Request, db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'create', payload=payload)


@router.get('/{edition_id}')
def textbook(edition_id: UUID, request: Request, db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'detail', edition_id=edition_id)


@router.get('/{edition_id}/revisions')
def history(edition_id: UUID, request: Request, limit: int = Query(default=50, ge=1, le=100),
            cursor: str | None = None, db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'history', edition_id=edition_id, limit=limit, cursor=cursor)


@router.post('/{edition_id}/revisions')
def revise_textbook(edition_id: UUID, payload: TextbookRevision, request: Request,
                   db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'revise', edition_id=edition_id, payload=payload)


@router.post('/{edition_id}/submit')
def submit(edition_id: UUID, payload: TextbookAction, request: Request, db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'submit', edition_id=edition_id, payload=payload)


@router.post('/{edition_id}/approve')
def approve(edition_id: UUID, payload: TextbookAction, request: Request, db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'approve', edition_id=edition_id, payload=payload)


@router.post('/{edition_id}/reject')
def reject(edition_id: UUID, payload: TextbookAction, request: Request, db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'reject', edition_id=edition_id, payload=payload)


@router.post('/{edition_id}/withdraw')
def withdraw(edition_id: UUID, payload: TextbookAction, request: Request, db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'withdraw', edition_id=edition_id, payload=payload)
