from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text


def registered(client, email=None, password="  Exact password with spaces  "):
    email = email or f"{uuid4().hex}@example.test"
    result = client.post("/api/v1/auth/register", json={
        "email": email, "password": password, "privacy_version": "2026-10-25", "accepted_privacy": True,
    })
    assert result.status_code == 201, result.text
    return email, result.json()["data"]


def test_password_is_not_silently_trimmed(client):
    email, _ = registered(client)
    assert client.post("/api/v1/auth/login", json={"email": email, "password": "Exact password with spaces"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": email.upper(), "password": "  Exact password with spaces  "}).status_code == 200


def test_failed_login_limits_persist_despite_rollback(client):
    email, _ = registered(client)
    for _ in range(10):
        assert client.post("/api/v1/auth/login", json={"email": email, "password": "wrong"}).status_code == 401
    result = client.post("/api/v1/auth/login", json={"email": email, "password": "wrong"})
    assert result.status_code == 429
    assert result.json()["error"]["retryable"] is True


def test_cas_concurrent_writes_only_one_wins(client, owner, student):
    def update(_):
        return client.patch(f"/api/v1/students/{student['id']}", headers=owner["headers"], json={"expected_revision": 1, "grade": 6})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(update, range(2)))
    assert sorted(item.status_code for item in responses) == [200, 409]


def test_unconsumed_invitation_revoked_on_guardian_removal(client, owner, student):
    email, user = registered(client)
    headers = {"Authorization": f"Bearer {user['access_token']}"}
    def invite():
        return client.post("/api/v1/guardian-invitations", headers=owner["headers"], json={"email": email, "student_ids": [student["id"]]}).json()["data"]["code"]
    first, outstanding = invite(), invite()
    assert client.post("/api/v1/guardian-invitations/accept", headers=headers, json={"code": first}).status_code == 200
    assert client.delete(f"/api/v1/tenants/members/{user['user_id']}", headers=owner["headers"]).status_code == 200
    assert client.post("/api/v1/guardian-invitations/accept", headers=headers, json={"code": outstanding}).status_code == 404


def test_reject_privileged_database_account(database):
    from wizzy.config import Settings
    from wizzy.main import create_app
    app = create_app(Settings(database_url=database["admin"].render_as_string(hide_password=False), storage_root=database["storage"]))
    try:
        with pytest.raises(RuntimeError, match="Runtime role"):
            with TestClient(app):
                pass
    finally:
        app.state.engine.dispose()


def test_password_and_codes_never_stored_in_plaintext(client, owner, student, admin_engine):
    code = client.post(f"/api/v1/students/{student['id']}/pairing-codes", headers=owner["headers"]).json()["data"]["code"]
    token = owner["headers"]["Authorization"].split()[1]
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM auth_sessions WHERE token_hash=:token"), {"token": token}).scalar() == 0
        assert db.execute(text("SELECT count(*) FROM pairing_codes WHERE code_hash=:code"), {"code": code}).scalar() == 0
        assert db.execute(text("SELECT bool_and(password_hash LIKE '$argon2id$%') FROM users")).scalar() is True


def test_student_pagination_stable_and_validated(client, owner):
    ids = []
    for index in range(3):
        result = client.post("/api/v1/students", headers=owner["headers"], json={"nickname": f"孩子{index}", "grade": 1})
        ids.append(result.json()["data"]["id"])
    first = client.get("/api/v1/students?limit=2", headers=owner["headers"]).json()["data"]
    assert [row["id"] for row in first["items"]] == ids[:2]
    second = client.get("/api/v1/students", params={"limit": 2, "cursor": first["next_cursor"]}, headers=owner["headers"]).json()["data"]
    assert [row["id"] for row in second["items"]] == ids[2:]
    assert second["next_cursor"] is None
    assert client.get("/api/v1/students?limit=101", headers=owner["headers"]).status_code == 422
    assert client.get("/api/v1/students?cursor=invalid", headers=owner["headers"]).status_code == 422


def test_active_guardian_invitation_cannot_deadlock_with_removal(client, owner, student, monkeypatch):
    from threading import Event

    import wizzy.identity as module

    email, user = registered(client)
    unbound = {"Authorization": f"Bearer {user['access_token']}"}

    def invite():
        return client.post("/api/v1/guardian-invitations", headers=owner["headers"], json={"email": email, "student_ids": [student["id"]]}).json()["data"]["code"]

    code = invite()
    accepted = client.post("/api/v1/guardian-invitations/accept", headers=unbound, json={"code": code}).json()["data"]
    active = {"Authorization": f"Bearer {accepted['access_token']}"}
    outstanding = invite()
    ready, removal_locked = Event(), Event()
    original_one, original_run = module.one, module.run

    def coordinate_read(db, query, **params):
        row = original_one(db, query, **params)
        if "role='owner' AND status='active' FOR SHARE" in query:
            ready.set()
            assert removal_locked.wait(5), "removal did not acquire advisory lock"
        return row

    def coordinate_lock(db, query, **params):
        result = original_run(db, query, **params)
        if "pg_advisory_xact_lock" in query:
            removal_locked.set()
        return result

    monkeypatch.setattr(module, "one", coordinate_read)
    monkeypatch.setattr(module, "run", coordinate_lock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        accepting = pool.submit(client.post, "/api/v1/guardian-invitations/accept", headers=active, json={"code": outstanding})
        assert ready.wait(5), "invitation did not acquire its membership read lock"
        removing = pool.submit(client.delete, f"/api/v1/tenants/members/{user['user_id']}", headers=owner["headers"])
        assert accepting.result(timeout=10).status_code == 409
        assert removing.result(timeout=10).status_code == 200
