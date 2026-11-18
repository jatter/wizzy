"""Controlled Celery registration; the provider below exists only in tests."""
from threading import Event
from uuid import uuid4

import pytest
from celery import signals
from celery.contrib.testing.worker import start_worker
from test_consumer import consumer_context as consumer_context
from test_consumer import fail, runs
from test_dispatch import dispatcher as dispatcher
from test_dispatch import due
from test_execution import execution  # noqa: F401
from test_results import snapshot

from wizzy.broker import TASK_NAME, publish_event
from wizzy.config import Settings


def settings_for(database):
    return Settings(
        database_url=database['url'],
        worker_database_url=database['worker_url'].render_as_string(hide_password=False),
        grading_queue='wizzy.test.' + uuid4().hex,
        storage_root=database['storage'],
    )


@pytest.fixture
def worker_app(database, consumer_context):
    from wizzy.worker import create_worker

    _, provider, _ = consumer_context
    app = create_worker(settings_for(database), provider=provider)
    yield app
    app.close()


def test_unconfigured_worker_fails_before_database_or_broker_access():
    from wizzy.worker import create_worker

    settings = Settings(worker_database_url='postgresql+psycopg://invalid@127.0.0.1:1/nope')
    with pytest.raises(RuntimeError, match='No released grading provider configured'):
        create_worker(settings)


def test_worker_rejects_privileged_database_role(database, consumer_context):
    from wizzy.worker import create_worker

    settings = settings_for(database)
    settings.worker_database_url = database['admin'].render_as_string(hide_password=False)
    with pytest.raises(RuntimeError, match='restricted worker role'):
        create_worker(settings, provider=consumer_context[1])


def test_registered_task_has_conservative_transport_policy(worker_app):
    task = worker_app.tasks[TASK_NAME]
    assert task.acks_late is True
    assert task.acks_on_failure_or_timeout is True
    assert task.reject_on_worker_lost is False
    assert task.max_retries == 0
    assert task.soft_time_limit == 50 and task.time_limit == 55
    conf = worker_app.conf
    assert conf.worker_prefetch_multiplier == 1
    assert conf.worker_enable_remote_control is False
    assert conf.worker_send_task_events is False and conf.task_send_sent_event is False
    assert conf.task_always_eager is False
    assert conf.result_backend is None and conf.task_ignore_result is True
    assert conf.accept_content == ['json'] and conf.task_publish_retry is False


@pytest.mark.parametrize('positional', [False, True])
def test_invalid_task_payload_is_sanitized(worker_app, consumer_context, caplog, positional):
    task = worker_app.tasks[TASK_NAME]
    message = consumer_context[2] | {'private': 'SECRET child answers'}
    result = task.run(message) if positional else task.run(**message)
    assert result == 'invalid_message'
    assert consumer_context[1].sent == []
    assert 'SECRET' not in caplog.text


def test_registered_task_settles_once(worker_app, consumer_context, admin_engine):
    _, provider, message = consumer_context
    task = worker_app.tasks[TASK_NAME]
    assert task.run(**message) == 'settled'
    assert task.run(**message) == 'ignored'
    assert len(provider.sent) == 1
    assert snapshot(admin_engine, provider.prepared[0])[1] == (1, 1)


def test_registered_task_hides_provider_exceptions(worker_app, consumer_context, admin_engine, caplog):
    _, provider, message = consumer_context
    provider.before_send = fail
    assert worker_app.tasks[TASK_NAME].run(**message) == 'reconciliation_required'
    assert runs(admin_engine, message) == [('in_flight', 'unknown', None)]
    assert 'SECRET' not in caplog.text


def test_real_dispatcher_redis_delivery_and_duplicate_settle_once(
        worker_app, consumer_context, admin_engine, dispatcher):
    _, provider, message = consumer_context
    task = worker_app.tasks[TASK_NAME]
    # The dispatcher fixture suppresses older events in this isolated test DB.
    # Re-enable this job regardless of fixture construction order.
    due(admin_engine, message["event_id"])
    dispatcher.publisher = lambda event: publish_event(worker_app, event)
    done = Event()
    statuses = []

    def finished(sender=None, retval=None, **kwargs):
        if sender is task:
            statuses.append(retval)
            if len(statuses) == 2:
                done.set()

    signals.task_postrun.connect(finished, weak=False)
    try:
        with start_worker(worker_app, pool='solo', concurrency=1, perform_ping_check=False,
                          shutdown_timeout=10, without_heartbeat=True):
            assert dispatcher.tick() == "published"
            publish_event(worker_app, message)
            assert done.wait(15), 'Registered worker did not finish both real Redis deliveries'
        assert statuses == ['settled', 'ignored']
        assert len(provider.sent) == 1
        assert snapshot(admin_engine, provider.prepared[0])[1] == (1, 1)
        assert runs(admin_engine, message) == [('succeeded', 'known', 123)]
    finally:
        signals.task_postrun.disconnect(finished)
        # Never flush a shared Redis database; delete this test's random queue only.
        with worker_app.connection_for_write() as connection:
            connection.default_channel.queue_delete(worker_app.conf.task_default_queue)


def test_task_database_failure_returns_safe_status(worker_app, consumer_context, monkeypatch, caplog):
    import wizzy.worker as worker

    monkeypatch.setattr(worker, '_engine', lambda *args: fail())
    assert worker_app.tasks[TASK_NAME].run(**consumer_context[2]) == 'unavailable'
    assert consumer_context[1].sent == []
    assert 'SECRET' not in caplog.text


def test_invalid_database_configuration_does_not_expose_connection_text(database, consumer_context):
    from wizzy.worker import create_worker

    settings = settings_for(database)
    settings.worker_database_url = 'SECRET malformed private connection text'
    with pytest.raises(RuntimeError, match='restricted worker role') as error:
        create_worker(settings, provider=consumer_context[1])
    assert 'SECRET' not in str(error.value)
    assert error.value.__suppress_context__ is True
