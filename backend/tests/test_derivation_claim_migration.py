"""A stale candidate snapshot must not apply a verdict event twice."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from alembic import command
from conftest import MIGRATION_HEAD
from conftest import database as session_database
from sqlalchemy import create_engine, text
from test_execution import execution as execution  # noqa: F401
from test_execution import wait_for_blocker
from test_mistakes import drain, mistakes, tick
from test_question_migration import migrate
from test_question_revisions import questions
from test_result_read import readable_result as readable_result  # noqa: F401
from test_results import result_context as result_context  # noqa: F401
from test_verdict_revisions import override

from wizzy.config import migration_config


@pytest.fixture
def database(tmp_path_factory):
    yield from session_database.__wrapped__(tmp_path_factory)


def test_stale_candidate_after_other_consumer_commits_is_idle(
    readable_result, client, owner, student, database, admin_engine,
):
    drain(database)
    q = questions(client, owner, readable_result[3])[0]
    assert override(client, owner['headers'], q['question_id'], verdict='incorrect').status_code == 201
    # Pause only the late consumer after NOT EXISTS has selected its candidate,
    # but before FOR UPDATE locks it. No receipt/projection logic is replaced.
    with admin_engine.begin() as db:
        original = db.execute(text("SELECT pg_get_functiondef('public.consume_question_derivation()'::regprocedure)")).scalar_one()
        anchor = 'ORDER BY e.created_at,e.id FOR UPDATE OF e SKIP LOCKED LIMIT 1;'
        assert original.count(anchor) == 1
        db.execute(text('''CREATE FUNCTION public.test_candidate_barrier(target uuid) RETURNS uuid
            LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog,pg_temp AS $$
            BEGIN
                IF current_setting('application_name')='test-late-derivation' THEN
                    PERFORM pg_advisory_xact_lock(20261026,19);
                END IF;
                RETURN target;
            END $$'''))
        db.execute(text(original.replace(anchor,
            'ORDER BY e.created_at,public.test_candidate_barrier(e.id) FOR UPDATE OF e SKIP LOCKED LIMIT 1;')))
    late_engine = create_engine(database['worker_url'], hide_parameters=True,
                                connect_args={'application_name': 'test-late-derivation'})

    def late_tick():
        from wizzy.learning import LearningConsumer

        return LearningConsumer(late_engine).tick()

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with admin_engine.begin() as blocker:
                pid = blocker.execute(text('SELECT pg_backend_pid()')).scalar_one()
                blocker.execute(text('SELECT pg_advisory_xact_lock(20261026,19)'))
                future = pool.submit(late_tick)
                wait_for_blocker(admin_engine, pid)
                assert tick(database) == 'applied'
            assert future.result(timeout=10) == 'idle'
        body = mistakes(client, owner['headers'], student['id']).json()['data']
        assert body['pending_updates'] == 0 and len(body['items']) == 1
        with admin_engine.connect() as db:
            assert db.execute(text('SELECT count(*) FROM question_derivation_receipts WHERE question_id=:id'),
                              {'id': q['question_id']}).scalar_one() == 1
    finally:
        late_engine.dispose()
        with admin_engine.begin() as db:
            db.execute(text(original))
            db.execute(text('DROP FUNCTION public.test_candidate_barrier(uuid)'))


CAPABILITY = 'public.consume_question_derivation()'


def function_state(db):
    return db.execute(text("""SELECT p.proowner,p.proacl::text,p.prosecdef,p.proconfig,p.prosrc
        FROM pg_proc p WHERE p.oid=to_regprocedure(:name)"""), {'name': CAPABILITY}).one()


def projection_state(db):
    return [db.execute(text('SELECT row_to_json(t) FROM public.'+table+' t ORDER BY question_id')).all()
            for table in ('question_derivation_receipts', 'mistake_projections')]


def assert_private_capability(db, database):
    state = function_state(db)
    assert state.prosecdef is True
    assert state.proconfig == ['search_path=pg_catalog, pg_temp']
    assert not db.execute(text("""SELECT EXISTS(SELECT 1 FROM pg_proc p,LATERAL aclexplode(p.proacl) a
        WHERE p.oid=to_regprocedure(:name) AND a.grantee=0 AND a.privilege_type='EXECUTE')"""),
        {'name': CAPABILITY}).scalar_one()
    for key in ('WIZZY_APP_ROLE', 'WIZZY_WORKER_ROLE', 'WIZZY_DISPATCHER_ROLE'):
        assert db.execute(text("SELECT has_function_privilege(:role,:name,'EXECUTE')"), {
            'role': database['env'][key], 'name': CAPABILITY}).scalar_one() == (key == 'WIZZY_WORKER_ROLE')
        for table in ('question_derivation_receipts', 'mistake_projections'):
            assert db.execute(text("""SELECT relrowsecurity AND relforcerowsecurity
                FROM pg_class WHERE oid=to_regclass(:name)"""), {'name': 'public.'+table}).scalar_one()
            for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE'):
                assert not db.execute(text('SELECT has_table_privilege(:role,:name,:privilege)'), {
                    'role': database['env'][key], 'name': 'public.'+table,
                    'privilege': privilege}).scalar_one()


def test_round_trip_preserves_processed_state_and_worker_only_capability(
    readable_result, client, owner, student, database, admin_engine,
):
    drain(database)
    q = questions(client, owner, readable_result[3])[0]
    assert override(client, owner['headers'], q['question_id'], verdict='incorrect').status_code == 201
    drain(database)
    with admin_engine.connect() as db:
        guarded = function_state(db)
        before = projection_state(db)
        assert before[0] and before[1]
        assert_private_capability(db, database)
    migrate(admin_engine, database, 'downgrade', '0018_model_catalog')
    # Check the restored body against the historical migration, not the new replacement logic.
    source = (Path(__file__).resolve().parents[1] / 'migrations/versions/0011_mistake_projection.py').read_text()
    historical_body = source.split('CREATE FUNCTION public.consume_question_derivation()', 1)[1].split('AS $$', 1)[1].split('$$;', 1)[0]
    with admin_engine.connect() as db:
        old = function_state(db)
        assert old.prosrc.strip() == historical_body.strip()
        assert old[:4] == guarded[:4]
        assert projection_state(db) == before
        assert_private_capability(db, database)
    migrate(admin_engine, database, 'upgrade', 'head')
    migrate(admin_engine, database, 'upgrade', 'head')
    with admin_engine.connect() as db:
        assert function_state(db) == guarded
        assert projection_state(db) == before
        assert_private_capability(db, database)
    assert tick(database) == 'idle'
    assert len(mistakes(client, owner['headers'], student['id']).json()['data']['items']) == 1
    assert override(client, owner['headers'], q['question_id'], verdict='correct',
                    expected_verdict_revision=1).status_code == 201
    assert tick(database) == 'applied'
    assert mistakes(client, owner['headers'], student['id']).json()['data']['items'] == []


@pytest.mark.parametrize('direction,target,initial', [
    ('upgrade', 'head', '0018_model_catalog'),
    ('downgrade', '0018_model_catalog', MIGRATION_HEAD),
])
def test_failed_migration_restores_function_acl_and_version(database, admin_engine, direction, target, initial):
    if direction == 'upgrade':
        migrate(admin_engine, database, 'downgrade', initial)
    with admin_engine.connect() as db:
        before = function_state(db)
    config = migration_config()
    with pytest.raises(RuntimeError, match='rollback claim guard'), admin_engine.begin() as db:
        config.attributes.update(connection=db, app_role=database['env']['WIZZY_APP_ROLE'],
                                 worker_role=database['env']['WIZZY_WORKER_ROLE'])
        getattr(command, direction)(config, target)
        assert function_state(db).prosrc != before.prosrc
        raise RuntimeError('rollback claim guard')
    with admin_engine.connect() as db:
        assert function_state(db) == before
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == initial
        assert_private_capability(db, database)
