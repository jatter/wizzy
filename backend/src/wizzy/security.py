"""Opaque sessions and authorization. Tenant context comes only from trusted records."""
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from pwdlib import PasswordHash
from sqlalchemy import text
from sqlalchemy.orm import Session

from wizzy.db import get_db, one, run, set_context
from wizzy.errors import DomainError, data

passwords = PasswordHash.recommended()
DUMMY_HASH = passwords.hash(secrets.token_urlsafe(32))
FAMILY_COOKIE = "wizzy_family_session"


def browser_request(request: Request):
    origin = request.headers.get("origin")
    allowed = {str(request.base_url).rstrip("/")}
    if public_origin := request.app.state.settings.family_public_origin:
        allowed.add(public_origin.rstrip("/"))
    if request.headers.get("X-Family-Session") != "browser" or (origin and origin not in allowed):
        raise DomainError(403, "forbidden", "请从小知页面发起操作")


def session_response(request: Request, value: dict, status_code: int = 200):
    if request.headers.get("X-Family-Session") != "browser":
        return JSONResponse(data(request, value), status_code=status_code)
    browser_request(request)
    public = {key: item for key, item in value.items() if key != "access_token"}
    response = JSONResponse(data(request, public | {"token_type": "cookie"}), status_code=status_code)
    response.set_cookie(FAMILY_COOKIE, value["access_token"], httponly=True, samesite="strict",
                        secure=request.url.scheme == "https", path="/api/v1",
                        max_age=request.app.state.settings.session_hours * 3600)
    return response


def digest(value: str):
    return hashlib.sha256(value.encode()).hexdigest()


def limited(request: Request, category: str, subject: str = "", maximum: int = 10):
    # A separate committed transaction preserves failed-attempt counters on request rollback.
    client = request.client.host if request.client else "unknown"
    bucket = digest(f"{category}:{client}:{subject}")
    with request.app.state.engine.begin() as connection:
        attempts = connection.execute(text("""
            INSERT INTO rate_limits(bucket,attempts,window_start) VALUES(:bucket,1,now())
            ON CONFLICT(bucket) DO UPDATE SET
                attempts=CASE WHEN rate_limits.window_start < now()-interval '5 minutes'
                    THEN 1 ELSE rate_limits.attempts+1 END,
                window_start=CASE WHEN rate_limits.window_start < now()-interval '5 minutes'
                    THEN now() ELSE rate_limits.window_start END
            RETURNING attempts
        """), {"bucket": bucket}).scalar_one()
    if attempts > maximum:
        raise DomainError(429, "rate_limited", "请求过于频繁，请稍后重试", retryable=True)


def issue(db, settings, *, user_id=None, tenant_id=None, student_id=None, device_name=None, source_session_id=None):
    token, session_id = secrets.token_urlsafe(32), uuid4()
    audience = "child" if student_id else "guardian"
    run(db, """INSERT INTO auth_sessions(id,token_hash,user_id,tenant_id,student_id,
        audience,device_name,expires_at,source_session_id) VALUES(:id,:hash,:user,:tenant,:student,:audience,:device,:expires,:source)""",
        id=session_id, hash=digest(token), user=user_id, tenant=tenant_id, student=student_id,
        audience=audience, device=device_name, source=source_session_id,
        expires=datetime.now(UTC) + timedelta(hours=settings.session_hours))
    return {"access_token": token, "token_type": "bearer", "session_id": session_id}


@dataclass
class Principal:
    session_id: UUID
    user_id: UUID | None
    tenant_id: UUID | None
    student_id: UUID | None
    audience: str
    role: str | None = None


def unauthorized():
    return DomainError(401, "authentication_required", "请登录或重新配对设备")


def family_token(request: Request):
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if not authorization:
        token = request.cookies.get(FAMILY_COOKIE, "")
        scheme = "bearer"
        if token:
            browser_request(request)
    if scheme.lower() != "bearer" or not token or len(token) > 128:
        raise unauthorized()
    return token


def current(request: Request, db: Session = Depends(get_db, scope="function")):
    token = family_token(request)
    session = one(db, """SELECT * FROM auth_sessions WHERE token_hash=:hash
        AND revoked_at IS NULL AND expires_at>now()""", hash=digest(token))
    if not session:
        raise unauthorized()
    if session.get('source_session_id') and not one(db, """SELECT id FROM auth_sessions
            WHERE id=:id AND device_revoked_at IS NULL""", id=session['source_session_id']):
        raise unauthorized()
    actor = Principal(session["id"], session["user_id"], session["tenant_id"],
                      session["student_id"], session["audience"])
    set_context(db, actor.tenant_id, actor.student_id, actor.user_id)
    if actor.user_id and not one(db, "SELECT id FROM users WHERE id=:id AND status='active'", id=actor.user_id):
        raise unauthorized()
    if actor.tenant_id:
        if not one(db, "SELECT id FROM tenants WHERE id=:id AND status='active' FOR SHARE", id=actor.tenant_id):
            raise unauthorized()
        if actor.audience == "guardian":
            member = one(db, """SELECT role FROM memberships WHERE tenant_id=:tenant
                AND user_id=:user AND status='active' FOR SHARE""", tenant=actor.tenant_id, user=actor.user_id)
            if not member:
                raise unauthorized()
            actor.role = member["role"]
        elif not one(db, """SELECT id FROM students WHERE id=:id AND status='active'
                AND consent_active FOR SHARE""", id=actor.student_id):
            raise unauthorized()
    return actor


def guardian(actor: Principal, *, owner=False, tenant=True):
    if actor.audience != "guardian" or (owner and actor.role != "owner"):
        raise DomainError(403, "forbidden", "此操作需要监护人或家庭管理员权限")
    if tenant and not actor.tenant_id:
        raise DomainError(403, "tenant_required", "请先创建或选择家庭")


def student_record(db, actor: Principal, student_id: UUID, *, lock=False):
    row = one(db, "SELECT * FROM students WHERE id=:id AND status='active'" + (" FOR UPDATE" if lock else ""), id=student_id)
    allowed = row and (actor.audience == "child" and actor.student_id == student_id
        or actor.role == "owner" or actor.audience == "guardian" and one(db,
            "SELECT 1 FROM student_access WHERE student_id=:id AND user_id=:user", id=student_id, user=actor.user_id))
    if not allowed:
        raise DomainError(404, "not_found", "学生不存在")
    return dict(row)


def audit(db, request, actor, action, resource_id=None):
    run(db, """INSERT INTO audit_events(id,tenant_id,actor_id,action,resource_id,request_id)
        VALUES(:id,:tenant,:actor,:action,:resource,:request)""", id=uuid4(), tenant=actor.tenant_id,
        actor=actor.user_id, action=action, resource=resource_id, request=request.state.request_id)
