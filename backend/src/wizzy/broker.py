"""JSON-only identifier transport. No result backend or private data in messages."""
from celery import Celery
from kombu import Queue

from wizzy.config import Settings

TASK_NAME = "wizzy.grade_submission.v1"
MESSAGE_FIELDS = ("event_id", "job_id", "source_revision", "deletion_epoch", "request_id")


def create_broker(settings: Settings):
    broker = Celery("wizzy", broker=settings.broker_url, set_as_current=False)
    broker.conf.update(
        accept_content=["json"], task_serializer="json", result_serializer="json",
        task_ignore_result=True, task_publish_retry=False,
        task_queues=(Queue(settings.grading_queue),), task_default_queue=settings.grading_queue,
        task_create_missing_queues=False, broker_connection_retry_on_startup=False,
        broker_connection_timeout=3,
        broker_transport_options={"socket_connect_timeout": 3, "socket_timeout": 3,
                                  "retry_on_timeout": False, "max_retries": 0},
        enable_utc=True, timezone="UTC",
    )
    return broker


def publish_event(broker, event):
    body = {key: str(event[key]) if key in ("event_id", "job_id") else event[key]
            for key in MESSAGE_FIELDS}
    broker.send_task(TASK_NAME, kwargs=body, task_id=body["event_id"], retry=False,
                     ignore_result=True, queue=broker.conf.task_default_queue)
