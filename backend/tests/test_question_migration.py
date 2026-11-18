"""Migration round trips on dedicated disposable databases, never the development DB."""
import pytest
from alembic import command
from conftest import database as session_database
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_execution import execution as execution  # noqa: F401
from test_question_revisions import correction, questions
from test_result_read import readable_result as readable_result  # noqa: F401
from test_results import complete, document, finish, snapshot
from test_results import result_context as result_context  # noqa: F401

from wizzy.config import migration_config


@pytest.fixture
def database(tmp_path_factory):
    # A separate database per test: migration tests must not mutate shared test state.
    yield from session_database.__wrapped__(tmp_path_factory)


def migrate(admin_engine, database, direction, target):
    config = migration_config()
    with admin_engine.begin() as db:
        config.attributes.update(connection=db, app_role=database["env"]["WIZZY_APP_ROLE"],
                                 worker_role=database["env"]["WIZZY_WORKER_ROLE"])
        getattr(command, direction)(config, target)
    # Historical API checks must reconnect after schema changes invalidate prepared row types.
    for engine in database.get('runtime_engines', []):
        engine.dispose()


def test_question_migration_backfills_original_results(readable_result, client, owner, admin_engine, database):
    submission_id = readable_result[3]
    original = client.get(f"/api/v1/submissions/{submission_id}/result", headers=owner["headers"]).json()["data"]
    migrate(admin_engine, database, "downgrade", "0008_result_reader")
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT to_regclass('public.questions')")).scalar_one() is None
    # A later failure must roll back the entire 0008 -> head upgrade, including backfill.
    config = migration_config()
    with pytest.raises(RuntimeError, match="rollback upgraded backfill"), admin_engine.begin() as db:
        config.attributes.update(connection=db, app_role=database["env"]["WIZZY_APP_ROLE"],
                                 worker_role=database["env"]["WIZZY_WORKER_ROLE"])
        command.upgrade(config, "head")
        raise RuntimeError("rollback upgraded backfill")
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0008_result_reader"
        assert db.execute(text("SELECT to_regclass('public.questions')")).scalar_one() is None
        assert db.execute(text("SELECT to_regclass('public.verdict_revisions')")).scalar_one() is None
    assert client.get(f"/api/v1/submissions/{submission_id}/result", headers=owner["headers"]).json()["data"] == original
    migrate(admin_engine, database, "upgrade", "head")
    with admin_engine.connect() as db:
        constraints = db.execute(text("""SELECT conname, condeferrable, condeferred
            FROM pg_constraint WHERE conrelid='public.questions'::regclass
            AND conname IN ('question_current_revision','question_effective_verdict')""")).all()
        assert sorted(constraints) == [
            ("question_current_revision", True, True), ("question_effective_verdict", True, True),
        ]
    rows = questions(client, owner, submission_id)
    assert [q["page_position"] for q in rows] == [1, 2, 3]
    assert all(q["revision"] == 1 and q["history"][0]["origin"] == "machine" for q in rows)
    assert all(q["verdict_revision"] == 0 and q["effective_revision"] == 1
               and q["effective_verdict_revision"] is None and q["verdict_history"] == [] for q in rows)
    assert client.get(f"/api/v1/submissions/{submission_id}/result", headers=owner["headers"]).json()["data"] == original
    migrate(admin_engine, database, "upgrade", "head")
    assert questions(client, owner, submission_id) == rows


def test_question_downgrade_refuses_to_discard_guardian_history(readable_result, client, owner, admin_engine, database):
    migrate(admin_engine, database, 'downgrade', '0009_question_revisions')
    q = questions(client, owner, readable_result[3])[0]
    edited = correction(client, owner["headers"], q["question_id"])
    assert edited.status_code == 201, edited.text
    with pytest.raises(DBAPIError, match="Cannot discard guardian OCR revision history"):
        migrate(admin_engine, database, "downgrade", "0008_result_reader")
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0009_question_revisions"
    assert client.get(f"/api/v1/questions/{q['question_id']}", headers=owner["headers"]).json()["data"] == edited.json()["data"]


def test_result_downgrade_refuses_data_loss(execution, result_context, database, admin_engine):
    lease, call, _ = result_context
    finish(execution, lease, call)
    assert complete(execution, lease, document(call))
    before = snapshot(admin_engine, lease)
    config = migration_config()
    config.attributes.update(app_role=database['env']['WIZZY_APP_ROLE'],
                             worker_role=database['env']['WIZZY_WORKER_ROLE'])
    with pytest.raises(RuntimeError, match='Cannot remove settled grading results'), admin_engine.begin() as db:
        config.attributes['connection'] = db
        command.downgrade(config, '0006_provider_call_fences')
    assert snapshot(admin_engine, lease) == before
