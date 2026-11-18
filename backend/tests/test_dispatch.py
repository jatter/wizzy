"""Real PostgreSQL leases and real Redis transport; no shared broker flushes."""
import os
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_media import upload
from test_submissions import payload, submit

from wizzy.config import ROOT


@pytest.fixture
def dispatcher(database, admin_engine):
    from wizzy.dispatch import Dispatcher

    # Only the session-owned disposable test DB; older tests must not supply work.
    with admin_engine.begin() as db:
        # An expired consumer lease bypasses next_dispatch_at when its attempt
        # was never observed by the dispatcher. Suppress only pre-existing test
        # events, including that recovery branch; new events remain dispatchable.
        db.execute(text("""UPDATE outbox_events e SET next_dispatch_at='infinity',
            lease_until=NULL,lease_token=NULL,execution_dispatch_attempt=COALESCE(
                (SELECT r.attempt FROM execution_receipts r WHERE r.event_id=e.id),0)"""))
    engine = create_engine(database["dispatcher_url"], hide_parameters=True)
    service = Dispatcher(engine, lambda event: None)
    yield service
    engine.dispose()


@pytest.fixture
def queued(dispatcher, client, owner, student):
    media = upload(client, owner["headers"], student["id"])
    response = submit(client, owner["headers"], payload(student["id"], [media]))
    assert response.status_code == 202
    return response.json()["data"]


def due(admin_engine, event_id, expire=False):
    with admin_engine.begin() as db:
        db.execute(text("""UPDATE outbox_events SET next_dispatch_at=now()-interval '1 second',
            lease_until=CASE WHEN :expire THEN now()-interval '1 second' ELSE lease_until END
            WHERE id=:id"""), {"id": event_id, "expire": expire})


def test_roles_cannot_read_children_or_modify_jobs_and_api_cannot_claim(database, dispatcher):
    with dispatcher.engine.connect() as db:
        assert db.execute(text("SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolinherit,rolreplication FROM pg_roles WHERE rolname=current_user")).one() == (False,) * 6
        for table in ("students", "auth_sessions", "media_assets", "jobs", "outbox_events"):
            with pytest.raises(DBAPIError):
                db.execute(text(f"SELECT * FROM {table}"))
            db.rollback()
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE jobs SET status='succeeded'"))
        db.rollback()
        rows = db.execute(text("""SELECT p.prosecdef,p.proconfig,
            has_function_privilege('public',p.oid,'EXECUTE') FROM pg_proc p
            JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.proname IN ('claim_outbox','settle_outbox')""")).all()
        assert len(rows) == 2
        assert all(row == (True, ['search_path=pg_catalog, pg_temp'], False) for row in rows)
    runtime = create_engine(database["url"], hide_parameters=True)
    try:
        with runtime.begin() as db:
            db.execute(text("SELECT set_config('app.tenant_id',:id,true)"), {"id": str(uuid4())})
            with pytest.raises(DBAPIError):
                db.execute(text("SELECT * FROM public.claim_outbox(1)"))
    finally:
        runtime.dispose()


def test_claim_returns_only_identifiers_not_untrusted_payload(dispatcher, queued, admin_engine):
    with admin_engine.begin() as db:
        db.execute(text("UPDATE outbox_events SET payload='{" + '"secret":"child-text-and-private-path"' + "}'::jsonb WHERE aggregate_id=:job"), {"job": queued["job_id"]})
    event = dispatcher.claim()
    assert set(event) == {"event_id", "job_id", "source_revision", "deletion_epoch", "request_id", "lease_token"}
    assert str(event["job_id"]) == queued["job_id"]
    assert event["source_revision"] == 1 and event["deletion_epoch"] == 0
    assert "child-text" not in str(event)
    assert dispatcher.claim() is None


def test_skip_locked_and_concurrent_claims_are_exclusive(dispatcher, queued, client, owner, student):
    media = upload(client, owner["headers"], student["id"])
    for _ in range(4):
        assert submit(client, owner["headers"], payload(student["id"], [media])).status_code == 202
    with dispatcher.engine.begin() as held:
        first = held.execute(text("SELECT * FROM public.claim_outbox(1)")).mappings().one()
        # Hold the first row lock while other connections claim; they must not block.
        with ThreadPoolExecutor(max_workers=4) as pool:
            others = list(pool.map(lambda _: dispatcher.claim(), range(4)))
        assert all(others)
        assert len({event["event_id"] for event in [first, *others]}) == 5
    assert dispatcher.claim() is None


