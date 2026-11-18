"""Readiness must validate schema compatibility, not merely a reachable database."""
import logging

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError


def assert_safe_error(response, status, code):
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["retryable"] is True
    assert response.headers["X-Request-ID"] == response.json()["request_id"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("version", ["0002_media", None])
def test_readiness_rejects_outdated_or_empty_schema(client, admin_engine, version):
    with admin_engine.begin() as db:
        original = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        db.execute(text("DELETE FROM alembic_version"))
        if version:
            db.execute(text("INSERT INTO alembic_version VALUES (:version)"), {"version": version})
    try:
        response = client.get("/health/ready")
        assert_safe_error(response, 503, "schema_mismatch")
        assert client.get("/health/live").status_code == 200
    finally:
        with admin_engine.begin() as db:
            db.execute(text("DELETE FROM alembic_version"))
            db.execute(text("INSERT INTO alembic_version VALUES (:version)"), {"version": original})
    assert client.get("/health/ready").status_code == 200


def test_readiness_database_failure_is_safe(client, app, monkeypatch):
    def fail():
        raise OperationalError("secret-sql", {"password": "secret-value"}, RuntimeError("unavailable"))

    monkeypatch.setattr(app.state.engine, "connect", fail)
    response = client.get("/health/ready")
    assert_safe_error(response, 503, "database_unavailable")
    assert "secret" not in response.text


@pytest.mark.parametrize("database_failure", [True, False])
def test_unexpected_and_database_errors_are_redacted(app, caplog, database_failure):
    from fastapi.testclient import TestClient

    @app.get("/injected-failure")
    def fail():
        if database_failure:
            raise OperationalError("secret-sql", {"password": "secret-value"}, RuntimeError("secret-driver"))
        raise RuntimeError("secret-child-content")

    with TestClient(app, raise_server_exceptions=False) as client, caplog.at_level(logging.ERROR, logger="wizzy.errors"):
        response = client.get("/injected-failure")
    assert_safe_error(response, 503 if database_failure else 500,
                      "database_unavailable" if database_failure else "internal_error")
    assert "secret" not in response.text
    assert "secret" not in caplog.text
    assert response.json()["request_id"] in caplog.text
