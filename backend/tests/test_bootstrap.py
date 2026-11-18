"""Bootstrap uses fresh databases only; never resets the local development database."""
import os
from uuid import uuid4

import psycopg
import pytest
from conftest import MIGRATION_HEAD, admin_url
from dotenv import dotenv_values
from psycopg import sql
from sqlalchemy import create_engine, text


@pytest.fixture
def bootstrap_database():
    name = f"wizzy_bootstrap_{uuid4().hex[:12]}"
    role = f"{name}_app"
    with psycopg.connect(admin_url("postgres").render_as_string(hide_password=False).replace("+psycopg", ""), autocommit=True) as db:
        db.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            yield admin_url(name), role
        finally:
            db.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
            db.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
            db.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role + "_dispatch")))
            db.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role + "_worker")))


def test_bootstrap_creates_restricted_role_migrates_and_is_repeatable(bootstrap_database, tmp_path):
    from wizzy.bootstrap import initialize
    from wizzy.execution import ExecutionStore

    url, role = bootstrap_database
    path = tmp_path / ".env"
    path.write_text("# preserve this comment\nUNRELATED_SETTING=keep-me\n")
    initialize(url, path, role)
    initial = path.read_bytes()
    values = dotenv_values(path)
    assert values["UNRELATED_SETTING"] == "keep-me"
    assert values["WIZZY_APP_ROLE"] == role
    assert len(values["WIZZY_APP_PASSWORD"]) >= 32
    assert values["WIZZY_DISPATCHER_ROLE"] == role + "_dispatch"
    assert len(values["WIZZY_DISPATCHER_PASSWORD"]) >= 32
    assert values["WIZZY_DISPATCHER_PASSWORD"] != values["WIZZY_APP_PASSWORD"]
    assert values["WIZZY_WORKER_ROLE"] == role + "_worker"
    assert len(values["WIZZY_WORKER_PASSWORD"]) >= 32
    assert len({values[f"WIZZY_{name}_PASSWORD"] for name in ("APP", "DISPATCHER", "WORKER")}) == 3
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".env.*.tmp"))
    runtime = create_engine(url.set(username=role, password=values["WIZZY_APP_PASSWORD"]), hide_parameters=True)
    try:
        with runtime.connect() as db:
            assert db.execute(text("SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolinherit,rolreplication FROM pg_roles WHERE rolname=current_user")).one() == (False,) * 6
            assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == MIGRATION_HEAD
            assert db.execute(text("SELECT count(*) FROM students")).scalar_one() == 0
            assert db.execute(text("SELECT count(*) FROM pg_tables WHERE tableowner=current_user")).scalar_one() == 0
    finally:
        runtime.dispose()
    dispatcher = create_engine(url.set(username=values["WIZZY_DISPATCHER_ROLE"], password=values["WIZZY_DISPATCHER_PASSWORD"]), hide_parameters=True)
    try:
        with dispatcher.connect() as db:
            assert db.execute(text("SELECT count(*) FROM public.claim_outbox(1)")).scalar_one() == 0
            assert not db.execute(text("SELECT has_table_privilege(current_user,'public.outbox_events','SELECT')")).scalar_one()
    finally:
        dispatcher.dispose()
    worker = create_engine(url.set(username=values["WIZZY_WORKER_ROLE"], password=values["WIZZY_WORKER_PASSWORD"]), hide_parameters=True)
    try:
        store = ExecutionStore(worker)
        assert store.claim({
            "event_id": str(uuid4()), "job_id": str(uuid4()),
            "source_revision": 1, "deletion_epoch": 0, "request_id": "bootstrap-test",
        }) is None
        with worker.connect() as db:
            assert not db.execute(text("SELECT has_table_privilege(current_user,'public.jobs','SELECT')")).scalar_one()
    finally:
        worker.dispose()
    initialize(url, path, role)
    assert path.read_bytes() == initial


def test_existing_role_without_secret_is_not_reset(bootstrap_database, tmp_path):
    from wizzy.bootstrap import BootstrapError, initialize

    url, role = bootstrap_database
    path = tmp_path / ".env"
    initialize(url, path, role)
    path.write_text("# secret deliberately absent\n")
    with pytest.raises(BootstrapError, match="existing role"):
        initialize(url, path, role)
    assert path.read_text() == "# secret deliberately absent\n"


def test_bootstrap_rejects_privileged_role_without_touching_env(bootstrap_database, tmp_path):
    from wizzy.bootstrap import BootstrapError, initialize

    url, _ = bootstrap_database
    path = tmp_path / ".env"
    with pytest.raises(BootstrapError, match="restricted"):
        initialize(url, path, "wizzy")
    assert not path.exists()


def test_failed_migration_rolls_back_new_role_and_tables(bootstrap_database, tmp_path, monkeypatch):
    from alembic import command

    from wizzy.bootstrap import initialize

    url, role = bootstrap_database
    path = tmp_path / ".env"
    original = command.upgrade

    def fail(config, revision):
        original(config, revision)
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(command, "upgrade", fail)
    with pytest.raises(RuntimeError, match="injected"):
        initialize(url, path, role)
    assert not path.exists()
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as db:
            assert db.execute(text("SELECT count(*) FROM pg_roles WHERE rolname IN (:role,:dispatcher,:worker)"), {"role": role, "dispatcher": role + "_dispatch", "worker": role + "_worker"}).scalar_one() == 0
            assert db.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname='public'")).scalar_one() == 0
    finally:
        engine.dispose()


def test_env_write_failure_rolls_back_database(bootstrap_database, tmp_path, monkeypatch):
    import wizzy.bootstrap as bootstrap

    url, role = bootstrap_database
    path = tmp_path / ".env"
    path.write_text("# do not lose local settings\n")

    def fail(*args):
        raise OSError("injected disk failure")

    monkeypatch.setattr(bootstrap, "save_credentials", fail)
    with pytest.raises(OSError, match="injected"):
        bootstrap.initialize(url, path, role)
    assert path.read_text() == "# do not lose local settings\n"
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as db:
            assert db.execute(text("SELECT count(*) FROM pg_roles WHERE rolname IN (:role,:dispatcher,:worker)"), {"role": role, "dispatcher": role + "_dispatch", "worker": role + "_worker"}).scalar_one() == 0
            assert db.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname='public'")).scalar_one() == 0
    finally:
        engine.dispose()


def test_existing_role_with_wrong_secret_is_not_reset(bootstrap_database, tmp_path):
    from dotenv import set_key
    from sqlalchemy.exc import OperationalError

    from wizzy.bootstrap import initialize

    url, role = bootstrap_database
    path = tmp_path / ".env"
    initialize(url, path, role)
    correct = dotenv_values(path)["WIZZY_APP_PASSWORD"]
    set_key(path, "WIZZY_APP_PASSWORD", "deliberately-incorrect-secret")
    before = path.read_bytes()
    with pytest.raises(OperationalError):
        initialize(url, path, role)
    assert path.read_bytes() == before
    runtime = create_engine(url.set(username=role, password=correct), hide_parameters=True)
    try:
        with runtime.connect() as db:
            assert db.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        runtime.dispose()
