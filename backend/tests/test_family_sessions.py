"""Family browser sessions survive reload without exposing credentials to JavaScript."""
from uuid import uuid4

from sqlalchemy import text

from wizzy.security import digest

BROWSER = {"X-Family-Session": "browser", "Origin": "http://testserver"}


def test_explicit_public_origin_supports_reverse_proxy_without_trusting_forwarded_host(client, app):
    app.state.settings.family_public_origin = 'http://wizzy.refineview.com'
    response = client.post('/api/v1/auth/register', headers={**BROWSER, 'Origin': 'http://wizzy.refineview.com'}, json={
        'email': f'proxy-{uuid4().hex}@example.test', 'password': PASSWORD,
        'privacy_version': '2026-10-25', 'accepted_privacy': True,
    })
    assert response.status_code == 201, response.text
    assert client.get('/api/v1/auth/me', headers={**BROWSER, 'Origin': 'http://wizzy.refineview.com'}).status_code == 200
    hostile = {**BROWSER, 'Origin': 'http://attacker.example', 'X-Forwarded-Host': 'attacker.example'}
    assert client.get('/api/v1/auth/me', headers=hostile).status_code == 403
PASSWORD = "Test-only-strong-passphrase"


def browser_login(client, owner, admin_engine):
    with admin_engine.connect() as db:
        email = db.execute(text("SELECT email FROM users WHERE id=(SELECT user_id FROM memberships WHERE tenant_id=:id AND role='owner')"), {"id": owner["tenant_id"]}).scalar_one()
    response = client.post("/api/v1/auth/login", headers=BROWSER, json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    assert response.json()["data"]["token_type"] == "cookie"
    assert "access_token" not in response.json()["data"]
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert "Path=/api/v1" in cookie and "Max-Age=86400" in cookie
    return email


def test_browser_registration_and_logout(client):
    response = client.post("/api/v1/auth/register", headers=BROWSER, json={
        "email": f"{uuid4().hex}@example.test", "password": PASSWORD,
        "privacy_version": "2026-10-25", "accepted_privacy": True,
    })
    assert response.status_code == 201
    assert "access_token" not in response.json()["data"]
    assert client.get("/api/v1/auth/me", headers=BROWSER).json()["data"]["audience"] == "guardian"
    assert client.post("/api/v1/auth/logout", headers=BROWSER).status_code == 200
    assert client.get("/api/v1/auth/me", headers=BROWSER).status_code == 401


def test_cookie_restores_active_household_and_rejects_cross_origin_writes(client, owner, student, admin_engine):
    browser_login(client, owner, admin_engine)
    response = client.post("/api/v1/auth/activate-tenant", headers=BROWSER, json={"tenant_id": owner["tenant_id"]})
    assert response.status_code == 200
    assert client.get("/api/v1/auth/me", headers=BROWSER).json()["data"]["tenant_id"] == owner["tenant_id"]
    assert client.get(f"/api/v1/students/{student['id']}/evaluation-consent", headers=BROWSER).status_code == 200
    assert client.post("/api/v1/students", headers={**BROWSER, "Origin": "https://other.example"}, json={"nickname": "越权", "grade": 3}).status_code == 403
    assert client.post("/api/v1/students", json={"nickname": "缺少浏览器标记", "grade": 3}).status_code == 403
    assert client.post("/api/v1/auth/login", headers={**BROWSER, "Origin": "https://other.example"}, json={"email": "other@example.test", "password": PASSWORD}).status_code in (401, 403)


def test_start_learning_replaces_guardian_with_restricted_revocable_session(client, owner, student, admin_engine):
    browser_login(client, owner, admin_engine)
    client.post("/api/v1/auth/activate-tenant", headers=BROWSER, json={"tenant_id": owner["tenant_id"]})
    old_cookie = client.cookies.get("wizzy_family_session")
    response = client.post("/api/v1/auth/start-learning", headers=BROWSER, json={"student_id": student["id"]})
    assert response.status_code == 200
    identity = client.get("/api/v1/auth/me", headers=BROWSER).json()["data"]
    assert identity["audience"] == "child" and identity["student_id"] == student["id"]
    assert identity["role"] is None
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT revoked_at IS NOT NULL FROM auth_sessions WHERE token_hash=:hash"), {"hash": digest(old_cookie)}).scalar_one()
    assert client.post("/api/v1/students", headers=BROWSER, json={"nickname": "越权", "grade": 3}).status_code == 403
    assert client.post("/api/v1/auth/start-learning", headers=BROWSER, json={"student_id": student["id"]}).status_code == 403
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {old_cookie}"}).status_code == 401
    session_id = response.json()["data"]["session_id"]
    assert client.delete(f"/api/v1/child-sessions/{session_id}", headers=owner["headers"]).status_code == 200
    assert client.get("/api/v1/auth/me", headers=BROWSER).status_code == 401


def test_start_learning_requires_consent_and_access(client, owner, student):
    sibling = client.post("/api/v1/students", headers=owner["headers"], json={"nickname": "未授权", "grade": 1}).json()["data"]
    assert client.post("/api/v1/auth/start-learning", headers=owner["headers"], json={"student_id": sibling["id"]}).status_code == 403
    assert client.post("/api/v1/auth/start-learning", headers=owner["headers"], json={"student_id": str(uuid4())}).status_code == 404


def test_paired_browser_session_survives_reload_and_expires(client, owner, student, admin_engine):
    code = client.post(f"/api/v1/students/{student['id']}/pairing-codes", headers=owner["headers"]).json()["data"]["code"]
    response = client.post("/api/v1/child-sessions", headers=BROWSER, json={"code": code, "device_name": "学习平板"})
    assert response.status_code == 201 and response.json()["data"]["token_type"] == "cookie"
    assert client.get("/api/v1/auth/me", headers=BROWSER).json()["data"]["student_id"] == student["id"]
    assert client.get("/api/v1/tenants", headers=BROWSER).status_code == 403
    with admin_engine.begin() as db:
        db.execute(text("UPDATE auth_sessions SET expires_at=now()-interval '1 second' WHERE id=:id"), {"id": response.json()["data"]["session_id"]})
    assert client.get("/api/v1/auth/me", headers=BROWSER).status_code == 401
