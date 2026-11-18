"""Projection migration/replay on disposable databases, not shared development data."""
import subprocess
import sys

import pytest
from conftest import MIGRATION_HEAD
from conftest import database as session_database
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_execution import execution as execution  # noqa: F401
from test_mistakes import drain, mistakes
from test_question_migration import migrate
from test_question_revisions import questions
from test_result_read import readable_result as readable_result  # noqa: F401
from test_results import result_context as result_context  # noqa: F401
from test_verdict_revisions import override


@pytest.fixture
def database(tmp_path_factory):
    yield from session_database.__wrapped__(tmp_path_factory)


def test_upgrade_replays_existing_events_and_is_repeatable(readable_result, client, owner, student, database, admin_engine):
    q = questions(client, owner, readable_result[3])[0]
    migrate(admin_engine, database, 'downgrade', '0010_verdict_revisions')
    assert override(client, owner['headers'], q['question_id'], verdict='incorrect').status_code == 201
    with admin_engine.connect() as db:
        event = db.execute(text('SELECT id,payload FROM question_verdict_events WHERE question_id=:id'), {'id': q['question_id']}).one()
    migrate(admin_engine, database, 'upgrade', 'head')
    assert mistakes(client, owner['headers'], student['id']).json()['data']['pending_updates'] == 1
    drain(database)
    before = mistakes(client, owner['headers'], student['id']).json()['data']
    assert len(before['items']) == 1 and before['pending_updates'] == 0
    migrate(admin_engine, database, 'upgrade', 'head')
    drain(database)
    assert mistakes(client, owner['headers'], student['id']).json()['data'] == before
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT id,payload FROM question_verdict_events WHERE question_id=:id'), {'id': q['question_id']}).one() == event
        assert db.execute(text('SELECT count(*) FROM question_derivation_receipts')).scalar_one() == 1


@pytest.mark.parametrize('verdict', ['incorrect', 'correct'])
def test_downgrade_refuses_to_discard_processed_state(readable_result, client, owner, student, database, admin_engine, verdict):
    q = questions(client, owner, readable_result[3])[0]
    assert override(client, owner['headers'], q['question_id'], verdict=verdict).status_code == 201
    drain(database)
    before = mistakes(client, owner['headers'], student['id']).json()['data']
    with pytest.raises(DBAPIError, match='Cannot discard processed question derivations'):
        migrate(admin_engine, database, 'downgrade', '0010_verdict_revisions')
    assert mistakes(client, owner['headers'], student['id']).json()['data'] == before
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == MIGRATION_HEAD
        assert db.execute(text('SELECT count(*) FROM question_derivation_receipts')).scalar_one() == 1


def test_empty_downgrade_retains_pending_events_and_restores_capabilities(readable_result, client, owner, student, database, admin_engine):
    q = questions(client, owner, readable_result[3])[0]
    assert override(client, owner['headers'], q['question_id'], verdict='incorrect').status_code == 201
    migrate(admin_engine, database, 'downgrade', '0010_verdict_revisions')
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT to_regclass('public.mistake_projections')")).scalar_one() is None
        assert db.execute(text("SELECT to_regprocedure('public.consume_question_derivation()')")).scalar_one() is None
        assert db.execute(text('SELECT count(*) FROM question_verdict_events')).scalar_one() == 1
    migrate(admin_engine, database, 'upgrade', 'head')
    drain(database)
    assert len(mistakes(client, owner['headers'], student['id']).json()['data']['items']) == 1


def test_consumer_cli_uses_isolated_worker_and_only_prints_status(readable_result, client, owner, student, database):
    q = questions(client, owner, readable_result[3])[0]
    assert override(client, owner['headers'], q['question_id'], verdict='incorrect').status_code == 201
    env = database['env'] | {'WIZZY_WORKER_DATABASE_URL': database['worker_url'].render_as_string(hide_password=False)}
    result = subprocess.run([sys.executable, '-m', 'wizzy.learning', '--once'],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'applied' and not result.stderr
    result = subprocess.run([sys.executable, '-m', 'wizzy.learning'],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'idle' and not result.stderr
    assert len(mistakes(client, owner['headers'], student['id']).json()['data']['items']) == 1
