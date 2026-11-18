"""Roster schema security and reversible empty migrations on disposable PostgreSQL."""
import pytest
import test_school_roster as fixtures
from alembic import command
from conftest import MIGRATION_HEAD
from conftest import database as session_database
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_question_migration import migrate
from test_school_roster import create

from wizzy.config import migration_config

platform = fixtures.platform
org = fixtures.org
roster = fixtures.roster
TABLES = ('school_roster_students', 'school_enrollments', 'school_enrollment_revisions')
CAPABILITY = 'public.school_roster_request(text,text,text,jsonb,uuid,uuid,text,text,text,integer,timestamptz,uuid)'
HELPERS = ('public.school_roster_metadata_valid(jsonb)', 'public.school_roster_view(uuid,integer)')


@pytest.fixture
def database(tmp_path_factory):
    yield from session_database.__wrapped__(tmp_path_factory)


def absent(db):
    for table in TABLES:
        assert db.execute(text('SELECT to_regclass(:name)'), {'name': 'public.'+table}).scalar_one() is None
    for function in (CAPABILITY, *HELPERS):
        assert db.execute(text('SELECT to_regprocedure(:name)'), {'name': function}).scalar_one() is None
    assert db.execute(text("SELECT count(*) FROM platform_permissions WHERE name LIKE 'platform.students.%'")).scalar_one() == 0
    assert 'schools.roster.create' not in db.execute(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='platform_audit_events_action_check'")).scalar_one()


def test_empty_round_trip_and_private_capabilities(database, admin_engine):
    migrate(admin_engine, database, 'downgrade', '0023_school_structure')
    with admin_engine.connect() as db:
        absent(db)
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
            definition = db.execute(text('SELECT prosecdef,proconfig FROM pg_proc WHERE oid=to_regprocedure(:name)'), {'name': function}).one()
            assert definition.prosecdef == (function != HELPERS[0])
            assert definition.proconfig == ['search_path=pg_catalog, pg_temp']
            assert not db.execute(text('''SELECT EXISTS(SELECT 1 FROM pg_proc p,LATERAL aclexplode(p.proacl) a
                WHERE p.oid=to_regprocedure(:name) AND a.grantee=0 AND a.privilege_type='EXECUTE')'''), {'name': function}).scalar_one()
            for key in ('WIZZY_APP_ROLE', 'WIZZY_WORKER_ROLE', 'WIZZY_DISPATCHER_ROLE'):
                assert db.execute(text("SELECT has_function_privilege(:role,:name,'EXECUTE')"), {
                    'role': database['env'][key], 'name': function}).scalar_one() == (key == 'WIZZY_APP_ROLE' and function == CAPABILITY)
        assert set(db.execute(text("SELECT role,permission FROM platform_role_permissions WHERE permission LIKE 'platform.students.%'")).all()) == {
            ('security_admin', 'platform.students.read'), ('security_admin', 'platform.students.write'), ('operator', 'platform.students.read')}
    runtime = create_engine(database['url'])
    try:
        for table in TABLES:
            with pytest.raises(DBAPIError), runtime.begin() as db:
                db.execute(text('SELECT * FROM public.'+table))
    finally:
        runtime.dispose()


@pytest.mark.parametrize('direction', ['upgrade', 'downgrade'])
def test_failed_migration_is_atomic(database, admin_engine, direction):
    previous = '0023_school_structure'
    if direction == 'upgrade':
        migrate(admin_engine, database, 'downgrade', previous)
    config = migration_config()
    with pytest.raises(RuntimeError, match='rollback roster'), admin_engine.begin() as db:
        config.attributes.update(connection=db, app_role=database['env']['WIZZY_APP_ROLE'],
                                 worker_role=database['env']['WIZZY_WORKER_ROLE'])
        getattr(command, direction)(config, 'head' if direction == 'upgrade' else previous)
        raise RuntimeError('rollback roster')
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == (previous if direction == 'upgrade' else MIGRATION_HEAD)
        if direction == 'upgrade':
            absent(db)
        else:
            for table in TABLES:
                assert db.execute(text('SELECT to_regclass(:name)'), {'name': 'public.'+table}).scalar_one() is not None
            assert db.execute(text("SELECT has_function_privilege(:role,:name,'EXECUTE')"), {
                'role': database['env']['WIZZY_APP_ROLE'], 'name': CAPABILITY}).scalar_one()
    migrate(admin_engine, database, 'upgrade', 'head')


@pytest.mark.parametrize('audit_only', [False, True])
def test_history_blocks_downgrade(client, roster, platform, database, admin_engine, audit_only):
    if audit_only:
        with admin_engine.begin() as db:
            db.execute(text("INSERT INTO platform_audit_events(id,actor_id,action,request_id) VALUES(gen_random_uuid(),:actor,'schools.roster.create','audit-only')"), {'actor': platform['id']})
    else:
        create(client, roster)
    with pytest.raises(RuntimeError, match='Cannot discard school roster or change history'):
        migrate(admin_engine, database, 'downgrade', '0023_school_structure')
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == MIGRATION_HEAD
        assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE action='schools.roster.create'")).scalar_one() == 1


@pytest.mark.parametrize('statement', ["UPDATE school_enrollment_revisions SET reason='changed'", 'DELETE FROM school_enrollment_revisions', 'TRUNCATE school_enrollment_revisions CASCADE'])
def test_immutable_history(client, roster, admin_engine, statement):
    item = create(client, roster)
    with pytest.raises(DBAPIError, match='School history is immutable'), admin_engine.begin() as db:
        db.execute(text(statement))
    headers, _, url, _, _ = roster
    assert client.get(url+'/'+item['id'], headers=headers).json()['data'] == item


def test_current_revision_and_cross_school_identity_constraints(client, roster, admin_engine):
    item = create(client, roster)
    other_school = fixtures.structure.schools.create(client, roster[0])
    for statement, params in [
        ('UPDATE school_enrollments SET current_revision=2 WHERE id=:id', {'id': item['id']}),
        ('UPDATE school_enrollments SET school_id=:school WHERE id=:id', {'id': item['id'], 'school': other_school['id']}),
    ]:
        with pytest.raises(DBAPIError), admin_engine.begin() as db:
            db.execute(text(statement), params)
