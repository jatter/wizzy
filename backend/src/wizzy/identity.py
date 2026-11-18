import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from wizzy import schemas
from wizzy.db import get_db, many, one, run, set_context
from wizzy.errors import DomainError, data
from wizzy.pagination import decode_cursor, page
from wizzy.security import (
    DUMMY_HASH,
    FAMILY_COOKIE,
    Principal,
    audit,
    current,
    digest,
    guardian,
    issue,
    limited,
    passwords,
    session_response,
    student_record,
    unauthorized,
)

router = APIRouter(prefix="/api/v1")


@router.post("/auth/register", status_code=201)
def register(payload: schemas.Register, request: Request, db: Session = Depends(get_db, scope="function")):
    limited(request, "register", maximum=30)
    user_id = uuid4()
    run(db, """INSERT INTO users(id,email,password_hash,privacy_version) VALUES(:id,:email,:hash,:version)""",
        id=user_id, email=payload.email, hash=passwords.hash(payload.password), version=payload.privacy_version)
    return session_response(request, issue(db, request.app.state.settings, user_id=user_id) | {"user_id": user_id}, 201)


@router.post("/auth/login")
def login(payload: schemas.Login, request: Request, db: Session = Depends(get_db, scope="function")):
    email = payload.email.strip().lower()
    limited(request, "login", email)
    limited(request, "login-ip", maximum=100)
    user = one(db, "SELECT * FROM users WHERE email=:email", email=email)
    valid = passwords.verify(payload.password, user["password_hash"] if user else DUMMY_HASH)
    if not valid or not user or user["status"] != "active":
        raise unauthorized()
    return session_response(request, issue(db, request.app.state.settings, user_id=user["id"]) | {"user_id": user["id"]})


