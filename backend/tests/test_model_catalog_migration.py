"""Catalog security and rollback guarantees on disposable PostgreSQL databases."""
import pytest
import test_model_catalog as catalog_fixtures
from alembic import command
from conftest import MIGRATION_HEAD
from conftest import database as session_database
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_model_catalog import create, session
from test_question_migration import migrate

from wizzy.config import migration_config

platform = catalog_fixtures.platform

TABLES = ('model_catalog', 'model_catalog_revisions')
CAPABILITY = 'public.model_catalog_request(text,text,text,jsonb,uuid,integer,timestamptz,uuid,text,boolean)'
HELPERS = ('public.model_metadata_valid(jsonb)', 'public.model_catalog_view(uuid,integer)',
           'public.model_revision_immutable()', 'public.model_catalog_availability(jsonb,timestamptz)')


@pytest.fixture
def database(tmp_path_factory):
    yield from session_database.__wrapped__(tmp_path_factory)


def test_empty_round_trip_and_private_capabilities(database, admin_engine):
    migrate(admin_engine, database, 'downgrade', '0017_shared_review_plans')
    with admin_engine.connect() as db:
        for table in TABLES:
            assert db.execute(text('SELECT to_regclass(:name)'), {'name': 'public.'+table}).scalar_one() is None
        for function in (CAPABILITY, *HELPERS):
            assert db.execute(text('SELECT to_regprocedure(:name)'), {'name': function}).scalar_one() is None
        assert not db.execute(text("SELECT EXISTS(SELECT 1 FROM platform_permissions WHERE name LIKE 'platform.models.%')")).scalar_one()
    migrate(admin_engine, database, 'upgrade', 'head')
    migrate(admin_engine, database, 'upgrade', 'head')
    with admin_engine.connect() as db:
        for table in TABLES:
            assert db.execute(text('SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid=to_regclass(:name)'),
                              {'name': 'public.'+table}).scalar_one()
            for key in ('WIZZY_APP_ROLE', 'WIZZY_WORKER_ROLE', 'WIZZY_DISPATCHER_ROLE'):
                for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'):
                    assert not db.execute(text('SELECT has_table_privilege(:role,:name,:privilege)'), {
                        'role': database['env'][key], 'name': 'public.'+table, 'privilege': privilege}).scalar_one()
        for function in (CAPABILITY, *HELPERS):
            definition = db.execute(text('SELECT prosecdef,proconfig FROM pg_proc WHERE oid=to_regprocedure(:name)'),
                                    {'name': function}).one()
            # Pure validator and immutable trigger need no elevated privileges.
            assert definition.prosecdef == (function in (CAPABILITY, 'public.model_catalog_view(uuid,integer)'))
            assert definition.proconfig == ['search_path=pg_catalog, pg_temp']
            assert not db.execute(text('''SELECT EXISTS(SELECT 1 FROM pg_proc p,LATERAL aclexplode(p.proacl) a
                WHERE p.oid=to_regprocedure(:name) AND a.grantee=0 AND a.privilege_type='EXECUTE')'''), {'name': function}).scalar_one()
            for key in ('WIZZY_APP_ROLE', 'WIZZY_WORKER_ROLE', 'WIZZY_DISPATCHER_ROLE'):
                assert db.execute(text("SELECT has_function_privilege(:role,:name,'EXECUTE')"), {
                    'role': database['env'][key], 'name': function}).scalar_one() == (
                        key == 'WIZZY_APP_ROLE' and function == CAPABILITY)
    runtime = create_engine(database['url'])
    try:
        for table in TABLES:
            with pytest.raises(DBAPIError), runtime.begin() as db:
                db.execute(text('SELECT * FROM public.'+table))
    finally:
        runtime.dispose()


def test_failed_upgrade_rolls_back_schema_permissions_and_version(database, admin_engine):
    migrate(admin_engine, database, 'downgrade', '0017_shared_review_plans')
    config = migration_config()
    with pytest.raises(RuntimeError, match='rollback model catalog'), admin_engine.begin() as db:
        config.attributes.update(connection=db, app_role=database['env']['WIZZY_APP_ROLE'],
                                 worker_role=database['env']['WIZZY_WORKER_ROLE'])
        command.upgrade(config, 'head')
        raise RuntimeError('rollback model catalog')
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0017_shared_review_plans'
        for table in TABLES:
            assert db.execute(text('SELECT to_regclass(:name)'), {'name': 'public.'+table}).scalar_one() is None
        for function in (CAPABILITY, *HELPERS):
            assert db.execute(text('SELECT to_regprocedure(:name)'), {'name': function}).scalar_one() is None
        assert not db.execute(text("SELECT EXISTS(SELECT 1 FROM platform_permissions WHERE name LIKE 'platform.models.%')")).scalar_one()
    migrate(admin_engine, database, 'upgrade', 'head')


def test_model_history_blocks_downgrade(client, platform, admin_engine, database):
    headers = session(client, platform)
    response = create(client, headers)
    assert response.status_code == 201, response.text
    model = response.json()['data']
    with pytest.raises(RuntimeError, match='Cannot discard model catalog or revision history'):
        migrate(admin_engine, database, 'downgrade', '0017_shared_review_plans')
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == MIGRATION_HEAD
        assert db.execute(text('SELECT count(*) FROM model_catalog')).scalar_one() == 1
        assert db.execute(text('SELECT count(*) FROM model_catalog_revisions')).scalar_one() == 1
    assert client.get('/api/v1/admin/models/'+model['id'], headers=headers).json()['data'] == model


def test_model_audit_alone_blocks_downgrade(client, platform, admin_engine, database):
    with admin_engine.begin() as db:
        db.execute(text("""INSERT INTO platform_audit_events(id,actor_id,action,request_id)
            VALUES(gen_random_uuid(),:actor,'models.revise','migration-audit-only')"""), {'actor': platform['id']})
    with pytest.raises(RuntimeError, match='Cannot discard model catalog or revision history'):
        migrate(admin_engine, database, 'downgrade', '0017_shared_review_plans')
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == MIGRATION_HEAD
        assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE action='models.revise'")).scalar_one() == 1
