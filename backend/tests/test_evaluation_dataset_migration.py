"""Evaluation corpora stay private, append-only and safe to migrate on PostgreSQL."""
import pytest
from alembic import command
from conftest import MIGRATION_HEAD
from conftest import database as session_database
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_account_governance import actors as actors  # noqa: F401
from test_account_governance import body, change
from test_evaluation_datasets import create
from test_evaluation_datasets import evaluators as evaluators  # noqa: F401
from test_evaluation_datasets import platform as platform  # noqa: F401
from test_question_migration import migrate

from wizzy.config import migration_config

PREVIOUS = '0028_textbook_content'
HEAD = MIGRATION_HEAD
TABLES = ('evaluation_datasets', 'evaluation_samples', 'evaluation_annotations',
          'evaluation_adjudications', 'evaluation_dataset_operations')
REQUEST = 'public.evaluation_dataset_request(text,text,text,jsonb,uuid,uuid,integer,integer)'
HELPERS = ('public.evaluation_dataset_valid(text,jsonb)', 'public.evaluation_gold_valid(jsonb)',
           'public.evaluation_source_active(uuid,uuid,integer,text)',
           'public.evaluation_dataset_view(uuid,uuid,integer,integer)',
           'public.evaluation_dataset_immutable()')
GOVERNANCE = 'public.platform_account_governance(text,text,text,uuid,jsonb,integer,timestamptz,uuid)'


@pytest.fixture
def database(tmp_path_factory):
    yield from session_database.__wrapped__(tmp_path_factory)


def absent(db):
    for table in TABLES:
        assert db.execute(text('SELECT to_regclass(:name)'), {'name': 'public.'+table}).scalar_one() is None
    for function in (REQUEST, *HELPERS):
        assert db.execute(text('SELECT to_regprocedure(:name)'), {'name': function}).scalar_one() is None
    assert not db.execute(text("SELECT EXISTS(SELECT 1 FROM platform_roles WHERE name LIKE 'evaluation_%')")).scalar_one()
    assert 'evaluation_annotator' not in db.execute(text('SELECT pg_get_functiondef(to_regprocedure(:name))'),
                                                  {'name': GOVERNANCE}).scalar_one()


def test_empty_round_trip_and_restricted_capabilities(database, admin_engine):
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
        for function in (REQUEST, *HELPERS, GOVERNANCE):
            definition = db.execute(text('SELECT prosecdef,proconfig FROM pg_proc WHERE oid=to_regprocedure(:name)'),
                                    {'name': function}).one()
            assert definition.prosecdef == (function in (REQUEST, *HELPERS[2:4], GOVERNANCE))
            assert definition.proconfig == ['search_path=pg_catalog, pg_temp']
            assert not db.execute(text('''SELECT EXISTS(SELECT 1 FROM pg_proc p,LATERAL aclexplode(p.proacl) a
                WHERE p.oid=to_regprocedure(:name) AND a.grantee=0 AND a.privilege_type='EXECUTE')'''),
                {'name': function}).scalar_one()
            for key in ('WIZZY_APP_ROLE', 'WIZZY_WORKER_ROLE', 'WIZZY_DISPATCHER_ROLE'):
                assert db.execute(text("SELECT has_function_privilege(:role,:name,'EXECUTE')"), {
                    'role': database['env'][key], 'name': function}).scalar_one() == (
                        key == 'WIZZY_APP_ROLE' and function in (REQUEST, GOVERNANCE))
    runtime = create_engine(database['url'])
    try:
        for table in TABLES:
            with pytest.raises(DBAPIError), runtime.begin() as db:
                db.execute(text('SELECT * FROM public.'+table))
        with pytest.raises(DBAPIError), runtime.begin() as db:
            db.execute(text("SELECT public.evaluation_gold_valid('{}'::jsonb)"))
    finally:
        runtime.dispose()
    migrate(admin_engine, database, 'downgrade', PREVIOUS)
    with admin_engine.connect() as db:
        absent(db)
    migrate(admin_engine, database, 'upgrade', 'head')


def test_failed_upgrade_rolls_back_schema_roles_and_version(database, admin_engine):
    migrate(admin_engine, database, 'downgrade', PREVIOUS)
    with pytest.raises(RuntimeError, match='rollback evaluation corpus'), admin_engine.begin() as db:
        config = migration_config()
        config.attributes.update(connection=db, app_role=database['env']['WIZZY_APP_ROLE'],
                                 worker_role=database['env']['WIZZY_WORKER_ROLE'])
        command.upgrade(config, 'head')
        raise RuntimeError('rollback evaluation corpus')
    with admin_engine.connect() as db:
        absent(db)
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == PREVIOUS
    migrate(admin_engine, database, 'upgrade', 'head')


def test_corpus_history_cannot_be_rewritten_or_downgraded(client, evaluators, database, admin_engine):
    dataset = create(client, evaluators[0])
    for statement in ('DELETE FROM evaluation_datasets WHERE id=:id',):
        with pytest.raises(DBAPIError, match='Evaluation history and locked corpora are immutable'), admin_engine.begin() as db:
            db.execute(text(statement), {'id': dataset['id']})
    for table in TABLES:
        with pytest.raises(DBAPIError, match='Evaluation history and locked corpora are immutable'), admin_engine.begin() as db:
            db.execute(text('TRUNCATE public.'+table+' CASCADE'))
    with pytest.raises(RuntimeError, match='Cannot discard evaluation history or evaluator accounts'):
        migrate(admin_engine, database, 'downgrade', PREVIOUS)
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == HEAD
        assert db.execute(text('SELECT name FROM evaluation_datasets WHERE id=:id'),
                          {'id': dataset['id']}).scalar_one() == dataset['name']


@pytest.mark.parametrize('evidence', ['audit', 'account', 'role_history'])
def test_each_evaluator_evidence_blocks_downgrade(client, actors, database, admin_engine, evidence):
    if evidence == 'audit':
        with admin_engine.begin() as db:
            db.execute(text('''INSERT INTO platform_audit_events(id,actor_id,action,request_id)
                VALUES(gen_random_uuid(),:actor,'evaluation.datasets.create','migration-audit-only')'''),
                {'actor': actors[0]['id']})
    else:
        assert change(client, actors[0], actors[3], body(role='evaluation_annotator', status='active')).status_code == 201
        if evidence == 'role_history':
            assert change(client, actors[0], actors[3], body(expected_revision=1, role='operator', status='active')).status_code == 201
    with pytest.raises(RuntimeError, match='Cannot discard evaluation history or evaluator accounts'):
        migrate(admin_engine, database, 'downgrade', PREVIOUS)
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == HEAD
