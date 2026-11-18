"""Password capabilities have narrow grants and transactional migration boundaries."""
import pytest
from alembic import command
from conftest import MIGRATION_HEAD
from conftest import database as session_database
from sqlalchemy import text
from test_platform_identity import platform as platform  # noqa: F401
from test_platform_identity import session
from test_platform_password import change
from test_question_migration import migrate

from wizzy.config import migration_config

PREVIOUS = '0033_review_plan_history'
HEAD = MIGRATION_HEAD
FUNCTIONS = ('public.platform_password_credentials(text)', 'public.platform_change_password(text,uuid,text,text)')


@pytest.fixture
def database(tmp_path_factory):
    yield from session_database.__wrapped__(tmp_path_factory)


def test_capabilities_and_round_trip(database, admin_engine):
    migrate(admin_engine, database, 'downgrade', PREVIOUS)
    with admin_engine.connect() as db:
        assert all(db.execute(text('SELECT to_regprocedure(:name)'), {'name': f}).scalar_one() is None for f in FUNCTIONS)
    for _ in range(2):
        migrate(admin_engine, database, 'upgrade', 'head')
    with admin_engine.connect() as db:
        for function in FUNCTIONS:
            row = db.execute(text('SELECT prosecdef,proconfig FROM pg_proc WHERE oid=to_regprocedure(:name)'), {'name': function}).one()
            assert row.prosecdef and row.proconfig == ['search_path=pg_catalog, pg_temp']
            assert not db.execute(text('''SELECT EXISTS(SELECT 1 FROM pg_proc p,LATERAL aclexplode(p.proacl) a
                WHERE p.oid=to_regprocedure(:name) AND a.grantee=0 AND a.privilege_type='EXECUTE')'''), {'name': function}).scalar_one()
            for key in ('WIZZY_APP_ROLE', 'WIZZY_WORKER_ROLE', 'WIZZY_DISPATCHER_ROLE'):
                assert db.execute(text("SELECT has_function_privilege(:role,:name,'EXECUTE')"),
                                  {'role': database['env'][key], 'name': function}).scalar_one() is (key == 'WIZZY_APP_ROLE')


@pytest.mark.parametrize('direction', ['upgrade', 'downgrade'])
def test_transactional_migration(database, admin_engine, direction):
    if direction == 'upgrade':
        migrate(admin_engine, database, 'downgrade', PREVIOUS)
    config = migration_config()
    with pytest.raises(RuntimeError, match='rollback password'), admin_engine.begin() as db:
        config.attributes.update(connection=db, app_role=database['env']['WIZZY_APP_ROLE'],
                                 worker_role=database['env']['WIZZY_WORKER_ROLE'])
        getattr(command, direction)(config, 'head' if direction == 'upgrade' else PREVIOUS)
        raise RuntimeError('rollback password')
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == (PREVIOUS if direction == 'upgrade' else HEAD)
        assert db.execute(text('SELECT to_regprocedure(:name) IS NOT NULL'), {'name': FUNCTIONS[0]}).scalar_one() is (direction == 'downgrade')


@pytest.mark.parametrize('current', ['wrong', 'Platform-test-only-strong-passphrase'])
def test_downgrade_keeps_password_audit(client, platform, admin_engine, database, current):
    result = change(client, session(client, platform), current=current)
    assert result.status_code in (200, 403)
    with pytest.raises(RuntimeError, match='Cannot discard platform password audit history'):
        migrate(admin_engine, database, 'downgrade', PREVIOUS)
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == HEAD
        assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE action LIKE 'password.%'")).scalar_one() == 1
