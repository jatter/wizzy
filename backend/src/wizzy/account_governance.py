"""Account governance through an audited, database-authorized capability."""
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

router = APIRouter(prefix='/api/v1/admin', tags=['account-governance'])


class AccountDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: UUID
    reason: str = Field(min_length=1, max_length=500)

    @field_validator('reason', mode='before')
    @classmethod
    def clean_reason(cls, value):
        if isinstance(value, str):
            if any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError('不能包含控制字符')
            return value.strip()
        return value


class AccountChange(AccountDecision):
    expected_revision: Annotated[StrictInt, Field(ge=0, le=999999998)]
    role: Literal['operator', 'security_admin', 'evaluation_annotator', 'evaluation_adjudicator']
    status: Literal['active', 'disabled']


CONFLICTS = {
    'operation_conflict': '操作编号已用于其他请求，请核对历史记录',
    'revision_conflict': '账号已被修改，请刷新后重新核对',
    'already_decided': '申请已有审批结果，请刷新历史记录',
    'separate_approver_required': '申请人不能审批自己的申请',
    'proposer_ineligible': '申请人已失去权限，无法批准此申请',
    'self_session_revoke': '本人请使用退出全部会话功能',
    'last_security_admin': '必须保留至少一个启用的安全管理员',
}


def perform(request, db, operation, identifier, *, payload=None, limit=50, cursor=None, sessions=False):
    scheme, _, token = request.headers.get('authorization', '').partition(' ')
    if scheme.lower() != 'bearer' or not token or len(token) > 128:
        raise unauthorized()
    before, before_id = decode_cursor(cursor)
    capability = 'platform_account_session_revocations' if sessions else 'platform_account_governance'
    result = one(db, f'''SELECT public.{capability}(:hash,:op,:request,:id,
        CAST(:payload AS jsonb),:limit,:before,:before_id) AS result''',
        hash=digest(token), op=operation, request=request.state.request_id, id=identifier,
        payload=json.dumps(payload.model_dump(mode='json') if payload else {}),
        limit=limit, before=before, before_id=before_id)['result']
    status = result['status']
    if status == 401:
        raise unauthorized()
    if status == 403:
        # Returning rather than raising commits the access.denied audit event.
        return JSONResponse(status_code=403, content={
            'error': {'code': 'forbidden', 'message': '缺少账号治理权限', 'field_errors': [], 'retryable': False},
            'request_id': request.state.request_id})
    if status == 409:
        code = result.get('code', 'conflict')
        raise DomainError(409, code, CONFLICTS.get(code, '账号状态冲突，请刷新后重试'))
    if status not in (200, 201):
        raise DomainError(status, 'not_found' if status == 404 else 'validation_error',
                          '账号或申请不存在' if status == 404 else '账号变更字段无效或没有发生变更')
    value = result['value']
    for row in value if operation == 'history' else [value]:
        row['created_at'] = datetime.fromisoformat(row['created_at'])
        if row.get('decided_at'):
            row['decided_at'] = datetime.fromisoformat(row['decided_at'])
    if operation == 'history':
        value = page(value, limit)
    return JSONResponse(status_code=status, content=data(request, value))


@router.get('/accounts/{account_id}')
def account(account_id: UUID, request: Request, db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'detail', account_id)


@router.get('/accounts/{account_id}/changes')
def history(account_id: UUID, request: Request, limit: int = Query(default=50, ge=1, le=100),
            cursor: str | None = None, db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'history', account_id, limit=limit, cursor=cursor)


@router.post('/accounts/{account_id}/changes', status_code=201)
def change(account_id: UUID, payload: AccountChange, request: Request,
           db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'change', account_id, payload=payload)


@router.post('/account-changes/{change_id}/approve')
def approve(change_id: UUID, payload: AccountDecision, request: Request,
            db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'approve', change_id, payload=payload)


@router.post('/account-changes/{change_id}/reject')
def reject(change_id: UUID, payload: AccountDecision, request: Request,
           db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'reject', change_id, payload=payload)


@router.get('/accounts/{account_id}/session-revocations')
def session_history(account_id: UUID, request: Request, limit: int = Query(default=50, ge=1, le=100),
                    cursor: str | None = None, db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'history', account_id, limit=limit, cursor=cursor, sessions=True)


@router.post('/accounts/{account_id}/session-revocations', status_code=201)
def revoke_sessions(account_id: UUID, payload: AccountDecision, request: Request,
                    db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'revoke', account_id, payload=payload, sessions=True)
