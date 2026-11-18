"""Shared-plan migration, ACL and rollback on disposable PostgreSQL databases."""
import pytest
from alembic import command
from conftest import MIGRATION_HEAD
from conftest import database as session_database
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_execution import execution as execution  # noqa: F401
from test_learning_reports import report_result as report_result  # noqa: F401
from test_question_migration import migrate
from test_result_read import readable_result as readable_result  # noqa: F401
from test_results import result_context as result_context  # noqa: F401
from test_review_plans import generate
from test_review_plans import plan_tasks as plan_tasks  # noqa: F401

from wizzy.config import migration_config

TABLES = ('review_plans', 'review_plan_entries')
CAPABILITIES = ('generate_review_plan(uuid,text,date,integer,integer,integer,uuid,uuid)',
                'read_review_plan(uuid)', 'read_review_plans(uuid,text,integer,timestamptz,uuid)')
HELPERS = ('lock_plan_student(uuid)', 'review_plan_view(uuid)')


@pytest.fixture
def database(tmp_path_factory):
    yield from session_database.__wrapped__(tmp_path_factory)


def test_empty_round_trip_and_restricted_capabilities(database, admin_engine):
    migrate(admin_engine, database, 'downgrade', '0016_learning_reports')
    with admin_engine.connect() as db:
        for table in TABLES:
            assert db.execute(text('SELECT to_regclass(:name)'), {'name': 'public.'+table}).scalar_one() is None
        for function in (*CAPABILITIES, *HELPERS):
            assert db.execute(text('SELECT to_regprocedure(:name)'), {'name': 'public.'+function}).scalar_one() is None
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
        for function in (*CAPABILITIES, *HELPERS):
            name = 'public.'+function
            definition = db.execute(text('SELECT prosecdef,proconfig FROM pg_proc WHERE oid=to_regprocedure(:name)'),
                                    {'name': name}).one()
            assert definition.prosecdef and definition.proconfig == ['search_path=pg_catalog, pg_temp']
            assert not db.execute(text('''SELECT EXISTS(SELECT 1 FROM pg_proc p,LATERAL aclexplode(p.proacl) a
                WHERE p.oid=to_regprocedure(:name) AND a.grantee=0 AND a.privilege_type='EXECUTE')'''), {'name': name}).scalar_one()
            for key in ('WIZZY_APP_ROLE', 'WIZZY_WORKER_ROLE', 'WIZZY_DISPATCHER_ROLE'):
                assert db.execute(text("SELECT has_function_privilege(:role,:name,'EXECUTE')"), {
                    'role': database['env'][key], 'name': name}).scalar_one() == (
                        key == 'WIZZY_APP_ROLE' and function in CAPABILITIES)


@pytest.mark.parametrize('with_entries', [False, True])
def test_plan_history_blocks_downgrade(request, client, owner, student, admin_engine, database, with_entries):
    if with_entries:
        request.getfixturevalue('plan_tasks')
    response = generate(client, owner['headers'], student['id'])
    assert response.status_code == 201, response.text
    report = response.json()['data']
    assert bool(report['question_count']) is with_entries
    with pytest.raises(DBAPIError, match='Cannot discard review plan history'):
        migrate(admin_engine, database, 'downgrade', '0016_learning_reports')
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == MIGRATION_HEAD
        assert db.execute(text('SELECT count(*) FROM review_plans')).scalar_one() == 1
        assert db.execute(text('SELECT count(*) FROM review_plan_entries')).scalar_one() == report['question_count']
    assert client.get('/api/v1/review-plans/'+report['id'], headers=owner['headers']).json()['data'] == report


def test_failed_upgrade_is_atomic(database, admin_engine):
    migrate(admin_engine, database, 'downgrade', '0016_learning_reports')
    config = migration_config()
    with pytest.raises(RuntimeError, match='rollback plan upgrade'), admin_engine.begin() as db:
        config.attributes.update(connection=db, app_role=database['env']['WIZZY_APP_ROLE'],
                                 worker_role=database['env']['WIZZY_WORKER_ROLE'])
        command.upgrade(config, 'head')
        raise RuntimeError('rollback plan upgrade')
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0016_learning_reports'
        for table in TABLES:
            assert db.execute(text('SELECT to_regclass(:name)'), {'name': 'public.'+table}).scalar_one() is None
        for function in (*CAPABILITIES, *HELPERS):
            assert db.execute(text('SELECT to_regprocedure(:name)'), {'name': 'public.'+function}).scalar_one() is None
    migrate(admin_engine, database, 'upgrade', 'head')
