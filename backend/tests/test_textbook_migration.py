"""Textbook migration rollback and history protections on disposable PostgreSQL."""
import pytest
import test_textbook_rights as textbook_fixtures
from alembic import command
from conftest import MIGRATION_HEAD
from conftest import database as session_database
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_question_migration import migrate
from test_textbook_rights import BASE, create, session

from wizzy.config import migration_config

platform = textbook_fixtures.platform
TABLES = ('textbook_editions', 'textbook_edition_revisions')
CAPABILITY = 'public.textbook_request(text,text,text,jsonb,uuid,integer,timestamptz,uuid,text,text)'
HELPERS = ('public.textbook_availability(jsonb,text,timestamptz)', 'public.textbook_metadata_valid(jsonb)', 'public.textbook_view(uuid,integer)',
           'public.textbook_revision_immutable()')


@pytest.fixture
def database(tmp_path_factory):
    yield from session_database.__wrapped__(tmp_path_factory)


def assert_absent(db):
    for table in TABLES:
        assert db.execute(text('SELECT to_regclass(:name)'), {'name': 'public.'+table}).scalar_one() is None
    for function in (CAPABILITY, *HELPERS):
        assert db.execute(text('SELECT to_regprocedure(:name)'), {'name': function}).scalar_one() is None
    assert not db.execute(text("SELECT EXISTS(SELECT 1 FROM platform_permissions WHERE name LIKE 'platform.textbooks.%')")).scalar_one()
    assert not db.execute(text("SELECT EXISTS(SELECT 1 FROM platform_role_permissions WHERE permission LIKE 'platform.textbooks.%')")).scalar_one()
    assert 'textbooks.create' not in db.execute(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='platform_audit_events_action_check'")).scalar_one()


def test_empty_round_trip_and_private_capabilities(database, admin_engine):
    migrate(admin_engine, database, 'downgrade', '0024_school_roster')
    with admin_engine.connect() as db:
        assert_absent(db)
    migrate(admin_engine, database, 'upgrade', 'head')
    migrate(admin_engine, database, 'upgrade', 'head')
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == MIGRATION_HEAD
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
            assert definition.prosecdef == (function in (CAPABILITY, 'public.textbook_view(uuid,integer)'))
            assert definition.proconfig == ['search_path=pg_catalog, pg_temp']
            assert not db.execute(text("""SELECT EXISTS(SELECT 1 FROM pg_proc p,LATERAL aclexplode(p.proacl) a
                WHERE p.oid=to_regprocedure(:name) AND a.grantee=0 AND a.privilege_type='EXECUTE')"""), {'name': function}).scalar_one()
            for key in ('WIZZY_APP_ROLE', 'WIZZY_WORKER_ROLE', 'WIZZY_DISPATCHER_ROLE'):
                assert db.execute(text("SELECT has_function_privilege(:role,:name,'EXECUTE')"), {
                    'role': database['env'][key], 'name': function}).scalar_one() == (
                        key == 'WIZZY_APP_ROLE' and function == CAPABILITY)
        permissions = db.execute(text("SELECT role,permission FROM platform_role_permissions WHERE permission LIKE 'platform.textbooks.%'")).all()
        assert set(permissions) == {('security_admin', 'platform.textbooks.read'), ('security_admin', 'platform.textbooks.write'), ('security_admin', 'platform.textbooks.review'),
                                    ('operator', 'platform.textbooks.read')}
    runtime = create_engine(database['url'])
    try:
        for table in TABLES:
            with pytest.raises(DBAPIError), runtime.begin() as db:
                db.execute(text('SELECT * FROM public.'+table))
    finally:
        runtime.dispose()


@pytest.mark.parametrize('direction', ['upgrade', 'downgrade'])
def test_failed_migration_rolls_back_schema_permissions_and_version(database, admin_engine, direction):
    previous = '0024_school_roster'
    if direction == 'upgrade':
        migrate(admin_engine, database, 'downgrade', previous)
    config = migration_config()
    with pytest.raises(RuntimeError, match='rollback textbook rights'), admin_engine.begin() as db:
        config.attributes.update(connection=db, app_role=database['env']['WIZZY_APP_ROLE'],
                                 worker_role=database['env']['WIZZY_WORKER_ROLE'])
        getattr(command, direction)(config, 'head' if direction == 'upgrade' else previous)
        raise RuntimeError('rollback textbook rights')
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == (
            previous if direction == 'upgrade' else MIGRATION_HEAD)
        if direction == 'upgrade':
            assert_absent(db)
        else:
            assert db.execute(text("SELECT has_function_privilege(:role,:name,'EXECUTE')"), {
                'role': database['env']['WIZZY_APP_ROLE'], 'name': CAPABILITY}).scalar_one()
            assert db.execute(text("SELECT count(*) FROM platform_permissions WHERE name LIKE 'platform.textbooks.%'")).scalar_one() == 3
            for table in TABLES:
                assert db.execute(text('SELECT to_regclass(:name)'), {'name': 'public.'+table}).scalar_one() is not None
    migrate(admin_engine, database, 'upgrade', 'head')


def test_school_history_blocks_downgrade(client, platform, admin_engine, database):
    headers = session(client, platform)
    school = create(client, headers)
    with pytest.raises(RuntimeError, match='Cannot discard textbook editions or review history'):
        migrate(admin_engine, database, 'downgrade', '0024_school_roster')
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == MIGRATION_HEAD
        assert db.execute(text('SELECT count(*) FROM textbook_edition_revisions')).scalar_one() == 1
        assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE action='textbooks.create'")).scalar_one() == 1
    assert client.get(BASE+'/'+school['id'], headers=headers).json()['data'] == school


def test_audit_alone_blocks_downgrade(client, platform, admin_engine, database):
    with admin_engine.begin() as db:
        db.execute(text("""INSERT INTO platform_audit_events(id,actor_id,action,request_id)
            VALUES(gen_random_uuid(),:actor,'textbooks.revise','migration-audit-only')"""), {'actor': platform['id']})
    with pytest.raises(RuntimeError, match='Cannot discard textbook editions or review history'):
        migrate(admin_engine, database, 'downgrade', '0024_school_roster')
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == MIGRATION_HEAD
        assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE action='textbooks.revise'")).scalar_one() == 1


@pytest.mark.parametrize('statement', [
    "UPDATE textbook_edition_revisions SET reason='changed'",
    'DELETE FROM textbook_edition_revisions',
    'TRUNCATE textbook_edition_revisions CASCADE',
])
def test_history_rejects_mutation(client, platform, admin_engine, statement):
    headers = session(client, platform)
    school = create(client, headers)
    with pytest.raises(DBAPIError, match='Textbook history is immutable'), admin_engine.begin() as db:
        db.execute(text(statement))
    assert client.get(BASE+'/'+school['id'], headers=headers).json()['data'] == school


def test_current_revision_must_belong_to_same_textbook(client, platform, admin_engine):
    headers = session(client, platform)
    school = create(client, headers)
    with pytest.raises(DBAPIError, match='textbook_current_revision'), admin_engine.begin() as db:
        db.execute(text('UPDATE textbook_editions SET current_revision=2 WHERE id=:id'), {'id': school['id']})
    assert client.get(BASE+'/'+school['id'], headers=headers).json()['data'] == school
