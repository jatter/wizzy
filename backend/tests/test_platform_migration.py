"""Platform tables cannot be queried directly by any runtime role."""
import pytest
from alembic import command
from conftest import MIGRATION_HEAD
from conftest import database as session_database
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_question_migration import migrate

from wizzy.config import migration_config

TABLES = ('platform_accounts', 'platform_sessions', 'platform_audit_events',
          'platform_roles', 'platform_permissions', 'platform_role_permissions')
FUNCTIONS = (
    'public.platform_credentials(text)',
    'public.platform_login(uuid,uuid,bigint,text,uuid,text)',
    'public.platform_request(text,text,text,integer,timestamptz,uuid)',
)


@pytest.fixture
def database(tmp_path_factory):
    yield from session_database.__wrapped__(tmp_path_factory)


def test_platform_round_trip_and_function_privileges(database, admin_engine):
    migrate(admin_engine, database, 'downgrade', '0014_timetable_core')
    with admin_engine.connect() as db:
        for table in TABLES:
            assert db.execute(text('SELECT to_regclass(:name)'), {'name': 'public.'+table}).scalar_one() is None
    migrate(admin_engine, database, 'upgrade', 'head')
    migrate(admin_engine, database, 'upgrade', 'head')
    with admin_engine.connect() as db:
        for table in TABLES:
            assert db.execute(text('SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid=to_regclass(:name)'),
                              {'name': 'public.'+table}).scalar_one()
            for key in ('WIZZY_APP_ROLE', 'WIZZY_WORKER_ROLE', 'WIZZY_DISPATCHER_ROLE'):
                assert not db.execute(text("SELECT has_table_privilege(:role,:name,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')"),
                                      {'role': database['env'][key], 'name': 'public.'+table}).scalar_one()
        for function in FUNCTIONS:
            row = db.execute(text('SELECT prosecdef,proconfig FROM pg_proc WHERE oid=to_regprocedure(:name)'), {'name': function}).one()
            assert row.prosecdef and row.proconfig == ['search_path=pg_catalog, pg_temp']
            assert not db.execute(text('''SELECT EXISTS(SELECT 1 FROM pg_proc p,LATERAL aclexplode(p.proacl) a
                WHERE p.oid=to_regprocedure(:name) AND a.grantee=0 AND a.privilege_type='EXECUTE')'''), {'name': function}).scalar_one()
            for key in ('WIZZY_APP_ROLE', 'WIZZY_WORKER_ROLE', 'WIZZY_DISPATCHER_ROLE'):
                assert db.execute(text("SELECT has_function_privilege(:role,:name,'EXECUTE')"),
                                  {'role': database['env'][key], 'name': function}).scalar_one() == (key == 'WIZZY_APP_ROLE')
    runtime = create_engine(database['url'])
    try:
        for table in TABLES:
            with pytest.raises(DBAPIError), runtime.begin() as db:
                db.execute(text('SELECT * FROM public.' + table))
    finally:
        runtime.dispose()


def test_platform_failed_upgrade_is_atomic(database, admin_engine):
    migrate(admin_engine, database, 'downgrade', '0014_timetable_core')
    config = migration_config()
    with pytest.raises(RuntimeError, match='rollback platform'), admin_engine.begin() as db:
        config.attributes.update(connection=db, app_role=database['env']['WIZZY_APP_ROLE'],
                                 worker_role=database['env']['WIZZY_WORKER_ROLE'])
        command.upgrade(config, 'head')
        raise RuntimeError('rollback platform')
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0014_timetable_core'
        for table in TABLES:
            assert db.execute(text('SELECT to_regclass(:name)'), {'name': 'public.'+table}).scalar_one() is None
    migrate(admin_engine, database, 'upgrade', 'head')


def test_downgrade_preserves_platform_identity_and_audit(database, admin_engine):
    from uuid import uuid4

    identifier = uuid4()
    with admin_engine.begin() as db:
        db.execute(text('''INSERT INTO platform_accounts(id,email,password_hash,encrypted_totp,credential_version,role)
            VALUES(:id,'migration-history@example.test','test-hash','test-ciphertext',:version,'security_admin')'''),
            {'id': identifier, 'version': uuid4()})
        db.execute(text("INSERT INTO platform_audit_events VALUES(:id,:actor,'bootstrap','history-test',clock_timestamp())"),
                   {'id': uuid4(), 'actor': identifier})
    with pytest.raises(RuntimeError, match='Cannot discard platform identity or audit history'):
        migrate(admin_engine, database, 'downgrade', '0014_timetable_core')
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == MIGRATION_HEAD
        assert db.execute(text('SELECT count(*) FROM platform_accounts WHERE id=:id'), {'id': identifier}).scalar_one() == 1
        assert db.execute(text('SELECT count(*) FROM platform_audit_events WHERE actor_id=:id'), {'id': identifier}).scalar_one() == 1
