"""Timetable schema safety on disposable databases, never the shared development DB."""
import pytest
from alembic import command
from conftest import database as session_database
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_question_migration import migrate
from test_timetables import create_term, override, save, slot, term_path

from wizzy.config import migration_config

TABLES = ('terms', 'timetable_versions', 'timetable_slots', 'lesson_overrides')
FUNCTION = 'public.timetable_access(uuid,uuid,bigint,boolean)'


@pytest.fixture
def database(tmp_path_factory):
    # Each case owns its schema so downgrade cannot affect other test modules.
    yield from session_database.__wrapped__(tmp_path_factory)


def test_empty_round_trip_and_least_privilege(database, admin_engine):
    migrate(admin_engine, database, 'downgrade', '0013_review_workbench')
    with admin_engine.connect() as db:
        for table in TABLES:
            assert db.execute(text('SELECT to_regclass(:table)'), {'table': 'public.'+table}).scalar_one() is None
        assert db.execute(text('SELECT to_regprocedure(:function)'), {'function': FUNCTION}).scalar_one() is None
    migrate(admin_engine, database, 'upgrade', 'head')
    migrate(admin_engine, database, 'upgrade', 'head')
    with admin_engine.connect() as db:
        for table in TABLES:
            assert db.execute(text('SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid=to_regclass(:table)'),
                              {'table': 'public.'+table}).scalar_one()
        for role in ('WIZZY_APP_ROLE', 'WIZZY_WORKER_ROLE', 'WIZZY_DISPATCHER_ROLE'):
            name = database['env'][role]
            app = role == 'WIZZY_APP_ROLE'
            assert db.execute(text("SELECT has_function_privilege(:role,:function,'EXECUTE')"),
                              {'role': name, 'function': FUNCTION}).scalar_one() == app
            for table in TABLES:
                for privilege in ('SELECT', 'INSERT'):
                    assert db.execute(text('SELECT has_table_privilege(:role,:table,:privilege)'),
                                      {'role': name, 'table': 'public.'+table, 'privilege': privilege}).scalar_one() == app
                assert not db.execute(text("SELECT has_table_privilege(:role,:table,'UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')"),
                                      {'role': name, 'table': 'public.'+table}).scalar_one()
                if table == 'terms':
                    for column in ('revision', 'template_revision', 'name', 'student_id', 'deletion_epoch'):
                        assert db.execute(text("SELECT has_column_privilege(:role,'public.terms',:column,'UPDATE')"),
                                          {'role': name, 'column': column}).scalar_one() == (
                                              app and column in ('revision', 'template_revision'))
        definition = db.execute(text('SELECT prosecdef,proconfig,proacl FROM pg_proc WHERE oid=to_regprocedure(:function)'),
                                {'function': FUNCTION}).one()
        assert definition.prosecdef and definition.proconfig == ['search_path=pg_catalog, pg_temp']
        assert not db.execute(text('''SELECT EXISTS (SELECT 1 FROM pg_proc p,
            LATERAL aclexplode(p.proacl) a WHERE p.oid=to_regprocedure(:function)
            AND a.grantee=0 AND a.privilege_type='EXECUTE')'''), {'function': FUNCTION}).scalar_one()


@pytest.mark.parametrize('history', ['term', 'template', 'override'])
def test_history_blocks_destructive_downgrade(client, owner, student, database, admin_engine, history):
    headers = owner['headers']
    response = create_term(client, headers, student['id'])
    assert response.status_code == 201, response.text
    tid, original = response.json()['data']['id'], slot()
    if history in ('template', 'override'):
        assert save(client, headers, tid, [original]).status_code == 200
    if history == 'override':
        assert override(client, headers, tid, original).status_code == 201
    before = client.get(term_path(tid), headers=headers).json()['data']
    with pytest.raises(DBAPIError, match='Cannot discard timetable or lesson override history'):
        migrate(admin_engine, database, 'downgrade', '0013_review_workbench')
    with admin_engine.connect() as db:
        from alembic.script import ScriptDirectory
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == ScriptDirectory.from_config(migration_config()).get_current_head()
        assert db.execute(text('SELECT count(*) FROM terms')).scalar_one() == 1
    assert client.get(term_path(tid), headers=headers).json()['data'] == before


def test_failed_upgrade_is_atomic(client, owner, student, database, admin_engine):
    migrate(admin_engine, database, 'downgrade', '0013_review_workbench')
    config = migration_config()
    with pytest.raises(RuntimeError, match='rollback timetable upgrade'), admin_engine.begin() as db:
        config.attributes.update(connection=db, app_role=database['env']['WIZZY_APP_ROLE'],
                                 worker_role=database['env']['WIZZY_WORKER_ROLE'])
        command.upgrade(config, 'head')
        raise RuntimeError('rollback timetable upgrade')
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0013_review_workbench'
        for table in TABLES:
            assert db.execute(text('SELECT to_regclass(:table)'), {'table': 'public.'+table}).scalar_one() is None
        assert db.execute(text('SELECT to_regprocedure(:function)'), {'function': FUNCTION}).scalar_one() is None
    migrate(admin_engine, database, 'upgrade', 'head')
    response = create_term(client, owner['headers'], student['id'])
    assert response.status_code == 201, response.text