def test_expired_lease_reclaims_and_stale_or_wrong_tokens_cannot_settle(dispatcher, queued, admin_engine):
    first = dispatcher.claim()
    assert not dispatcher.finish(first | {"lease_token": uuid4()}, True)
    due(admin_engine, first["event_id"], expire=True)
    assert not dispatcher.finish(first, True)
    second = dispatcher.claim()
    assert first["event_id"] == second["event_id"] and first["lease_token"] != second["lease_token"]
    assert not dispatcher.finish(first, False)
    assert dispatcher.finish(second, True)
    assert not dispatcher.finish(second, True)


def test_broker_failure_keeps_queued_quota_and_records_bounded_backoff(dispatcher, queued, admin_engine):
    def fail(event):
        raise ConnectionError("do-not-store-or-log-secrets")

    dispatcher.publisher = fail
    assert dispatcher.tick() == "retry"
    assert dispatcher.claim() is None
    with admin_engine.connect() as db:
        row = db.execute(text("SELECT * FROM outbox_events WHERE aggregate_id=:job"), {"job": queued["job_id"]}).mappings().one()
        assert row["published_at"] is None and row["lease_token"] is None
        assert row["dispatch_attempts"] == row["consecutive_failures"] == 1
        assert row["last_error_code"] == "broker_unavailable"
        assert 0 < (row["next_dispatch_at"] - row["created_at"]).total_seconds() < 10
        assert db.execute(text("SELECT status FROM jobs WHERE id=:job"), {"job": queued["job_id"]}).scalar_one() == "queued"
        assert db.execute(text("SELECT status FROM quota_reservations WHERE submission_id=:id"), {"id": queued["submission_id"]}).scalar_one() == "reserved"
    due(admin_engine, row["id"])
    dispatcher.publisher = lambda event: None
    assert dispatcher.tick() == "published"
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT consecutive_failures,last_error_code FROM outbox_events WHERE id=:id"), {"id": row["id"]}).one() == (0, None)


@pytest.mark.parametrize("invalid", [0, 101, None])
def test_claim_batch_bounds_enforced_by_database(dispatcher, invalid):
    with dispatcher.engine.begin() as db, pytest.raises(DBAPIError):
        db.execute(text("SELECT * FROM public.claim_outbox(:limit)"), {"limit": invalid})


@pytest.mark.parametrize("state", ["cancelled", "consent", "epoch", "inactive", "tenant"])
def test_ineligible_events_are_not_claimed(dispatcher, queued, client, owner, student, admin_engine, state):
    if state == "cancelled":
        assert client.post(f"/api/v1/jobs/{queued['job_id']}/cancel", headers=owner["headers"]).status_code == 200
    else:
        statement = {
            "consent": "UPDATE students SET consent_active=false WHERE id=:id",
            "epoch": "UPDATE students SET deletion_epoch=deletion_epoch+1 WHERE id=:id",
            "inactive": "UPDATE students SET status='disabled' WHERE id=:id",
            "tenant": "UPDATE tenants SET status='suspended' WHERE id=:id",
        }[state]
        with admin_engine.begin() as db:
            db.execute(text(statement), {"id": owner["tenant_id"] if state == "tenant" else student["id"]})
    assert dispatcher.claim() is None


@pytest.fixture
def transport():
    from wizzy.broker import create_broker, publish_event
    from wizzy.config import Settings

    name = f"wizzy-test-{uuid4().hex}"
    broker = create_broker(Settings(_env_file=None, grading_queue=name))
    with broker.connection_for_write() as connection:
        connection.ensure_connection(max_retries=0)
        queue = connection.SimpleQueue(name)
        yield broker, queue, lambda event: publish_event(broker, event)
        # Never FLUSHDB or purge another queue; this run owns this random name.
        queue.queue.delete()
        queue.close()
    broker.close()


def message(queue):
    item = queue.get(block=True, timeout=3)
    assert item.content_type == "application/json"
    assert item.headers["task"] == "wizzy.grade_submission.v1"
    item.ack()
    return item


def test_real_redis_json_envelope_and_database_driven_replay(dispatcher, queued, transport, admin_engine):
    broker, queue, publisher = transport
    assert broker.conf.result_backend is None
    dispatcher.publisher = publisher
    assert dispatcher.tick() == "published"
    first = message(queue)
    body = first.payload[1]
    assert set(body) == {"event_id", "job_id", "source_revision", "deletion_epoch", "request_id"}
    assert body["job_id"] == queued["job_id"]
    assert first.payload[0] == []
    event_id = UUID(body["event_id"])
    assert dispatcher.claim() is None
    # Simulate broker data loss or no consumer acknowledgement: DB queued work replays.
    due(admin_engine, event_id)
    assert dispatcher.tick() == "published"
    second = message(queue)
    assert second.payload == first.payload and second.headers["id"] == first.headers["id"]
    with admin_engine.connect() as db:
        row = db.execute(text("SELECT dispatch_attempts,published_at FROM outbox_events WHERE id=:id"), {"id": event_id}).one()
        assert row.dispatch_attempts == 2 and row.published_at is not None
        assert db.execute(text("SELECT status FROM jobs WHERE id=:id"), {"id": queued["job_id"]}).scalar_one() == "queued"


