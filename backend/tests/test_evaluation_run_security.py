"""Run imports remain atomic, immutable and restricted to real platform sessions."""
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from alembic import command as alembic_command
from conftest import MIGRATION_HEAD
from conftest import database as session_database
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_evaluation_runs import BASE, import_body, manifest, ok, run
from test_evaluation_runs import corpus as corpus  # noqa: F401
from test_evaluation_runs import evaluators as evaluators  # noqa: F401
from test_evaluation_runs import execution as execution  # noqa: F401
from test_evaluation_runs import platform as platform  # noqa: F401
from test_evaluation_runs import readable_result as readable_result  # noqa: F401
from test_evaluation_runs import result_context as result_context  # noqa: F401
from test_evaluation_runs import source as source  # noqa: F401
from test_question_migration import migrate

from wizzy.config import migration_config
from wizzy.security import digest

PREVIOUS = '0029_evaluation_datasets'
HEAD = MIGRATION_HEAD
TABLES = ('evaluation_runs', 'evaluation_run_predictions', 'evaluation_run_operations')
REQUEST = 'public.evaluation_run_request(text,text,text,jsonb,uuid,integer,integer)'
HELPERS = ('public.evaluation_run_valid(text,jsonb)', 'public.evaluation_run_usable(uuid)',
           'public.evaluation_run_view(uuid)', 'public.evaluation_run_immutable()')


@pytest.fixture
def database(tmp_path_factory):
    yield from session_database.__wrapped__(tmp_path_factory)


def sql(db, headers, operation, payload, item=None):
    return db.execute(text('''SELECT public.evaluation_run_request(:hash,:op,'run-security',
        CAST(:payload AS jsonb),:id,50,0)'''), {
        'hash': digest(headers['Authorization'].split()[1]), 'op': operation,
        'payload': json.dumps(payload), 'id': item['id'] if item else None}).scalar_one()


def absent(db):
    for table in TABLES:
        assert db.execute(text('SELECT to_regclass(:name)'), {'name': 'public.'+table}).scalar_one() is None
    for function in (REQUEST, *HELPERS):
        assert db.execute(text('SELECT to_regprocedure(:name)'), {'name': function}).scalar_one() is None
    assert not db.execute(text("SELECT EXISTS(SELECT 1 FROM platform_permissions WHERE name='platform.evaluation.run')")).scalar_one()


def test_empty_migration_round_trip_and_capabilities(database, admin_engine):
    migrate(admin_engine, database, 'downgrade', PREVIOUS)
    with admin_engine.connect() as db:
        absent(db)
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
        for function in (REQUEST, *HELPERS):
            definition = db.execute(text('SELECT prosecdef,proconfig FROM pg_proc WHERE oid=to_regprocedure(:name)'),
                                    {'name': function}).one()
            assert definition.prosecdef == (function in (REQUEST, HELPERS[1], HELPERS[2]))
            assert definition.proconfig == ['search_path=pg_catalog, pg_temp']
            assert not db.execute(text('''SELECT EXISTS(SELECT 1 FROM pg_proc p,LATERAL aclexplode(p.proacl) a
                WHERE p.oid=to_regprocedure(:name) AND a.grantee=0 AND a.privilege_type='EXECUTE')'''),
                {'name': function}).scalar_one()
            for key in ('WIZZY_APP_ROLE', 'WIZZY_WORKER_ROLE', 'WIZZY_DISPATCHER_ROLE'):
                assert db.execute(text("SELECT has_function_privilege(:role,:name,'EXECUTE')"), {
                    'role': database['env'][key], 'name': function}).scalar_one() == (
                        key == 'WIZZY_APP_ROLE' and function == REQUEST)
    runtime = create_engine(database['url'])
    try:
        for table in TABLES:
            with pytest.raises(DBAPIError), runtime.begin() as db:
                db.execute(text('SELECT * FROM public.'+table))
        with pytest.raises(DBAPIError), runtime.begin() as db:
            db.execute(text("SELECT public.evaluation_run_valid('create','{}'::jsonb)"))
    finally:
        runtime.dispose()
    migrate(admin_engine, database, 'downgrade', PREVIOUS)
    with admin_engine.connect() as db:
        absent(db)
    migrate(admin_engine, database, 'upgrade', 'head')


def test_failed_upgrade_is_atomic(database, admin_engine):
    migrate(admin_engine, database, 'downgrade', PREVIOUS)
    with pytest.raises(RuntimeError, match='rollback run migration'), admin_engine.begin() as db:
        config = migration_config()
        config.attributes['connection'] = db
        config.attributes['app_role'] = database['env']['WIZZY_APP_ROLE']
        config.attributes['worker_role'] = database['env']['WIZZY_WORKER_ROLE']
        alembic_command.upgrade(config, 'head')
        raise RuntimeError('rollback run migration')
    with admin_engine.connect() as db:
        absent(db)
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == PREVIOUS
    migrate(admin_engine, database, 'upgrade', 'head')