@router.get("/auth/me")
def me(request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    profile = {"audience": actor.audience, "tenant_id": actor.tenant_id, "student_id": actor.student_id, "role": actor.role}
    if actor.user_id:
        profile |= dict(one(db, "SELECT id AS user_id,email FROM users WHERE id=:id", id=actor.user_id))
        profile['username'] = profile['email']
    return data(request, profile)


@router.post("/auth/logout")
def logout(request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    if actor.audience == 'child':
        run(db, '''DELETE FROM learning_devices
            WHERE session_id=(SELECT COALESCE(source_session_id,id) FROM auth_sessions WHERE id=:id)''', id=actor.session_id)
    run(db, "UPDATE auth_sessions SET revoked_at=now() WHERE id=:id", id=actor.session_id)
    response = JSONResponse(data(request, {"revoked": True}))
    if request.headers.get("X-Family-Session") == "browser":
        response.delete_cookie(FAMILY_COOKIE, path="/api/v1", httponly=True, samesite="strict",
                               secure=request.url.scheme == "https")
    return response


@router.post("/auth/start-learning")
def start_learning(payload: schemas.StartLearning, request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    guardian(actor)
    student = student_record(db, actor, payload.student_id, lock=True)
    if not student["consent_active"]:
        raise DomainError(403, "consent_required", "请先完成儿童信息处理同意")
    session = issue(db, request.app.state.settings, tenant_id=actor.tenant_id,
                    student_id=payload.student_id, device_name="家庭共用设备")
    run(db, "UPDATE auth_sessions SET revoked_at=now() WHERE id=:id", id=actor.session_id)
    audit(db, request, actor, "learning.started", payload.student_id)
    return session_response(request, session)


@router.post("/tenants", status_code=201)
def create_tenant(payload: schemas.TenantCreate, request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    guardian(actor, tenant=False)
    tenant_id = uuid4()
    set_context(db, tenant_id, user_id=actor.user_id)
    tenant = dict(one(db, "INSERT INTO tenants(id,name) VALUES(:id,:name) RETURNING id,name", id=tenant_id, name=payload.name))
    run(db, "INSERT INTO memberships(id,tenant_id,user_id,role) VALUES(:id,:tenant,:user,'owner')", id=uuid4(), tenant=tenant_id, user=actor.user_id)
    return session_response(request, {"tenant": tenant} | issue(db, request.app.state.settings, user_id=actor.user_id, tenant_id=tenant_id), 201)


@router.get("/tenants")
def list_tenants(request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    guardian(actor, tenant=False)
    memberships = many(db, "SELECT tenant_id,role FROM memberships WHERE user_id=:user AND status='active' ORDER BY created_at,id LIMIT 100", user=actor.user_id)
    # Only switch context after a trusted membership lookup, never on an arbitrary request header.
    result = []
    for member in memberships:
        set_context(db, member["tenant_id"], user_id=actor.user_id)
        tenant = one(db, "SELECT id,name FROM tenants WHERE id=:id AND status='active'", id=member["tenant_id"])
        if tenant:
            result.append(dict(tenant) | {"role": member["role"]})
    return data(request, {"items": result})


@router.post("/auth/activate-tenant")
def activate(payload: schemas.ActivateTenant, request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    guardian(actor, tenant=False)
    member = one(db, "SELECT role FROM memberships WHERE tenant_id=:tenant AND user_id=:user AND status='active'", tenant=payload.tenant_id, user=actor.user_id)
    if not member:
        raise DomainError(404, "not_found", "家庭不存在")
    set_context(db, payload.tenant_id, user_id=actor.user_id)
    if not one(db, "SELECT id FROM tenants WHERE id=:id AND status='active'", id=payload.tenant_id):
        raise DomainError(404, "not_found", "家庭不存在")
    return session_response(request, issue(db, request.app.state.settings, user_id=actor.user_id, tenant_id=payload.tenant_id))


@router.post("/students", status_code=201)
def create_student(payload: schemas.StudentCreate, request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    guardian(actor, owner=True)
    row = one(db, """INSERT INTO students(id,tenant_id,nickname,grade) VALUES(:id,:tenant,:name,:grade)
        RETURNING id,nickname,grade,revision,consent_active,created_at""", id=uuid4(), tenant=actor.tenant_id, name=payload.nickname, grade=payload.grade)
    audit(db, request, actor, "student.created", row["id"])
    return data(request, dict(row))


@router.get("/students")
def list_students(request: Request, limit: int = Query(20, ge=1, le=100), cursor: str | None = None, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    if not actor.tenant_id:
        raise DomainError(403, "tenant_required", "请先选择家庭")
    date, identifier = decode_cursor(cursor)
    rows = many(db, """SELECT id,nickname,grade,revision,consent_active,created_at FROM students s
        WHERE status='active' AND (CAST(:date AS timestamptz) IS NULL OR (created_at,id)>(:date,:identifier)) AND (:all OR EXISTS(SELECT 1 FROM student_access a
            WHERE a.student_id=s.id AND a.user_id=:user)) ORDER BY created_at,id LIMIT :limit""",
        all=actor.role == "owner" or actor.audience == "child", user=actor.user_id,
        date=date, identifier=identifier, limit=limit+1)
    return data(request, page(rows, limit))


@router.get("/students/{student_id}")
def get_student(student_id: UUID, request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    row = student_record(db, actor, student_id)
    return data(request, {key: row[key] for key in ("id", "nickname", "grade", "revision", "consent_active", "created_at")})


@router.post("/students/{student_id}/profile")
@router.patch("/students/{student_id}")
def patch_student(student_id: UUID, payload: schemas.StudentPatch, request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    guardian(actor, owner=True)
    student_record(db, actor, student_id)
    row = one(db, """UPDATE students SET nickname=coalesce(:name,nickname),grade=coalesce(:grade,grade),revision=revision+1
        WHERE id=:id AND revision=:revision RETURNING id,nickname,grade,revision,consent_active""", id=student_id, name=payload.nickname, grade=payload.grade, revision=payload.expected_revision)
    if not row:
        raise DomainError(409, "revision_conflict", "学生资料已变更，请刷新后重试")
    audit(db, request, actor, "student.updated", student_id)
    return data(request, dict(row))


@router.post("/consents", status_code=201)
def consent(payload: schemas.Consent, request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    guardian(actor)
    student_record(db, actor, payload.student_id, lock=True)
    row = one(db, """INSERT INTO consent_records(id,tenant_id,student_id,guardian_id,policy_version,child_rules_version,granted)
        VALUES(:id,:tenant,:student,:user,:policy,:rules,:granted) RETURNING id,granted,created_at""", id=uuid4(), tenant=actor.tenant_id,
        student=payload.student_id, user=actor.user_id, policy=payload.policy_version, rules=payload.child_rules_version, granted=payload.granted)
    run(db, """UPDATE students SET consent_active=:granted,
        deletion_epoch=deletion_epoch+CASE WHEN :granted THEN 0 ELSE 1 END WHERE id=:id""", granted=payload.granted, id=payload.student_id)
    if not payload.granted:
        from wizzy.daily_homework import cancel_imports
        from wizzy.submissions import cancel_student_jobs

        cancel_imports(db, payload.student_id)
        cancel_student_jobs(db, payload.student_id)
        run(db, "UPDATE auth_sessions SET revoked_at=now(),device_revoked_at=now() WHERE tenant_id=:tenant AND student_id=:student", tenant=actor.tenant_id, student=payload.student_id)
        run(db, "UPDATE pairing_codes SET consumed_at=now() WHERE tenant_id=:tenant AND student_id=:student AND consumed_at IS NULL", tenant=actor.tenant_id, student=payload.student_id)
    audit(db, request, actor, "consent.granted" if payload.granted else "consent.revoked", payload.student_id)
    return data(request, dict(row))


@router.post("/students/{student_id}/pairing-codes", status_code=201)
def pairing(student_id: UUID, request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    guardian(actor)
    student = student_record(db, actor, student_id, lock=True)
    if not student["consent_active"]:
        raise DomainError(403, "consent_required", "请先完成儿童信息处理同意")
    limited(request, "pair-create", str(actor.user_id), maximum=30)
    code = secrets.token_urlsafe(24)
    run(db, """INSERT INTO pairing_codes(id,code_hash,tenant_id,student_id,guardian_id,expires_at)
        VALUES(:id,:hash,:tenant,:student,:user,:expires)""", id=uuid4(), hash=digest(code), tenant=actor.tenant_id,
        student=student_id, user=actor.user_id, expires=datetime.now(UTC) + timedelta(minutes=5))
    audit(db, request, actor, "pairing.created", student_id)
    return data(request, {"code": code, "expires_in_seconds": 300})


@router.post("/child-sessions", status_code=201)
def child_session(payload: schemas.ChildSession, request: Request, db: Session = Depends(get_db, scope="function")):
    limited(request, "pair-consume", maximum=30)
    pair = one(db, "SELECT * FROM pairing_codes WHERE code_hash=:hash AND consumed_at IS NULL AND expires_at>now()", hash=digest(payload.code))
    if not pair:
        raise unauthorized()
    set_context(db, pair["tenant_id"], user_id=pair["guardian_id"])
    # Order locks like consent/removal: tenant -> membership -> student -> pairing code.
    if not one(db, "SELECT id FROM tenants WHERE id=:id AND status='active' FOR SHARE", id=pair["tenant_id"]):
        raise unauthorized()
    member = one(db, "SELECT role FROM memberships WHERE tenant_id=:tenant AND user_id=:user AND status='active' FOR SHARE", tenant=pair["tenant_id"], user=pair["guardian_id"])
    if not member or not one(db, "SELECT id FROM users WHERE id=:id AND status='active'", id=pair["guardian_id"]):
        raise unauthorized()
    if member["role"] != "owner" and not one(db, "SELECT 1 FROM student_access WHERE student_id=:student AND user_id=:user", student=pair["student_id"], user=pair["guardian_id"]):
        raise unauthorized()
    if not one(db, "SELECT id FROM students WHERE id=:id AND consent_active AND status='active' FOR SHARE", id=pair["student_id"]):
        raise unauthorized()
    consumed = one(db, "UPDATE pairing_codes SET consumed_at=now() WHERE id=:id AND consumed_at IS NULL AND expires_at>now() RETURNING id", id=pair["id"])
    if not consumed:
        raise unauthorized()
    return session_response(request, issue(db, request.app.state.settings, tenant_id=pair["tenant_id"], student_id=pair["student_id"], device_name=payload.device_name), 201)


@router.get("/child-sessions")
def list_child_sessions(request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    guardian(actor)
    rows = many(db, """SELECT a.id,a.student_id,a.device_name,a.created_at,a.expires_at FROM auth_sessions a
        JOIN students s ON s.id=a.student_id WHERE a.tenant_id=:tenant AND a.audience='child'
        AND a.source_session_id IS NULL AND a.device_revoked_at IS NULL
        AND ((a.revoked_at IS NULL AND a.expires_at>now()) OR EXISTS(SELECT 1 FROM learning_devices d WHERE d.session_id=a.id AND d.expires_at>now())
            OR EXISTS(SELECT 1 FROM learning_passkeys p WHERE p.session_id=a.id AND p.revoked_at IS NULL)) AND (:owner OR EXISTS(
            SELECT 1 FROM student_access x WHERE x.student_id=s.id AND x.user_id=:user)) ORDER BY a.created_at,a.id LIMIT 100""",
        tenant=actor.tenant_id, owner=actor.role == "owner", user=actor.user_id)
    return data(request, {"items": rows})


@router.delete("/child-sessions/{session_id}")
def revoke_child(session_id: UUID, request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    guardian(actor)
    row = one(db, "SELECT student_id FROM auth_sessions WHERE id=:id AND tenant_id=:tenant AND audience='child'", id=session_id, tenant=actor.tenant_id)
    if not row:
        raise DomainError(404, "not_found", "设备不存在")
    student_record(db, actor, row["student_id"])
    run(db, '''UPDATE auth_sessions SET revoked_at=now(),device_revoked_at=now()
        WHERE id=(SELECT COALESCE(source_session_id,id) FROM auth_sessions WHERE id=:id)''', id=session_id)
    audit(db, request, actor, "device.revoked", session_id)
    return data(request, {"revoked": True})


@router.post("/guardian-invitations", status_code=201)
def invite(payload: schemas.Invitation, request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    guardian(actor, owner=True)
    for student_id in set(payload.student_ids):
        student_record(db, actor, student_id)
    code = secrets.token_urlsafe(32)
    run(db, """INSERT INTO guardian_invitations(id,code_hash,tenant_id,email,student_ids,invited_by,expires_at)
        VALUES(:id,:hash,:tenant,:email,:students,:user,:expires)""", id=uuid4(), hash=digest(code), tenant=actor.tenant_id,
        email=payload.email.strip().lower(), students=list(set(payload.student_ids)), user=actor.user_id, expires=datetime.now(UTC) + timedelta(days=1))
    audit(db, request, actor, "guardian.invited")
    return data(request, {"code": code, "expires_in_seconds": 86400})


@router.post("/guardian-invitations/accept")
def accept_invite(payload: schemas.AcceptInvitation, request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    guardian(actor, tenant=False)
    email = one(db, "SELECT email FROM users WHERE id=:id", id=actor.user_id)["email"]
    invite = one(db, "SELECT * FROM guardian_invitations WHERE code_hash=:hash AND email=:email AND consumed_at IS NULL AND expires_at>now()", hash=digest(payload.code), email=email)
    if not invite:
        raise DomainError(404, "not_found", "邀请不存在或已失效")
    set_context(db, invite["tenant_id"], user_id=actor.user_id)
    if not one(db, "SELECT id FROM tenants WHERE id=:id AND status='active' FOR SHARE", id=invite["tenant_id"]):
        raise DomainError(404, "not_found", "邀请不存在或已失效")
    inviter = one(db, "SELECT id FROM memberships WHERE tenant_id=:tenant AND user_id=:user AND role='owner' AND status='active' FOR SHARE", tenant=invite["tenant_id"], user=invite["invited_by"])
    # current() may hold a SHARE lock on an active membership. Reject before the
    # advisory lock so removal cannot wait on our row lock while we wait on it.
    if not inviter or one(db, "SELECT id FROM memberships WHERE tenant_id=:tenant AND user_id=:user AND status='active'", tenant=invite["tenant_id"], user=actor.user_id):
        raise DomainError(409, "membership_conflict", "无法使用此邀请")
    run(db, "SELECT pg_advisory_xact_lock(hashtextextended(:key,0))", key=f"member:{invite['tenant_id']}:{actor.user_id}")
    membership = one(db, "SELECT status FROM memberships WHERE tenant_id=:tenant AND user_id=:user FOR UPDATE", tenant=invite["tenant_id"], user=actor.user_id)
    if membership and membership["status"] == "active":
        raise DomainError(409, "membership_conflict", "已加入此家庭")
    consumed = one(db, "UPDATE guardian_invitations SET consumed_at=now() WHERE id=:id AND consumed_at IS NULL AND expires_at>now() RETURNING id", id=invite["id"])
    if not consumed:
        raise DomainError(404, "not_found", "邀请不存在或已失效")
    run(db, """INSERT INTO memberships(id,tenant_id,user_id,role) VALUES(:id,:tenant,:user,'guardian')
        ON CONFLICT(tenant_id,user_id) DO UPDATE SET status='active'""", id=uuid4(), tenant=invite["tenant_id"], user=actor.user_id)
    for student_id in invite["student_ids"]:
        if not one(db, "SELECT id FROM students WHERE id=:id AND status='active'", id=student_id):
            raise DomainError(409, "invitation_stale", "学生状态已变化，请重新邀请")
        run(db, "INSERT INTO student_access(tenant_id,student_id,user_id) VALUES(:tenant,:student,:user) ON CONFLICT DO NOTHING", tenant=invite["tenant_id"], student=student_id, user=actor.user_id)
    return session_response(request, issue(db, request.app.state.settings, tenant_id=invite["tenant_id"], user_id=actor.user_id))


@router.get("/tenants/members")
def members(request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    guardian(actor, owner=True)
    return data(request, {"items": many(db, "SELECT user_id,role FROM memberships WHERE tenant_id=:tenant AND status='active' ORDER BY created_at,id LIMIT 100", tenant=actor.tenant_id)})


@router.delete("/tenants/members/{user_id}")
def remove_member(user_id: UUID, request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    guardian(actor, owner=True)
    if user_id == actor.user_id:
        raise DomainError(409, "last_owner", "不能移除家庭管理员")
    run(db, "SELECT pg_advisory_xact_lock(hashtextextended(:key,0))", key=f"member:{actor.tenant_id}:{user_id}")
    member = one(db, "SELECT role FROM memberships WHERE tenant_id=:tenant AND user_id=:user AND status='active' FOR UPDATE", tenant=actor.tenant_id, user=user_id)
    if not member:
        raise DomainError(404, "not_found", "成员不存在")
    if member["role"] == "owner":
        raise DomainError(409, "last_owner", "不能移除家庭管理员")
    run(db, "UPDATE memberships SET status='revoked' WHERE tenant_id=:tenant AND user_id=:user", tenant=actor.tenant_id, user=user_id)
    run(db, "DELETE FROM student_access WHERE tenant_id=:tenant AND user_id=:user", tenant=actor.tenant_id, user=user_id)
    run(db, "UPDATE auth_sessions SET revoked_at=now() WHERE tenant_id=:tenant AND user_id=:user AND revoked_at IS NULL", tenant=actor.tenant_id, user=user_id)
    run(db, "UPDATE pairing_codes SET consumed_at=now() WHERE tenant_id=:tenant AND guardian_id=:user AND consumed_at IS NULL", tenant=actor.tenant_id, user=user_id)
    run(db, """UPDATE guardian_invitations SET consumed_at=now() WHERE tenant_id=:tenant
        AND email=(SELECT email FROM users WHERE id=:user) AND consumed_at IS NULL""", tenant=actor.tenant_id, user=user_id)
    audit(db, request, actor, "guardian.removed", user_id)
    return data(request, {"revoked": True})