def test_crash_after_real_publish_before_mark_can_replay(dispatcher, queued, transport, admin_engine, monkeypatch):
    _, queue, publisher = transport
    dispatcher.publisher = publisher
    original = dispatcher.finish

    def crash(*args):
        raise RuntimeError("injected database outage after broker accepted message")

    monkeypatch.setattr(dispatcher, "finish", crash)
    with pytest.raises(RuntimeError, match="injected"):
        dispatcher.tick()
    first = message(queue)
    due(admin_engine, UUID(first.payload[1]["event_id"]), expire=True)
    monkeypatch.setattr(dispatcher, "finish", original)
    assert dispatcher.tick() == "published"
    assert message(queue).payload == first.payload


def test_dispatcher_rejects_admin_and_api_connections(database):
    from wizzy.dispatch import Dispatcher

    for url in (database["admin"], database["url"]):
        engine = create_engine(url, hide_parameters=True)
        try:
            with pytest.raises(RuntimeError, match="restricted dispatcher"):
                Dispatcher(engine, lambda event: None)
        finally:
            engine.dispose()


def run_dispatcher(database, broker_url, queue):
    return subprocess.run(
        ["uv", "run", "python", "-m", "wizzy.dispatch", "--once"],
        cwd=ROOT / "backend", capture_output=True, text=True, timeout=15,
        env=os.environ | {
            "WIZZY_DISPATCHER_DATABASE_URL": database["dispatcher_url"].render_as_string(hide_password=False),
            "WIZZY_BROKER_URL": broker_url,
            "WIZZY_GRADING_QUEUE": queue,
        },
    )


def test_cli_publishes_once_then_reports_idle(database, queued, transport, admin_engine):
    broker, queue, _ = transport
    result = run_dispatcher(database, broker.conf.broker_url, broker.conf.task_default_queue)
    assert result.returncode == 0 and result.stdout.strip() == "dispatch=published"
    assert result.stderr == ""
    assert message(queue).payload[1]["job_id"] == queued["job_id"]
    result = run_dispatcher(database, broker.conf.broker_url, broker.conf.task_default_queue)
    assert result.returncode == 0 and result.stdout.strip() == "dispatch=idle"
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT status FROM jobs WHERE id=:id"), {"id": queued["job_id"]}).scalar_one() == "queued"


def test_real_connection_failure_is_bounded_and_cli_does_not_expose_credentials(database, queued, admin_engine):
    # Reserve a local port without listening. No shared Redis shutdown or data loss.
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        port = unavailable.getsockname()[1]
        result = run_dispatcher(database, f"redis://:must-not-log-this@127.0.0.1:{port}/0", "unused")
    assert result.returncode == 1 and result.stdout.strip() == "dispatch=retry"
    assert result.stderr == ""
    with admin_engine.connect() as db:
        row = db.execute(text("""SELECT published_at,lease_token,last_error_code,consecutive_failures
            FROM outbox_events WHERE aggregate_id=:id"""), {"id": queued["job_id"]}).one()
        assert row == (None, None, "broker_unavailable", 1)
        assert db.execute(text("SELECT status FROM jobs WHERE id=:id"), {"id": queued["job_id"]}).scalar_one() == "queued"
        assert db.execute(text("SELECT status FROM quota_reservations WHERE submission_id=:id"), {"id": queued["submission_id"]}).scalar_one() == "reserved"


def test_repeated_failure_backoff_caps_and_success_resets(dispatcher, queued, admin_engine):
    def fail(event):
        raise ConnectionError("do-not-log-this")

    dispatcher.publisher = fail
    for attempt in range(1, 13):
        assert dispatcher.tick() == "retry"
        with admin_engine.connect() as db:
            row = db.execute(text("""SELECT id,dispatch_attempts,consecutive_failures,
                extract(epoch FROM next_dispatch_at-clock_timestamp()) AS wait
                FROM outbox_events WHERE aggregate_id=:id"""), {"id": queued["job_id"]}).mappings().one()
        expected = min(300, 2 ** min(attempt, 9))
        assert expected - 2 < row["wait"] <= expected
        assert row["dispatch_attempts"] == attempt
        assert row["consecutive_failures"] == min(attempt, 9)
        due(admin_engine, row["id"])
    dispatcher.publisher = lambda event: None
    assert dispatcher.tick() == "published"
    with admin_engine.connect() as db:
        assert db.execute(text("""SELECT dispatch_attempts,consecutive_failures,last_error_code
            FROM outbox_events WHERE aggregate_id=:id"""), {"id": queued["job_id"]}).one() == (13, 0, None)