def test_history_cannot_change_or_be_discarded(client, evaluators, corpus, database, admin_engine):
    headers = evaluators[0]
    item = run(client, headers, corpus)
    ok(client.post(f"{BASE}/{item['id']}/predictions", headers=headers, json=import_body(item, corpus)))
    for table in TABLES:
        for statement in (f'DELETE FROM {table}', f'UPDATE {table} SET run_id=run_id' if table != 'evaluation_runs'
                          else 'UPDATE evaluation_runs SET manifest=manifest', f'TRUNCATE {table} CASCADE'):
            with pytest.raises(DBAPIError, match='Evaluation run history is immutable'), admin_engine.begin() as db:
                db.execute(text(statement))
    with pytest.raises(RuntimeError, match='Cannot discard evaluation run history'):
        migrate(admin_engine, database, 'downgrade', PREVIOUS)
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == HEAD
    assert client.get(f"{BASE}/{item['id']}/report", headers=headers).status_code == 200


@pytest.mark.parametrize('changes', [
    {'expected_revision': True}, {'expected_revision': 1.5}, {'manifest_digest': 'x'},
    {'source_reference': ' bad'}, {'source_reference': '\n'}, {'predictions': None},
    {'predictions': [{'question_id': str(uuid4()), 'verdict': 'correct', 'automatic_release': 'true'}]},
    {'predictions': [{'question_id': str(uuid4()), 'verdict': 'unsupported', 'automatic_release': True}]},
    {'accuracy': 1},
])
def test_sql_cannot_bypass_http_validation(client, evaluators, corpus, changes):
    headers = evaluators[0]
    item = run(client, headers, corpus)
    payload = {**import_body(item, corpus), **changes}
    with client.app.state.engine.begin() as db:
        assert sql(db, headers, 'import', payload, item)['status'] == 422
    assert ok(client.get(f"{BASE}/{item['id']}", headers=headers), 200)['revision'] == 1


@pytest.mark.parametrize('operation', ['create', 'import'])
def test_audit_failure_rolls_back_every_write(app, client, evaluators, corpus, admin_engine, operation):
    headers = evaluators[0]
    item = run(client, headers, corpus) if operation == 'import' else None
    payload = import_body(item, corpus) if item else manifest(corpus)
    url = f"{BASE}/{item['id']}/predictions" if item else BASE
    with admin_engine.begin() as db:
        db.execute(text('''CREATE FUNCTION fail_run_audit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.action=:action THEN RAISE EXCEPTION 'test audit failure';
            END IF; RETURN NEW; END $$;
            CREATE TRIGGER fail_run_audit BEFORE INSERT ON platform_audit_events
            FOR EACH ROW EXECUTE FUNCTION fail_run_audit();'''.replace(':action', "'evaluation.runs."+operation+"'")))
    try:
        with TestClient(app, raise_server_exceptions=False) as failing:
            assert failing.post(url, headers=headers, json=payload).status_code == 500
        with admin_engine.connect() as db:
            assert not db.execute(text('SELECT EXISTS(SELECT 1 FROM evaluation_run_operations WHERE operation_id=:id)'),
                                  {'id': payload['operation_id']}).scalar_one()
            if item:
                assert db.execute(text('SELECT revision FROM evaluation_runs WHERE id=:id'),
                                  {'id': item['id']}).scalar_one() == 1
                assert db.execute(text('SELECT count(*) FROM evaluation_run_predictions WHERE run_id=:id'),
                                  {'id': item['id']}).scalar_one() == 0
    finally:
        with admin_engine.begin() as db:
            db.execute(text('DROP TRIGGER fail_run_audit ON platform_audit_events; DROP FUNCTION fail_run_audit()'))
    ok(client.post(url, headers=headers, json=payload))


@pytest.mark.parametrize('same_operation', [True, False])
def test_concurrent_import_has_one_prediction_set(app, client, evaluators, corpus, admin_engine, same_operation):
    headers = evaluators[0]
    item = run(client, headers, corpus)
    payload = import_body(item, corpus)
    bodies = [payload, payload if same_operation else {**payload, 'operation_id': str(uuid4())}]

    def import_predictions(body):
        with TestClient(app, client=(str(uuid4()), 50000)) as caller:
            return caller.post(f"{BASE}/{item['id']}/predictions", headers=headers, json=body)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(import_predictions, bodies))
    assert sorted(r.status_code for r in responses) == ([200, 201] if same_operation else [201, 409])
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM evaluation_run_predictions WHERE run_id=:id'),
                          {'id': item['id']}).scalar_one() == 1
        assert db.execute(text('SELECT count(*) FROM evaluation_run_operations WHERE run_id=:id'),
                          {'id': item['id']}).scalar_one() == 2
