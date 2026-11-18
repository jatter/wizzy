"""Real PostgreSQL only; every run owns a fresh DB and restricted login role."""
import os
import secrets
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from alembic.script import ScriptDirectory
from psycopg import sql
from sqlalchemy import URL, create_engine

from wizzy.config import migration_config

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_HEAD = ScriptDirectory.from_config(migration_config()).get_current_head()


class DatabaseConfig(dict):
    def __repr__(self):
        return "<isolated PostgreSQL test database; credentials redacted>"



def admin_url(database):
    # Read only the required local secret; never log a connection URL.
    values = dict(
        line.split("=", 1) for line in (ROOT / ".env").read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    return URL.create("postgresql+psycopg", username="wizzy",
                      password=values["POSTGRES_PASSWORD"], host="127.0.0.1",
                      port=5432, database=database)


@pytest.fixture(scope="session")
def database(tmp_path_factory):
    name = f"wizzy_test_{uuid4().hex[:12]}"
    role = f"{name}_app"
    password = secrets.token_urlsafe(32)
    dispatcher_role = f"{name}_dispatch"
    dispatcher_password = secrets.token_urlsafe(32)
    worker_role = f"{name}_worker"
    worker_password = secrets.token_urlsafe(32)
    admin = admin_url("postgres")
    with psycopg.connect(admin.render_as_string(hide_password=False).replace("+psycopg", ""), autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT").format(sql.Identifier(role), sql.Literal(password)))
        connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION").format(sql.Identifier(dispatcher_role), sql.Literal(dispatcher_password)))
        connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION").format(sql.Identifier(worker_role), sql.Literal(worker_password)))
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    runtime = URL.create("postgresql+psycopg", username=role, password=password,
                         host="127.0.0.1", port=5432, database=name)
    env = os.environ | {
        "WIZZY_MIGRATION_URL": admin_url(name).render_as_string(hide_password=False),
        "WIZZY_APP_ROLE": role,
        "WIZZY_DISPATCHER_ROLE": dispatcher_role,
        "WIZZY_WORKER_ROLE": worker_role,
    }
    try:
        subprocess.run(["uv", "run", "alembic", "upgrade", "head"], cwd=ROOT / "backend",
                       env=env, check=True, capture_output=True, text=True)
        yield DatabaseConfig(url=runtime.render_as_string(hide_password=False), admin=admin_url(name),
                             dispatcher_url=runtime.set(username=dispatcher_role, password=dispatcher_password),
                             worker_url=runtime.set(username=worker_role, password=worker_password),
                             storage=tmp_path_factory.mktemp("private-media"), env=env)
    finally:
        with psycopg.connect(admin.render_as_string(hide_password=False).replace("+psycopg", ""), autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
            connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
            connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(dispatcher_role)))
            connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(worker_role)))


@pytest.fixture
def app(database):
    from wizzy.config import Settings
    from wizzy.main import create_app

    application = create_app(Settings(database_url=database["url"],
                                      storage_root=database["storage"], environment="test"))
    database.setdefault('runtime_engines', []).append(application.state.engine)
    yield application
    database['runtime_engines'].remove(application.state.engine)
    application.state.engine.dispose()


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    # Isolate IP-based rate-limit buckets without disabling the real limiter.
    with TestClient(app, client=(f"test-{uuid4().hex}", 50000)) as test_client:
        yield test_client


@pytest.fixture
def owner(client):
    response = client.post("/api/v1/auth/register", json={
        "email": f"guardian-{uuid4().hex}@example.test", "password": "Test-only-strong-passphrase",
        "privacy_version": "2026-10-25", "accepted_privacy": True,
    })
    assert response.status_code == 201, response.text
    token = response.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    response = client.post("/api/v1/tenants", json={"name": "测试家庭"}, headers=headers)
    assert response.status_code == 201, response.text
    return {"headers": {"Authorization": f"Bearer {response.json()['data']['access_token']}"},
            "tenant_id": response.json()["data"]["tenant"]["id"]}


@pytest.fixture
def student(client, owner):
    response = client.post("/api/v1/students", headers=owner["headers"],
                           json={"nickname": "小知", "grade": 3})
    assert response.status_code == 201, response.text
    value = response.json()["data"]
    response = client.post("/api/v1/consents", headers=owner["headers"], json={
        "student_id": value["id"], "policy_version": "2026-10-25",
        "child_rules_version": "2026-10-25", "granted": True,
    })
    assert response.status_code == 201, response.text
    return value


@pytest.fixture
def admin_engine(database):
    engine = create_engine(database["admin"])
    yield engine
    engine.dispose()
