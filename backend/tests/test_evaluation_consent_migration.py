"""Private consent evidence and reversible empty migrations on disposable PostgreSQL."""
import pytest
from alembic import command
from conftest import MIGRATION_HEAD
from conftest import database as session_database
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_evaluation_consent import grant
from test_question_migration import migrate

from wizzy.config import migration_config

TABLES = ('evaluation_consent_records', 'evaluation_consent_states')
CAPABILITY = 'public.evaluation_consent_request(text,uuid,text,jsonb,text)'
HELPERS = ('public.evaluation_authorization_active(uuid)', 'public.evaluation_consent_view(uuid)',
           'public.evaluation_consent_immutable()')


@pytest.fixture
def database(tmp_path_factory):
    yield from session_database.__wrapped__(tmp_path_factory)


def assert_absent(db):
    for table in TABLES:
        assert db.execute(text('SELECT to_regclass(:name)'), {'name': 'public.'+table}).scalar_one() is None
    for function in (CAPABILITY, *HELPERS):
        assert db.execute(text('SELECT to_regprocedure(:name)'), {'name': function}).scalar_one() is None
    assert not db.execute(text("SELECT EXISTS(SELECT 1 FROM pg_constraint WHERE conname='evaluation_session_identity')")).scalar_one()


def test_empty_round_trip_and_private_capabilities(database, admin_engine):
    migrate(admin_engine, database, 'downgrade', '0019_derivation_claim_guard')
    with admin_engine.connect() as db:
        assert_absent(db)
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
        for function in (CAPABILITY, *HELPERS):
            definition = db.execute(text('SELECT prosecdef,proconfig FROM pg_proc WHERE oid=to_regprocedure(:name)'),
                                    {'name': function}).one()
            assert definition.prosecdef == (function != 'public.evaluation_consent_immutable()')
            assert definition.proconfig == ['search_path=pg_catalog, pg_temp']
            assert not db.execute(text('''SELECT EXISTS(SELECT 1 FROM pg_proc p,LATERAL aclexplode(p.proacl) a
                WHERE p.oid=to_regprocedure(:name) AND a.grantee=0 AND a.privilege_type='EXECUTE')'''), {'name': function}).scalar_one()
            for key in ('WIZZY_APP_ROLE', 'WIZZY_WORKER_ROLE', 'WIZZY_DISPATCHER_ROLE'):
                assert db.execute(text("SELECT has_function_privilege(:role,:name,'EXECUTE')"), {
                    'role': database['env'][key], 'name': function}).scalar_one() == (
                        key == 'WIZZY_APP_ROLE' and function == CAPABILITY)
    runtime = create_engine(database['url'])
    try:
        for table in TABLES:
            with pytest.raises(DBAPIError), runtime.begin() as db:
                db.execute(text('SELECT * FROM public.'+table))
        with pytest.raises(DBAPIError), runtime.begin() as db:
            db.execute(text('SELECT public.evaluation_authorization_active(gen_random_uuid())'))
    finally:
        runtime.dispose()


def test_failed_upgrade_rolls_back_schema_permissions_and_version(database, admin_engine):
    migrate(admin_engine, database, 'downgrade', '0019_derivation_claim_guard')
    config = migration_config()
    with pytest.raises(RuntimeError, match='rollback evaluation consent'), admin_engine.begin() as db:
        config.attributes.update(connection=db, app_role=database['env']['WIZZY_APP_ROLE'],
                                 worker_role=database['env']['WIZZY_WORKER_ROLE'])
        command.upgrade(config, 'head')
        raise RuntimeError('rollback evaluation consent')
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0019_derivation_claim_guard'
        assert_absent(db)
    migrate(admin_engine, database, 'upgrade', 'head')


def test_history_is_immutable_and_blocks_downgrade(client, owner, student, admin_engine, database):
    result = grant(client, owner, student)
    for statement in (
        'UPDATE evaluation_consent_records SET granted=false WHERE id=:id',
        'DELETE FROM evaluation_consent_records WHERE id=:id',
        'TRUNCATE evaluation_consent_records CASCADE',
    ):
        with pytest.raises(DBAPIError, match='Evaluation consent history is immutable'), admin_engine.begin() as db:
            db.execute(text(statement), {'id': result['decision']['id']})
    with pytest.raises(RuntimeError, match='Cannot discard evaluation consent or audit history'):
        migrate(admin_engine, database, 'downgrade', '0019_derivation_claim_guard')
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == MIGRATION_HEAD
        assert db.execute(text('SELECT granted FROM evaluation_consent_records WHERE id=:id'),
                          {'id': result['decision']['id']}).scalar_one()
        assert db.execute(text('SELECT count(*) FROM evaluation_consent_states')).scalar_one() == 1


def test_audit_alone_blocks_downgrade(client, owner, student, admin_engine, database):
    with admin_engine.begin() as db:
        db.execute(text('''INSERT INTO audit_events(id,tenant_id,actor_id,action,resource_id,request_id)
            SELECT gen_random_uuid(),tenant_id,user_id,'evaluation.consent.revoked',:student,'evaluation-audit-only'
            FROM memberships WHERE tenant_id=:tenant AND role='owner' '''),
                   {'tenant': owner['tenant_id'], 'student': student['id']})
    with pytest.raises(RuntimeError, match='Cannot discard evaluation consent or audit history'):
        migrate(admin_engine, database, 'downgrade', '0019_derivation_claim_guard')
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == MIGRATION_HEAD
        assert db.execute(text("SELECT count(*) FROM audit_events WHERE action='evaluation.consent.revoked'")).scalar_one() == 1
