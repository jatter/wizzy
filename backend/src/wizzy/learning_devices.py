"""Child-only device credentials. Raw keys are held by native secure storage."""
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from wizzy.db import get_db, one, run, set_context
from wizzy.errors import DomainError, data
from wizzy.security import (
    Principal,
    current,
    digest,
    issue,
    limited,
    session_response,
    unauthorized,
)

router = APIRouter(prefix='/api/v1/auth/learning-device', tags=['learning-device'])


class DeviceResume(BaseModel):
    device_token: str = Field(min_length=16, max_length=128)


def require_secure_login(request: Request):
    settings = request.app.state.settings
    if settings.environment != 'test' and request.url.scheme != 'https':
        raise DomainError(503, 'secure_connection_required', '请通过 HTTPS 安全连接使用免密登录')


def child_root(db: Session, actor: Principal):
    if actor.audience != 'child':
        raise DomainError(403, 'forbidden', '快捷登录仅用于孩子的学习空间')
    row = one(db, 'SELECT COALESCE(source_session_id,id) AS id FROM auth_sessions WHERE id=:id', id=actor.session_id)
    return row['id']


def active_learning_device(db: Session, session_id: UUID):
    root = one(db, "SELECT * FROM auth_sessions WHERE id=:id AND audience='child' AND device_revoked_at IS NULL", id=session_id)
    if not root:
        raise unauthorized()
    set_context(db, root['tenant_id'], root['student_id'])
    # Same lock order as consent withdrawal; also blocks cross-tenant device use.
    if not one(db, "SELECT id FROM tenants WHERE id=:id AND status='active' FOR SHARE", id=root['tenant_id']):
        raise unauthorized()
    if not one(db, "SELECT id FROM students WHERE id=:id AND status='active' AND consent_active FOR SHARE", id=root['student_id']):
        raise unauthorized()
    if not one(db, 'SELECT id FROM auth_sessions WHERE id=:id AND device_revoked_at IS NULL FOR SHARE', id=session_id):
        raise unauthorized()
    return root


@router.post('/register', status_code=201)
def register_device(request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope='function')):
    require_secure_login(request)
    session_id = child_root(db, actor)
    limited(request, 'device-register', str(session_id))
    active_learning_device(db, session_id)
    key = secrets.token_urlsafe(48)
    expires = datetime.now(UTC) + timedelta(days=request.app.state.settings.learning_device_days)
    run(db, '''INSERT INTO learning_devices(session_id,token_hash,expires_at) VALUES(:id,:hash,:expires)
        ON CONFLICT(session_id) DO UPDATE SET token_hash=excluded.token_hash,expires_at=excluded.expires_at''',
        id=session_id, hash=digest(key), expires=expires)
    return data(request, {'device_token': key, 'expires_at': expires})


@router.post('/resume')
def resume_device(payload: DeviceResume, request: Request, db: Session = Depends(get_db, scope='function')):
    require_secure_login(request)
    limited(request, 'device-resume', maximum=60)
    device = one(db, 'SELECT * FROM learning_devices WHERE token_hash=:hash AND expires_at>now()', hash=digest(payload.device_token))
    if not device:
        raise unauthorized()
    root = active_learning_device(db, device['session_id'])
    # A new short-lived session keeps the long-lived key out of the WebView cookie.
    value = issue(db, request.app.state.settings, tenant_id=root['tenant_id'], student_id=root['student_id'],
                  device_name=root['device_name'], source_session_id=root['id'])
    run(db, 'UPDATE learning_devices SET last_used_at=now() WHERE session_id=:id', id=root['id'])
    return session_response(request, value)


@router.post('/forget')
def forget_device(payload: DeviceResume, request: Request, db: Session = Depends(get_db, scope='function')):
    require_secure_login(request)
    limited(request, 'device-forget', maximum=30)
    run(db, 'DELETE FROM learning_devices WHERE token_hash=:hash', hash=digest(payload.device_token))
    return data(request, {'revoked': True})
