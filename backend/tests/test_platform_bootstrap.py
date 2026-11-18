"""First administrator initialization is offline, atomic and never a reset operation."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from conftest import database as session_database
from fastapi.testclient import TestClient
from sqlalchemy import text

from wizzy.security import passwords

PASSWORD = 'Offline-platform-only-strong-passphrase'


@pytest.fixture
def database(tmp_path_factory):
    yield from session_database.__wrapped__(tmp_path_factory)


@pytest.fixture
def credentials():
    return dict(email=f'ADMIN-{uuid4()}@example.test', password=PASSWORD)


def initialize(database, credentials):
    from wizzy.platform_bootstrap import initialize as bootstrap

    return bootstrap(database['admin'], **credentials)


def test_first_admin_is_hashed_audited_and_can_login(database, credentials, admin_engine, app):
    identifier = initialize(database, credentials)
    with admin_engine.connect() as db:
        row = db.execute(text('SELECT * FROM platform_accounts WHERE id=:id'), {'id': identifier}).mappings().one()
        assert row['email'] == credentials['email'].lower()
        assert row['role'] == 'security_admin' and row['status'] == 'active'
        assert passwords.verify(PASSWORD, row['password_hash'])
        assert row['encrypted_totp'] == 'unused'
        assert row['last_totp_step'] == -1
        assert db.execute(text('SELECT count(*) FROM platform_sessions')).scalar_one() == 0
        assert db.execute(text("SELECT action FROM platform_audit_events WHERE actor_id=:id"), {'id': identifier}).scalar_one() == 'bootstrap'
    from test_platform_identity import login

    with TestClient(app) as client:
        response = login(client, {'email': credentials['email'], 'app': app}, password=PASSWORD)
        assert response.status_code == 200, response.text


def test_repeat_initialization_does_not_reset_or_create_account(database, credentials, admin_engine):
    identifier = initialize(database, credentials)
    with admin_engine.begin() as db:
        db.execute(text("UPDATE platform_accounts SET status='disabled' WHERE id=:id"), {'id': identifier})
    from wizzy.platform_bootstrap import BootstrapError

    with pytest.raises(BootstrapError, match='already initialized'):
        initialize(database, credentials | {'email': 'another@example.test'})
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT id,status FROM platform_accounts')).one() == (identifier, 'disabled')
        assert db.execute(text('SELECT count(*) FROM platform_audit_events')).scalar_one() == 1


@pytest.mark.parametrize('change', [
    {'password': 'short'}, {'email': 'not-an-email'}, {'email': 'has space@example.test'},
])
def test_invalid_enrollment_leaves_no_account(database, credentials, admin_engine, change):
    from wizzy.platform_bootstrap import BootstrapError

    with pytest.raises(BootstrapError):
        initialize(database, credentials | change)
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM platform_accounts')).scalar_one() == 0
        assert db.execute(text('SELECT count(*) FROM platform_audit_events')).scalar_one() == 0


def test_runtime_role_cannot_bootstrap(database, credentials, admin_engine):
    from wizzy.platform_bootstrap import BootstrapError

    with pytest.raises(BootstrapError, match='offline'):
        initialize(database | {'admin': database['url']}, credentials)
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM platform_accounts')).scalar_one() == 0


def test_parallel_bootstrap_creates_only_one_admin(database, credentials, admin_engine):
    from wizzy.platform_bootstrap import BootstrapError

    def attempt(_):
        try:
            initialize(database, credentials)
            return 'created'
        except BootstrapError:
            return 'refused'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == ['created', 'refused']
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM platform_accounts')).scalar_one() == 1
        assert db.execute(text('SELECT count(*) FROM platform_audit_events')).scalar_one() == 1


def test_bootstrap_audit_failure_rolls_back(database, credentials, admin_engine):
    with admin_engine.begin() as db:
        db.execute(text('''CREATE FUNCTION public.fail_bootstrap_audit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'injected bootstrap failure'; END $$;
            CREATE TRIGGER fail_bootstrap_audit BEFORE INSERT ON platform_audit_events
            FOR EACH ROW EXECUTE FUNCTION public.fail_bootstrap_audit()'''))
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError):
        initialize(database, credentials)
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM platform_accounts')).scalar_one() == 0


def test_cli_does_not_print_secrets_or_driver_errors(monkeypatch, capsys):
    from wizzy.platform_bootstrap import main

    monkeypatch.setenv('WIZZY_MIGRATION_URL', 'postgresql+psycopg://sensitive:never-log-me@localhost/missing')
    values = iter([PASSWORD, PASSWORD])
    monkeypatch.setattr('getpass.getpass', lambda _: next(values))
    def fail(*args, **kwargs):
        raise RuntimeError('driver-sensitive-input-never-log-me')
    monkeypatch.setattr('wizzy.platform_bootstrap.initialize', fail)
    assert main(['--email', 'admin@example.test']) == 1
    output = capsys.readouterr()
    assert output.out == ''
    assert 'failed' in output.err
    assert 'never-log-me' not in output.err and 'sensitive-input' not in output.err


def test_cli_requires_explicit_offline_url_before_reading_secrets(monkeypatch, capsys):
    from wizzy.platform_bootstrap import main

    monkeypatch.delenv('WIZZY_MIGRATION_URL', raising=False)
    def unexpected_prompt(_):
        pytest.fail('No secrets should be prompted without an explicit offline URL')
    monkeypatch.setattr('getpass.getpass', unexpected_prompt)
    assert main(['--email', 'admin@example.test']) == 1
    assert 'failed' in capsys.readouterr().err
