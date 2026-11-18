from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text


def test_health_openapi_and_error_envelope(client):
    assert client.get("/health/live").json()["status"] == "ok"
    assert client.get("/health/ready").status_code == 200
    assert "/api/v1/child-sessions" in client.get("/openapi.json").json()["paths"]
    response = client.get("/api/v1/students")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    assert client.get("/not-an-endpoint").json()["error"]["code"] == "not_found"


def test_registration_login_and_privacy(client):
    email = f"{uuid4().hex}@example.test"
    payload = {"email": email, "password": "Test-only-strong-passphrase",
               "privacy_version": "2026-10-25", "accepted_privacy": False}
    assert client.post("/api/v1/auth/register", json=payload).status_code == 422
    payload["accepted_privacy"] = True
    assert client.post("/api/v1/auth/register", json=payload).status_code == 201
    assert client.post("/api/v1/auth/register", json=payload).status_code == 409
    assert client.post("/api/v1/auth/login", json={"email": email, "password": "wrong"}).status_code == 401
    result = client.post("/api/v1/auth/login", json={"email": email, "password": payload["password"]})
    assert result.status_code == 200
    headers = {"Authorization": f"Bearer {result.json()['data']['access_token']}"}
    assert client.get("/api/v1/auth/me", headers=headers).json()["data"]["email"] == email
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_pinyin_username_registration_and_login_with_email_compatibility(client):
    username = 'liyuhan' + uuid4().hex[:8]
    payload = {'username': '  ' + username.upper() + '  ', 'password': '123456789',
               'privacy_version': '2026-10-25', 'accepted_privacy': True}
    assert client.post('/api/v1/auth/register', json=payload).status_code == 201
    assert client.post('/api/v1/auth/register', json=payload | {'username': username}).status_code == 409
    for field in ('username', 'email'):
        result = client.post('/api/v1/auth/login', json={field: username.upper(), 'password': payload['password']})
        assert result.status_code == 200
        headers = {'Authorization': 'Bearer ' + result.json()['data']['access_token']}
        assert client.get('/api/v1/auth/me', headers=headers).json()['data']['username'] == username
    assert client.post('/api/v1/auth/login', json={'username': username, 'password': 'wrong'}).status_code == 401


@pytest.mark.parametrize('username,password', [('ab', '123456789'), ('li yu han', '123456789'),
    ('li/yuhan', '123456789'), ('123li', '123456789'), ('a' * 41, '123456789'), ('liyuhan', '1234567')])
def test_username_and_registration_password_validation(client, username, password):
    result = client.post('/api/v1/auth/register', json={'username': username, 'password': password,
        'privacy_version': '2026-10-25', 'accepted_privacy': True})
    assert result.status_code == 422


@pytest.mark.parametrize("method,suffix", [("patch", ""), ("post", "/profile")])
def test_family_isolation_student_cas_and_no_untrusted_tenant(client, owner, student, admin_engine, method, suffix):
    headers = owner["headers"]
    path = f"/api/v1/students/{student['id']}{suffix}"
    result = client.request(method, path, headers=headers,
                          json={"expected_revision": 1, "nickname": "小知改名", "grade": 4})
    assert result.status_code == 200
    assert result.json()["data"]["revision"] == 2
    assert client.request(method, path, headers=headers,
                        json={"expected_revision": 1, "nickname": "旧值"}).status_code == 409
    second = client.post("/api/v1/tenants", headers=headers, json={"name": "另一个家庭"}).json()["data"]
    other = {"Authorization": f"Bearer {second['access_token']}"}
    assert client.get("/api/v1/students", headers=other).json()["data"]["items"] == []
    assert client.get(f"/api/v1/students/{student['id']}", headers=other).status_code == 404
    assert client.request(method, path, headers=other, json={"expected_revision": 2, "nickname": "越权"}).status_code == 404
    assert client.request(method, path, json={"expected_revision": 2, "nickname": "未登录"}).status_code == 401
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM audit_events WHERE resource_id=:id AND action='student.updated'"), {"id": student["id"]}).scalar_one() == 1
    assert client.post("/api/v1/students", headers=other,
                       json={"nickname": "伪造", "grade": 3, "tenant_id": owner["tenant_id"]}).status_code == 422
    activated = client.post("/api/v1/auth/activate-tenant", headers=other,
                            json={"tenant_id": owner["tenant_id"]})
    assert activated.status_code == 200
    assert client.post("/api/v1/auth/activate-tenant", headers=other,
                       json={"tenant_id": str(uuid4())}).status_code == 404


