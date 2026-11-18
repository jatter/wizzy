"""The history capability can be rolled back without deleting saved plans."""
import pytest
from alembic import command
from conftest import MIGRATION_HEAD
from conftest import database as session_database
from sqlalchemy import text
from test_question_migration import migrate
from test_review_plans import generate

from wizzy.config import migration_config

CAPABILITY = 'public.read_review_plans(uuid,text,integer,timestamptz,uuid)'
PREVIOUS = '0032_platform_password_captcha'
HEAD = MIGRATION_HEAD


@pytest.fixture
def database(tmp_path_factory):
    yield from session_database.__wrapped__(tmp_path_factory)


def test_downgrade_preserves_plan_and_audit_history(client, owner, student, admin_engine, database):
    plan = generate(client, owner['headers'], student['id']).json()['data']
    migrate(admin_engine, database, 'downgrade', PREVIOUS)
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT to_regprocedure(:name)'), {'name': CAPABILITY}).scalar_one() is None
        assert db.execute(text('SELECT count(*) FROM review_plans WHERE id=:id'), {'id': plan['id']}).scalar_one() == 1
        assert db.execute(text("SELECT count(*) FROM audit_events WHERE resource_id=:id AND action='review_plan.generated'"),
                          {'id': plan['id']}).scalar_one() == 1
    assert client.get('/api/v1/review-plans/'+plan['id'], headers=owner['headers']).json()['data'] == plan
    migrate(admin_engine, database, 'upgrade', 'head')
    migrate(admin_engine, database, 'upgrade', 'head')
    response = client.get('/api/v1/review-plans', headers=owner['headers'], params={'student_id': student['id']})
    assert response.status_code == 200 and response.json()['data']['items'][0]['id'] == plan['id']


@pytest.mark.parametrize('direction', ['upgrade', 'downgrade'])
def test_history_migration_is_transactional(database, admin_engine, direction):
    if direction == 'upgrade':
        migrate(admin_engine, database, 'downgrade', PREVIOUS)
    config = migration_config()
    with pytest.raises(RuntimeError, match='rollback history capability'), admin_engine.begin() as db:
        config.attributes.update(connection=db, app_role=database['env']['WIZZY_APP_ROLE'],
                                 worker_role=database['env']['WIZZY_WORKER_ROLE'])
        getattr(command, direction)(config, 'head' if direction == 'upgrade' else PREVIOUS)
        raise RuntimeError('rollback history capability')
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == (
            PREVIOUS if direction == 'upgrade' else HEAD)
        exists = db.execute(text('SELECT to_regprocedure(:name) IS NOT NULL'), {'name': CAPABILITY}).scalar_one()
        assert exists is (direction == 'downgrade')
