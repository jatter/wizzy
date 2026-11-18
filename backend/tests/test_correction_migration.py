"""Correction migration safety in disposable PostgreSQL databases."""
import pytest
from alembic import command
from conftest import MIGRATION_HEAD
from conftest import database as session_database
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_correction_attempts import judge, submit
from test_correction_attempts import wrong_question as wrong_question  # noqa: F401
from test_execution import execution as execution  # noqa: F401
from test_question_migration import migrate
from test_result_read import readable_result as readable_result  # noqa: F401
from test_results import result_context as result_context  # noqa: F401

from wizzy.config import migration_config


@pytest.fixture
def database(tmp_path_factory):
    yield from session_database.__wrapped__(tmp_path_factory)


def test_empty_downgrade_upgrade_and_capabilities(database, admin_engine):
    migrate(admin_engine, database, 'downgrade', '0011_mistake_projection')
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT to_regclass('public.correction_attempts')")).scalar_one() is None
    migrate(admin_engine, database, 'upgrade', 'head')
    migrate(admin_engine, database, 'upgrade', 'head')
    with admin_engine.connect() as db:
        for table in ('correction_attempts', 'correction_judgments'):
            assert db.execute(text('SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid=to_regclass(:table)'),
                              {'table': table}).scalar_one()
        functions = ('read_correction_attempts(uuid,integer,timestamptz,uuid)',
                     'submit_correction_attempt(uuid,bigint,text,uuid,uuid)',
                     'judge_correction_attempt(uuid,uuid,integer,text,text,uuid)',
                     'lock_correction_question(uuid)', 'correction_attempt_view(uuid)')
        for role in ('WIZZY_APP_ROLE', 'WIZZY_WORKER_ROLE', 'WIZZY_DISPATCHER_ROLE'):
            name = database['env'][role]
            for function in functions:
                allowed = role == 'WIZZY_APP_ROLE' and function in functions[:3]
                assert db.execute(text("SELECT has_function_privilege(:role,:function,'EXECUTE')"),
                                  {'role': name, 'function': 'public.'+function}).scalar_one() == allowed
            for table in ('correction_attempts', 'correction_judgments'):
                assert not db.execute(text("SELECT has_table_privilege(:role,:table,'SELECT,INSERT,UPDATE,DELETE')"),
                                      {'role': name, 'table': table}).scalar_one()
        rows = db.execute(text("""SELECT prosecdef,proconfig FROM pg_proc WHERE pronamespace='public'::regnamespace
            AND proname IN ('read_correction_attempts','submit_correction_attempt','judge_correction_attempt',
                            'lock_correction_question','correction_attempt_view')""")).all()
        assert len(rows) == 5
        assert all(row.prosecdef and row.proconfig == ['search_path=pg_catalog, pg_temp'] for row in rows)


@pytest.mark.parametrize('judged', [False, True])
def test_downgrade_refuses_history(wrong_question, client, owner, admin_engine, database, judged):
    qid, h = wrong_question['question_id'], owner['headers']
    attempt = submit(client, h, qid).json()['data']
    if judged:
        attempt = judge(client, h, qid, attempt['id']).json()['data']
    with pytest.raises(DBAPIError, match='Cannot discard correction attempt or judgment history'):
        migrate(admin_engine, database, 'downgrade', '0011_mistake_projection')
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == MIGRATION_HEAD
        assert db.execute(text('SELECT count(*) FROM correction_attempts')).scalar_one() == 1
        assert db.execute(text('SELECT count(*) FROM correction_judgments')).scalar_one() == int(judged)
    assert client.get(f'/api/v1/questions/{qid}/correction-attempts', headers=h).json()['data']['items'] == [attempt]


def test_failed_upgrade_rolls_back_without_changing_source(wrong_question, client, owner, admin_engine, database):
    qid, headers = wrong_question['question_id'], owner['headers']
    before = client.get(f'/api/v1/questions/{qid}', headers=headers).json()['data']
    migrate(admin_engine, database, 'downgrade', '0011_mistake_projection')
    config = migration_config()
    with pytest.raises(RuntimeError, match='rollback correction upgrade'), admin_engine.begin() as db:
        config.attributes.update(connection=db, app_role=database['env']['WIZZY_APP_ROLE'],
                                 worker_role=database['env']['WIZZY_WORKER_ROLE'])
        command.upgrade(config, 'head')
        raise RuntimeError('rollback correction upgrade')
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0011_mistake_projection'
        for table in ('correction_attempts', 'correction_judgments'):
            assert db.execute(text('SELECT to_regclass(:table)'), {'table': 'public.'+table}).scalar_one() is None
        assert db.execute(text("SELECT to_regprocedure('public.submit_correction_attempt(uuid,bigint,text,uuid,uuid)')")).scalar_one() is None
    assert client.get(f'/api/v1/questions/{qid}', headers=headers).json()['data'] == before
    migrate(admin_engine, database, 'upgrade', 'head')
    migrate(admin_engine, database, 'upgrade', 'head')
    assert client.get(f'/api/v1/questions/{qid}', headers=headers).json()['data'] == before
    assert client.get(f'/api/v1/questions/{qid}/correction-attempts', headers=headers).json()['data']['items'] == []
