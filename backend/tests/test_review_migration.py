"""Review migration safety and least privilege on disposable PostgreSQL databases."""
import pytest
from alembic import command
from conftest import MIGRATION_HEAD
from conftest import database as session_database
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_correction_attempts import wrong_question as wrong_question  # noqa: F401
from test_execution import execution as execution  # noqa: F401
from test_question_migration import migrate
from test_result_read import readable_result as readable_result  # noqa: F401
from test_results import result_context as result_context  # noqa: F401
from test_review_workbench import attempt, enroll, reschedule, task_path

from wizzy.config import migration_config

TABLES = ('review_tasks', 'review_attempts', 'review_reschedules')
FUNCTIONS = (
    'read_review_task(uuid)', 'create_review_task(uuid,bigint)',
    'read_student_review_tasks(uuid,integer,timestamptz,uuid,boolean)',
    'save_review_draft(uuid,integer,text)', 'submit_review_attempt(uuid,integer,text,uuid)',
    'reschedule_review_task(uuid,integer,date,text,uuid)',
    'review_task_view(uuid)', 'lock_review_task(uuid)',
)


@pytest.fixture
def database(tmp_path_factory):
    # Migration tests must not downgrade the database shared by other test modules.
    yield from session_database.__wrapped__(tmp_path_factory)


def test_empty_round_trip_and_restricted_capabilities(database, admin_engine):
    migrate(admin_engine, database, 'downgrade', '0012_correction_attempts')
    with admin_engine.connect() as db:
        for table in TABLES:
            assert db.execute(text('SELECT to_regclass(:table)'), {'table': 'public.'+table}).scalar_one() is None
    migrate(admin_engine, database, 'upgrade', 'head')
    migrate(admin_engine, database, 'upgrade', 'head')
    with admin_engine.connect() as db:
        for table in TABLES:
            assert db.execute(text('SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid=to_regclass(:table)'),
                              {'table': 'public.'+table}).scalar_one()
        for role in ('WIZZY_APP_ROLE', 'WIZZY_WORKER_ROLE', 'WIZZY_DISPATCHER_ROLE'):
            name = database['env'][role]
            for function in FUNCTIONS:
                assert db.execute(text("SELECT has_function_privilege(:role,:function,'EXECUTE')"),
                                  {'role': name, 'function': 'public.'+function}).scalar_one() == (
                                      role == 'WIZZY_APP_ROLE' and function in FUNCTIONS[:6])
            for table in TABLES:
                assert not db.execute(text("SELECT has_table_privilege(:role,:table,'SELECT,INSERT,UPDATE,DELETE')"),
                                      {'role': name, 'table': 'public.'+table}).scalar_one()
        for function in FUNCTIONS:
            definition = db.execute(text('SELECT prosecdef,proconfig FROM pg_proc WHERE oid=to_regprocedure(:function)'),
                                    {'function': 'public.'+function}).one()
            assert definition.prosecdef and definition.proconfig == ['search_path=pg_catalog, pg_temp']


@pytest.mark.parametrize('history', ['task', 'attempt', 'reschedule'])
def test_history_prevents_destructive_downgrade(wrong_question, client, owner, student, admin_engine, database, history):
    from test_result_read import child_session

    headers, qid = owner['headers'], wrong_question['question_id']
    task = enroll(client, headers, qid).json()['data']
    if history == 'attempt':
        response = attempt(client, child_session(client, owner, student['id']), task['id'])
        assert response.status_code == 201, response.text
        task = response.json()['data']
    if history == 'reschedule':
        response = reschedule(client, headers, task['id'])
        assert response.status_code == 201, response.text
        task = response.json()['data']
    with pytest.raises(DBAPIError, match='Cannot discard review task, attempt or reschedule history'):
        migrate(admin_engine, database, 'downgrade', '0012_correction_attempts')
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == MIGRATION_HEAD
        assert db.execute(text('SELECT count(*) FROM review_tasks')).scalar_one() == 1
        assert db.execute(text('SELECT count(*) FROM review_attempts')).scalar_one() == int(history == 'attempt')
        assert db.execute(text('SELECT count(*) FROM review_reschedules')).scalar_one() == int(history == 'reschedule')
    assert client.get(task_path(task['id']), headers=headers).json()['data'] == task


def test_failed_upgrade_rolls_back_without_changing_question(wrong_question, client, owner, admin_engine, database):
    qid, headers = wrong_question['question_id'], owner['headers']
    before = client.get(f'/api/v1/questions/{qid}', headers=headers).json()['data']
    migrate(admin_engine, database, 'downgrade', '0012_correction_attempts')
    config = migration_config()
    with pytest.raises(RuntimeError, match='rollback review upgrade'), admin_engine.begin() as db:
        config.attributes.update(connection=db, app_role=database['env']['WIZZY_APP_ROLE'],
                                 worker_role=database['env']['WIZZY_WORKER_ROLE'])
        command.upgrade(config, 'head')
        raise RuntimeError('rollback review upgrade')
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0012_correction_attempts'
        for table in TABLES:
            assert db.execute(text('SELECT to_regclass(:table)'), {'table': 'public.'+table}).scalar_one() is None
        for function in FUNCTIONS:
            assert db.execute(text('SELECT to_regprocedure(:function)'), {'function': 'public.'+function}).scalar_one() is None
    assert client.get(f'/api/v1/questions/{qid}', headers=headers).json()['data'] == before
    migrate(admin_engine, database, 'upgrade', 'head')
    migrate(admin_engine, database, 'upgrade', 'head')
    assert client.get(f'/api/v1/questions/{qid}', headers=headers).json()['data'] == before
    task = enroll(client, headers, qid).json()['data']
    assert task['state'] == 'open' and task['attempts'] == [] and task['reschedules'] == []
