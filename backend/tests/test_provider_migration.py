"""Paid-call downgrade guard on a disposable database, isolated from newer results."""
import pytest
from conftest import database as session_database
from sqlalchemy import text
from test_execution import execution as execution  # noqa: F401
from test_execution import queued_message as queued_message  # noqa: F401
from test_provider_calls import begin


@pytest.fixture
def database(tmp_path_factory):
    yield from session_database.__wrapped__(tmp_path_factory)


def test_downgrade_cannot_remove_existing_paid_call_fences(execution, queued_message, admin_engine, database):
    from alembic import command

    from wizzy.config import migration_config

    lease = execution.claim(queued_message)
    call = begin(execution, lease)
    config = migration_config()
    config.attributes.update(
        app_role=database['env']['WIZZY_APP_ROLE'],
        dispatcher_role=database['env']['WIZZY_DISPATCHER_ROLE'],
        worker_role=database['env']['WIZZY_WORKER_ROLE'],
    )
    with admin_engine.connect() as db:
        original_version = db.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
    with pytest.raises(RuntimeError, match='Cannot remove durable paid-call fences'), admin_engine.begin() as db:
        config.attributes['connection'] = db
        command.downgrade(config, '0005_execution_leases')
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == original_version
        assert db.execute(text('SELECT operation_key FROM model_runs WHERE id=:id'),
                          {'id': call['run_id']}).scalar_one() == call['operation_key']
    assert begin(execution, lease) is None
    assert not execution.release(lease)
