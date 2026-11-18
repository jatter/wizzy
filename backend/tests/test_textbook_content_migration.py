"""Content capability ACL, transactional migration and history preservation."""
import pytest
from alembic import command
from conftest import database as session_database
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_question_migration import migrate
from test_textbook_content import source as source
from test_textbook_documents import approved as approved
from test_textbook_documents import private_storage as private_storage
from test_textbook_processing import received as received
from test_textbook_processing import worker as worker
from test_textbook_rights import historical_reviewer as historical_reviewer

from wizzy.config import migration_config

platform = historical_reviewer
reviewer = historical_reviewer

PREVIOUS = '0027_textbook_processing'
HEAD = '0028_textbook_content'
CAPABILITY = 'public.textbook_content_request(text,text,text,jsonb,uuid,uuid,integer,integer,integer,integer,timestamptz,uuid)'
HELPERS = ('public.textbook_content_valid(jsonb,integer)', 'public.textbook_content_ready(jsonb)',
           'public.textbook_content_view(uuid,integer)', 'public.textbook_content_allowed(uuid,integer)')


@pytest.fixture
def database(tmp_path_factory):
    for value in session_database.__wrapped__(tmp_path_factory):
        engine = create_engine(value['admin'], hide_parameters=True)
        try:
            migrate(engine, value, 'downgrade', HEAD)
            yield value
        finally:
            engine.dispose()


def assert_absent(db):
    assert db.execute(text("SELECT to_regclass('public.textbook_contents')")).scalar_one() is None
    assert db.execute(text("SELECT to_regclass('public.textbook_content_revisions')")).scalar_one() is None
    assert db.execute(text('SELECT to_regprocedure(:name)'), {'name': CAPABILITY}).scalar_one() is None
    assert 'textbooks.content.create' not in db.execute(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='platform_audit_events_action_check'")).scalar_one()


def test_empty_round_trip_and_restricted_capability(database, admin_engine):
    migrate(admin_engine, database, 'downgrade', PREVIOUS)
    with admin_engine.connect() as db:
        assert_absent(db)
    migrate(admin_engine, database, 'upgrade', HEAD)
    migrate(admin_engine, database, 'upgrade', HEAD)
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == HEAD
        for table in ('textbook_contents', 'textbook_content_revisions'):
            assert db.execute(text("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid=to_regclass(:table)"), {'table': table}).scalar_one()
            for key in ('WIZZY_APP_ROLE', 'WIZZY_WORKER_ROLE', 'WIZZY_DISPATCHER_ROLE'):
                for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'):
                    assert not db.execute(text("SELECT has_table_privilege(:role,:table,:privilege)"), {'role': database['env'][key], 'table': table, 'privilege': privilege}).scalar_one()
        for function in (CAPABILITY, *HELPERS):
            row = db.execute(text('SELECT prosecdef,proconfig FROM pg_proc WHERE oid=to_regprocedure(:name)'), {'name': function}).one()
            assert row.prosecdef == (function not in HELPERS[:2])
            assert row.proconfig == ['search_path=pg_catalog, pg_temp']
            assert not db.execute(text("SELECT EXISTS(SELECT 1 FROM pg_proc p,LATERAL aclexplode(p.proacl) a WHERE p.oid=to_regprocedure(:name) AND a.grantee=0 AND a.privilege_type='EXECUTE')"), {'name': function}).scalar_one()
            for key in ('WIZZY_APP_ROLE', 'WIZZY_WORKER_ROLE', 'WIZZY_DISPATCHER_ROLE'):
                assert db.execute(text("SELECT has_function_privilege(:role,:name,'EXECUTE')"), {'role': database['env'][key], 'name': function}).scalar_one() == (function == CAPABILITY and key == 'WIZZY_APP_ROLE')
    runtime = create_engine(database['url'])
    try:
        with pytest.raises(DBAPIError), runtime.begin() as db:
            db.execute(text('SELECT * FROM textbook_contents'))
    finally:
        runtime.dispose()


@pytest.mark.parametrize('direction', ['upgrade', 'downgrade'])
def test_migration_transaction_failure_restores_schema(database, admin_engine, direction):
    if direction == 'upgrade':
        migrate(admin_engine, database, 'downgrade', PREVIOUS)
    config = migration_config()
    with pytest.raises(RuntimeError, match='rollback content'), admin_engine.begin() as db:
        config.attributes.update(connection=db, app_role=database['env']['WIZZY_APP_ROLE'],
                                 worker_role=database['env']['WIZZY_WORKER_ROLE'])
        getattr(command, direction)(config, HEAD if direction == 'upgrade' else PREVIOUS)
        raise RuntimeError('rollback content')
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == (PREVIOUS if direction == 'upgrade' else HEAD)
        if direction == 'upgrade':
            assert_absent(db)
        else:
            assert db.execute(text("SELECT has_function_privilege(:role,:name,'EXECUTE')"), {'role': database['env']['WIZZY_APP_ROLE'], 'name': CAPABILITY}).scalar_one()
    migrate(admin_engine, database, 'upgrade', HEAD)


def test_content_prevents_destructive_downgrade(client, source, database, admin_engine):
    _, _, item, _ = source
    with pytest.raises(RuntimeError, match='Cannot discard textbook content or publication history'):
        migrate(admin_engine, database, 'downgrade', PREVIOUS)
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == HEAD
        assert db.execute(text('SELECT id::text FROM textbook_contents')).scalar_one() == item['id']


def test_audit_alone_prevents_downgrade(client, platform, database, admin_engine):
    with admin_engine.begin() as db:
        db.execute(text("INSERT INTO platform_audit_events(id,actor_id,action,request_id) VALUES(gen_random_uuid(),:actor,'textbooks.content.create','document-audit-only')"), {'actor': platform['id']})
    with pytest.raises(RuntimeError, match='Cannot discard textbook content or publication history'):
        migrate(admin_engine, database, 'downgrade', PREVIOUS)
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == HEAD
