"""Controlled worker factory; deliberately no default or mock production provider.

Each delivery opens its own restricted-role engine, avoiding inherited prefork
connections. SQL outbox/leases/fences, not broker redelivery, govern recovery.
This single-stage integration is NOT a released real-AI worker entry point.
"""
from sqlalchemy import create_engine

from wizzy.broker import TASK_NAME, create_broker
from wizzy.config import Settings
from wizzy.consumer import GradingConsumer, StructureProvider
from wizzy.execution import ExecutionStore, InvalidMessage, envelope


def _engine(url):
    return create_engine(url, hide_parameters=True, pool_pre_ping=True,
                         connect_args={"connect_timeout": 3})


def create_worker(settings: Settings, *, provider: StructureProvider | None = None):
    # Fail before any database/broker access; never silently select a test adapter.
    GradingConsumer(None, provider)
    url = settings.worker_db_url
    engine = None
    try:
        engine = _engine(url)
        ExecutionStore(engine)
    except Exception:
        raise RuntimeError("Worker database unavailable or not a restricted worker role") from None
    finally:
        if engine is not None:
            engine.dispose()

    app = create_broker(settings)
    app.conf.update(
        worker_prefetch_multiplier=1, worker_enable_remote_control=False,
        worker_send_task_events=False, task_send_sent_event=False,
    )

    @app.task(name=TASK_NAME, shared=False, lazy=False, max_retries=0,
              acks_late=True, acks_on_failure_or_timeout=True,
              reject_on_worker_lost=False, soft_time_limit=50, time_limit=55)
    def grade_submission(*args, **kwargs):
        if args:
            return "invalid_message"
        try:
            message = envelope(kwargs)
        except InvalidMessage:
            return "invalid_message"
        engine = None
        try:
            engine = _engine(url)
            return GradingConsumer(ExecutionStore(engine), provider).process(message)
        except Exception:
            # SQL/SDK exceptions may contain private payloads or credentials.
            # Never pass their text to Celery's task-failure logger/backend.
            return "unavailable"
        finally:
            if engine is not None:
                engine.dispose()

    return app