def test_pairing_single_use_sibling_bound_and_revocable(client, owner, student):
    guardian = owner["headers"]
    sibling = client.post("/api/v1/students", headers=guardian,
                          json={"nickname": "妹妹", "grade": 1}).json()["data"]
    pair = client.post(f"/api/v1/students/{student['id']}/pairing-codes", headers=guardian)
    assert pair.status_code == 201
    code = pair.json()["data"]["code"]
    assert pair.json()["data"]["expires_in_seconds"] == 300
    session = client.post("/api/v1/child-sessions", json={"code": code, "device_name": "学习平板"})
    assert session.status_code == 201
    assert client.post("/api/v1/child-sessions", json={"code": code, "device_name": "重复设备"}).status_code == 401
    child = {"Authorization": f"Bearer {session.json()['data']['access_token']}"}
    students = client.get("/api/v1/students", headers=child).json()["data"]["items"]
    assert [value["id"] for value in students] == [student["id"]]
    assert client.get(f"/api/v1/students/{sibling['id']}", headers=child).status_code == 404
    assert client.post("/api/v1/tenants", headers=child, json={"name": "儿童创建"}).status_code == 403
    assert client.post(f"/api/v1/students/{student['id']}/pairing-codes", headers=child).status_code == 403
    assert client.delete(f"/api/v1/child-sessions/{session.json()['data']['session_id']}", headers=guardian).status_code == 200
    assert client.get("/api/v1/students", headers=child).status_code == 401


def test_expired_pairing_and_consent_revocation(client, owner, student, admin_engine):
    route = f"/api/v1/students/{student['id']}/pairing-codes"
    code = client.post(route, headers=owner["headers"]).json()["data"]["code"]
    with admin_engine.begin() as db:
        db.execute(text("UPDATE pairing_codes SET expires_at=:date WHERE student_id=:id"),
                   {"date": datetime.now(UTC) - timedelta(seconds=1), "id": student["id"]})
    assert client.post("/api/v1/child-sessions", json={"code": code, "device_name": "过期设备"}).status_code == 401
    code = client.post(route, headers=owner["headers"]).json()["data"]["code"]
    child = client.post("/api/v1/child-sessions", json={"code": code, "device_name": "授权设备"}).json()["data"]
    assert client.post("/api/v1/consents", headers=owner["headers"], json={
        "student_id": student["id"], "policy_version": "2026-10-25",
        "child_rules_version": "2026-10-25", "granted": False,
    }).status_code == 201
    assert client.get("/api/v1/students", headers={"Authorization": f"Bearer {child['access_token']}"}).status_code == 401
    assert client.post(route, headers=owner["headers"]).status_code == 403


def test_pairing_consumption_is_atomic(client, owner, student):
    code = client.post(f"/api/v1/students/{student['id']}/pairing-codes", headers=owner["headers"]).json()["data"]["code"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post("/api/v1/child-sessions", json={"code": code, "device_name": "竞态设备"}), range(2)))
    assert sorted(value.status_code for value in responses) == [201, 401]


def test_rls_and_non_owner_runtime_role(app, owner, student):
    with app.state.engine.begin() as db:
        role = db.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user")).one()
        assert role == (False, False)
        assert db.execute(text("SELECT count(*) FROM students")).scalar() == 0
        db.execute(text("SELECT set_config('app.tenant_id', :id, true)"), {"id": owner["tenant_id"]})
        assert db.execute(text("SELECT id FROM students WHERE id=:id"), {"id": student["id"]}).scalar()
        assert db.execute(text("SELECT tableowner=current_user FROM pg_tables WHERE tablename='students'")).scalar() is False
    with app.state.engine.begin() as db:
        assert db.execute(text("SELECT count(*) FROM students")).scalar() == 0


def test_guardian_invitation_student_scope_and_removal(client, owner, student):
    email = f"{uuid4().hex}@example.test"
    registered = client.post("/api/v1/auth/register", json={
        "email": email, "password": "Second-guardian-strong-passphrase",
        "privacy_version": "2026-10-25", "accepted_privacy": True,
    }).json()["data"]
    unbound = {"Authorization": f"Bearer {registered['access_token']}"}
    invite = client.post("/api/v1/guardian-invitations", headers=owner["headers"], json={
        "email": email, "student_ids": [student["id"]],
    })
    assert invite.status_code == 201, invite.text
    accepted = client.post("/api/v1/guardian-invitations/accept", headers=unbound,
                           json={"code": invite.json()["data"]["code"]})
    assert accepted.status_code == 200, accepted.text
    guardian = {"Authorization": f"Bearer {accepted.json()['data']['access_token']}"}
    assert client.get(f"/api/v1/students/{student['id']}", headers=guardian).status_code == 200
    sibling = client.post("/api/v1/students", headers=owner["headers"], json={"nickname": "哥哥", "grade": 5}).json()["data"]
    assert client.get(f"/api/v1/students/{sibling['id']}", headers=guardian).status_code == 404
    assert client.post("/api/v1/students", headers=guardian, json={"nickname": "不应创建", "grade": 2}).status_code == 403
    members = client.get("/api/v1/tenants/members", headers=owner["headers"]).json()["data"]["items"]
    owner_id = next(item["user_id"] for item in members if item["role"] == "owner")
    assert client.delete(f"/api/v1/tenants/members/{owner_id}", headers=guardian).status_code == 403
    assert client.delete(f"/api/v1/tenants/members/{owner_id}", headers=owner["headers"]).status_code == 409
    assert client.delete(f"/api/v1/tenants/members/{registered['user_id']}", headers=owner["headers"]).status_code == 200
    assert client.get(f"/api/v1/students/{student['id']}", headers=guardian).status_code == 401
