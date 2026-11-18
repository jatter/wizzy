"""Verdict migrations on dedicated disposable databases, never the development DB."""
import pytest
from conftest import database as session_database
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_execution import execution as execution  # noqa: F401
from test_question_migration import migrate
from test_question_revisions import correction, questions
from test_result_read import readable_result as readable_result  # noqa: F401
from test_results import result_context as result_context  # noqa: F401
from test_verdict_revisions import override, view

from wizzy.db import set_context


@pytest.fixture
def database(tmp_path_factory):
    yield from session_database.__wrapped__(tmp_path_factory)


def test_verdict_upgrade_preserves_ocr_history_without_inventing_confirmation(readable_result, client, owner, admin_engine, database):
    q = questions(client, owner, readable_result[3])[0]
    migrate(admin_engine, database, 'downgrade', '0009_question_revisions')
    response = correction(client, owner['headers'], q['question_id'])
    assert response.status_code == 201, response.text
    original = response.json()['data']
    assert 'verdict_revision' not in original
    migrate(admin_engine, database, 'upgrade', 'head')
    upgraded = view(client, owner, q['question_id'])
    assert upgraded['history'] == original['history']
    assert upgraded['machine_question'] == original['machine_question']
    assert upgraded['verdict'] == 'pending_review' and upgraded['verdict_provenance'] == 'ocr_pending'
    assert upgraded['verdict_revision'] == 0 and upgraded['effective_revision'] == 1
    assert upgraded['verdict_history'] == [] and upgraded['effective_verdict_revision'] is None
    migrate(admin_engine, database, 'upgrade', 'head')
    assert view(client, owner, q['question_id']) == upgraded
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM question_verdict_events')).scalar_one() == 0
    response = override(client, owner['headers'], q['question_id'], expected_revision=2)
    assert response.status_code == 201, response.text
    assert response.json()['data']['verdict_history'][0]['question_revision'] == 2


def test_verdict_downgrade_refuses_to_discard_human_history(readable_result, client, owner, admin_engine, database):
    q = questions(client, owner, readable_result[3])[0]
    response = override(client, owner['headers'], q['question_id'])
    assert response.status_code == 201, response.text
    with pytest.raises(DBAPIError, match='Cannot discard guardian verdict revision history'):
        migrate(admin_engine, database, 'downgrade', '0009_question_revisions')
    assert view(client, owner, q['question_id']) == response.json()['data']
    with admin_engine.connect() as db:
        from alembic.script import ScriptDirectory

        from wizzy.config import migration_config
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == ScriptDirectory.from_config(migration_config()).get_current_head()
        assert db.execute(text('SELECT count(*) FROM question_verdict_events WHERE question_id=:id'), {'id': q['question_id']}).scalar_one() == 1


def test_empty_verdict_downgrade_restores_reader_and_privileges(readable_result, client, owner, admin_engine, database):
    q = questions(client, owner, readable_result[3])[0]
    migrate(admin_engine, database, 'downgrade', '0009_question_revisions')
    old = view(client, owner, q['question_id'])
    assert old['verdict'] == q['verdict'] and 'verdict_history' not in old
    actor_id = client.get('/api/v1/auth/me', headers=owner['headers']).json()['data']['user_id']
    engine = create_engine(database['url'], hide_parameters=True)
    try:
        with engine.begin() as db:
            set_context(db, owner['tenant_id'], user_id=actor_id)
            assert db.execute(text('SELECT public.read_question(:id)'), {'id': q['question_id']}).scalar_one() == old
    finally:
        engine.dispose()
    migrate(admin_engine, database, 'upgrade', 'head')
    assert view(client, owner, q['question_id']) == q


def test_verdict_downgrade_refuses_to_discard_ocr_only_event(readable_result, client, owner, admin_engine, database):
    q = questions(client, owner, readable_result[3])[0]
    response = correction(client, owner['headers'], q['question_id'])
    assert response.status_code == 201, response.text
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM verdict_revisions WHERE question_id=:id'), {'id': q['question_id']}).scalar_one() == 0
    with pytest.raises(DBAPIError, match='Cannot discard guardian OCR revision history or verdict change events'):
        migrate(admin_engine, database, 'downgrade', '0009_question_revisions')
    assert view(client, owner, q['question_id']) == response.json()['data']
    with admin_engine.connect() as db:
        from alembic.script import ScriptDirectory

        from wizzy.config import migration_config
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == ScriptDirectory.from_config(migration_config()).get_current_head()
        assert db.execute(text('SELECT count(*) FROM question_verdict_events WHERE question_id=:id'), {'id': q['question_id']}).scalar_one() == 1
