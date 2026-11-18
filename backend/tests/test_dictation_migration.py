"""Dictation upgrade preserves customization and worker privileges on disposable DBs."""
import pytest
from conftest import database as session_database
from sqlalchemy import text
from test_media import upload
from test_question_migration import migrate
from test_submissions import payload, submit


@pytest.fixture
def database(tmp_path_factory):
    yield from session_database.__wrapped__(tmp_path_factory)


def test_round_trip_preserves_custom_prompt_and_worker_boundary(database, admin_engine):
    migrate(admin_engine, database, 'downgrade', '0043_homework_page_limit')
    with admin_engine.begin() as db:
        before = db.execute(text("SELECT proacl,prosecdef,proconfig FROM pg_proc WHERE oid='public.claim_homework_grading()'::regprocedure")).one()
        db.execute(text("UPDATE grading_prompts SET content='自定义批改规则' WHERE priority=20"))
    migrate(admin_engine, database, 'upgrade', 'head')
    migrate(admin_engine, database, 'upgrade', 'head')
    with admin_engine.connect() as db:
        after = db.execute(text("SELECT proacl,prosecdef,proconfig FROM pg_proc WHERE oid='public.claim_homework_grading()'::regprocedure")).one()
        assert after == before
        prompts = dict(db.execute(text('SELECT priority,content FROM grading_prompts')).all())
        assert '听写' in prompts[10]
        assert prompts[20] == '自定义批改规则'
        definition = db.execute(text("SELECT pg_get_functiondef('public.claim_homework_grading()'::regprocedure)")).scalar_one()
        assert definition.count("'activity_type'") == 2 and definition.count("'reference_text'") == 2
    migrate(admin_engine, database, 'downgrade', '0043_homework_page_limit')
    with admin_engine.connect() as db:
        columns = db.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='submissions'")).scalars().all()
        assert 'activity_type' not in columns and 'reference_text' not in columns
        assert db.execute(text('SELECT content FROM grading_prompts WHERE priority=20')).scalar_one() == '自定义批改规则'
    migrate(admin_engine, database, 'upgrade', 'head')


def test_downgrade_refuses_to_discard_saved_dictation(database, admin_engine, client, owner, student):
    body = payload(student['id'], [upload(client, owner['headers'], student['id'])])
    body.update(activity_type='dictation', reference_text='apple')
    saved = submit(client, owner['headers'], body)
    assert saved.status_code == 202, saved.text
    with pytest.raises(RuntimeError, match='Cannot discard dictation context'):
        migrate(admin_engine, database, 'downgrade', '0043_homework_page_limit')
    detail = client.get('/api/v1/submissions/' + saved.json()['data']['submission_id'], headers=owner['headers']).json()['data']
    assert detail['activity_type'] == 'dictation' and detail['reference_text'] == 'apple'
