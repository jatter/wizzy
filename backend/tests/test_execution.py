"""Real PostgreSQL consumer boundary; no fake AI provider or successful grading."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_media import upload
from test_submissions import payload, submit


@pytest.fixture
def execution(database):
    from wizzy.execution import ExecutionStore

    engine = create_engine(database['worker_url'], hide_parameters=True)
    store = ExecutionStore(engine)
    yield store
    engine.dispose()


@pytest.fixture
def queued_message(client, owner, student, admin_engine):
    media = upload(client, owner['headers'], student['id'])
    response = submit(client, owner['headers'], payload(student['id'], [media]))
    assert response.status_code == 202
    result = response.json()['data']
    with admin_engine.connect() as db:
        event = db.execute(text('SELECT id,request_id FROM outbox_events WHERE aggregate_id=:id'),
                           {'id': result['job_id']}).mappings().one()
    return {'event_id': str(event['id']), 'job_id': result['job_id'], 'source_revision': 1,
            'deletion_epoch': 0, 'request_id': event['request_id']}


def expire(admin_engine, message, *, retry=False):
    with admin_engine.begin() as db:
        if retry:
            db.execute(text("UPDATE execution_receipts SET retry_at=now()-interval '1 second' WHERE event_id=:id"),
                       {'id': message['event_id']})
            db.execute(text("UPDATE outbox_events SET next_dispatch_at=now()-interval '1 second' WHERE id=:id"),
                       {'id': message['event_id']})
        else:
            db.execute(text("UPDATE execution_receipts SET lease_until=now()-interval '1 second' WHERE event_id=:id"),
                       {'id': message['event_id']})


def test_claim_reloads_trusted_context_and_does_not_complete_grading(execution, queued_message, owner, student, admin_engine):
    with admin_engine.begin() as db:
        db.execute(text("UPDATE outbox_events SET payload=jsonb_build_object('tenant_id',CAST(:fake AS text)) WHERE id=:id"),
                   {'fake': str(uuid4()), 'id': queued_message['event_id']})
    lease = execution.claim(queued_message)
    assert str(lease['tenant_id']) == owner['tenant_id']
    assert str(lease['student_id']) == student['id']
    assert str(lease['job_id']) == queued_message['job_id']
    assert lease['attempt'] == 1
    assert set(lease) == {'event_id', 'job_id', 'tenant_id', 'student_id', 'submission_id',
                          'source_revision', 'deletion_epoch', 'request_id', 'lease_token', 'attempt'}
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT j.status,s.status FROM jobs j JOIN submissions s ON s.id=j.submission_id WHERE j.id=:id'),
                          {'id': queued_message['job_id']}).one() == ('running', 'processing')
        assert db.execute(text('SELECT status FROM quota_reservations WHERE submission_id=:id'),
                          {'id': lease['submission_id']}).scalar_one() == 'reserved'
        assert db.execute(text('SELECT used_pages FROM quota_accounts WHERE tenant_id=:id'),
                          {'id': lease['tenant_id']}).scalar_one() == 0


def test_concurrent_duplicate_messages_only_one_execution(execution, queued_message, admin_engine):
    with ThreadPoolExecutor(max_workers=6) as pool:
        leases = list(pool.map(lambda _: execution.claim(queued_message), range(6)))
    assert len([lease for lease in leases if lease]) == 1
    assert execution.claim(queued_message) is None
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT attempt FROM execution_receipts WHERE event_id=:id'),
                          {'id': queued_message['event_id']}).scalar_one() == 1


@pytest.mark.parametrize('change', [
    {'tenant_id': str(uuid4())}, {'lease_token': str(uuid4())}, {'source_revision': True},
    {'source_revision': '1'}, {'source_revision': 0}, {'deletion_epoch': -1},
    {'deletion_epoch': False}, {'event_id': 'not-an-id'}, {'request_id': ''},
    {'request_id': 'x' * 129}, {'request_id': None},
])
def test_untrusted_envelope_rejected_without_echoing_content(execution, queued_message, change):
    from wizzy.execution import InvalidMessage

    with pytest.raises(InvalidMessage, match='^Invalid grading message$'):
        execution.claim(queued_message | change)
    assert execution.claim(queued_message)


@pytest.mark.parametrize('change', [
    {'job_id': str(uuid4())}, {'event_id': str(uuid4())}, {'source_revision': 2},
    {'deletion_epoch': 1}, {'request_id': 'forged-request'},
])
def test_forged_or_stale_references_cannot_claim(execution, queued_message, change):
    assert execution.claim(queued_message | change) is None
    assert execution.claim(queued_message)


def test_worker_has_functions_only_and_other_roles_cannot_execute(database, execution):
    with execution.engine.connect() as db:
        assert db.execute(text('SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolinherit,rolreplication FROM pg_roles WHERE rolname=current_user')).one() == (False,) * 6
        for table in ('students', 'jobs', 'execution_receipts', 'outbox_events', 'media_assets'):
            with pytest.raises(DBAPIError):
                db.execute(text(f'SELECT * FROM {table}'))
            db.rollback()
        with pytest.raises(DBAPIError):
            db.execute(text('SELECT * FROM public.claim_outbox(1)'))
        db.rollback()
        with pytest.raises(DBAPIError):
            db.execute(text('SELECT * FROM public.lock_execution_context(:id)'), {'id': uuid4()})
        db.rollback()
        functions = db.execute(text("""SELECT p.prosecdef,p.proconfig,
            has_function_privilege('public',p.oid,'EXECUTE') FROM pg_proc p
            JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'
            AND p.proname IN ('claim_execution','heartbeat_execution','release_execution')""")).all()
        assert len(functions) == 3
        assert all(row == (True, ['search_path=pg_catalog, pg_temp'], False) for row in functions)
    for url in (database['url'], database['dispatcher_url']):
        engine = create_engine(url, hide_parameters=True)
        try:
            with engine.begin() as db, pytest.raises(DBAPIError):
                db.execute(text('SELECT * FROM public.claim_execution(:event,:job,1,0,:request)'),
                           {'event': uuid4(), 'job': uuid4(), 'request': 'untrusted'})
        finally:
            engine.dispose()


def test_heartbeat_extends_only_current_authorized_execution(execution, queued_message, admin_engine):
    lease = execution.claim(queued_message)
    with admin_engine.begin() as db:
        db.execute(text("UPDATE execution_receipts SET lease_until=now()+interval '5 seconds' WHERE event_id=:id"),
                   {'id': lease['event_id']})
    assert not execution.heartbeat(lease | {'lease_token': uuid4()})
    assert not execution.heartbeat(lease | {'source_revision': 2})
    assert execution.heartbeat(lease)
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT lease_until>now()+interval \'50 seconds\' FROM execution_receipts WHERE event_id=:id'),
                          {'id': lease['event_id']}).scalar_one()
    expire(admin_engine, queued_message)
    assert not execution.heartbeat(lease)
    assert not execution.release(lease)


@pytest.mark.parametrize('published', [True, False, None])
def test_expired_execution_is_redispatched_and_old_token_is_fenced(execution, queued_message, admin_engine, database, published):
    from wizzy.dispatch import Dispatcher

    lease = execution.claim(queued_message)
    expire(admin_engine, queued_message)
    with admin_engine.begin() as db:
        # Published flags or a future publish-replay time cannot strand a crashed execution.
        db.execute(text("UPDATE outbox_events SET published_at=now(),next_dispatch_at=now()+interval '1 day' WHERE id=:id"),
                   {'id': queued_message['event_id']})
    engine = create_engine(database['dispatcher_url'], hide_parameters=True)
    try:
        relay = Dispatcher(engine, lambda event: None)
        # Isolate this event from other session test work without deleting anything.
        with admin_engine.begin() as db:
            db.execute(text("UPDATE outbox_events SET next_dispatch_at='infinity' WHERE id<>:id"),
                       {'id': queued_message['event_id']})
            db.execute(text("UPDATE execution_receipts SET lease_until='infinity' WHERE event_id<>:id AND state='running'"),
                       {'id': queued_message['event_id']})
        event = relay.claim()
        assert str(event['event_id']) == queued_message['event_id']
        with admin_engine.connect() as db:
            row = db.execute(text('SELECT execution_dispatch_attempt,next_dispatch_at<=clock_timestamp() AS due FROM outbox_events WHERE id=:id'),
                             {'id': queued_message['event_id']}).mappings().one()
            assert row['execution_dispatch_attempt'] == lease['attempt']
            assert row['due']  # A dispatcher crash must not restore the old one-day delay.
        if published is not None:
            assert relay.finish(event, published)
        assert relay.claim() is None  # Respect either publish backoff or an active relay lease.
        with admin_engine.begin() as db:
            # Advance only this event's clocks: recovery must remain replayable
            # without a new execution attempt after the relay delay expires.
            db.execute(text("UPDATE outbox_events SET next_dispatch_at=now()-interval '1 second',lease_until=CASE WHEN lease_until IS NULL THEN NULL ELSE now()-interval '1 second' END WHERE id=:id"),
                       {'id': queued_message['event_id']})
        replay = relay.claim()
        assert replay['event_id'] == event['event_id']
        assert relay.finish(replay, True)
        if published is None:
            assert not relay.finish(event, True)  # Old relay token is fenced too.
        assert relay.claim() is None
    finally:
        engine.dispose()
    renewed = execution.claim(queued_message)
    assert renewed['attempt'] == 2 and renewed['lease_token'] != lease['lease_token']
    assert not execution.heartbeat(lease) and not execution.release(lease)
    assert execution.heartbeat(renewed)


def test_release_has_bounded_retry_and_atomic_quota_release(execution, queued_message, admin_engine):
    for attempt in range(1, 4):
        lease = execution.claim(queued_message)
        assert lease['attempt'] == attempt
        assert execution.release(lease)
        assert not execution.release(lease)
        assert execution.claim(queued_message) is None
        if attempt < 3:
            with admin_engine.connect() as db:
                row = db.execute(text('SELECT state,retry_at,updated_at FROM execution_receipts WHERE event_id=:id'),
                                 {'id': lease['event_id']}).mappings().one()
                assert row['state'] == 'retry_wait'
                assert 2 ** attempt <= (row['retry_at'] - row['updated_at']).total_seconds() < 2 ** attempt + 1
            expire(admin_engine, queued_message, retry=True)
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT status,error_code FROM jobs WHERE id=:id'),
                          {'id': queued_message['job_id']}).one() == ('failed', 'execution_abandoned')
        assert db.execute(text('SELECT status FROM submissions WHERE id=:id'),
                          {'id': lease['submission_id']}).scalar_one() == 'failed'
        assert db.execute(text('SELECT status FROM quota_reservations WHERE submission_id=:id'),
                          {'id': lease['submission_id']}).scalar_one() == 'released'
        assert db.execute(text('SELECT reserved_pages,used_pages FROM quota_accounts WHERE tenant_id=:id'),
                          {'id': lease['tenant_id']}).one() == (0, 0)
        assert db.execute(text('SELECT state,attempt FROM execution_receipts WHERE event_id=:id'),
                          {'id': lease['event_id']}).one() == ('deadletter', 3)


def test_three_crashes_are_recoverable_but_not_infinite(execution, queued_message, admin_engine):
    for attempt in range(1, 4):
        lease = execution.claim(queued_message)
        assert lease['attempt'] == attempt
        expire(admin_engine, queued_message)
    assert execution.claim(queued_message) is None
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT state FROM execution_receipts WHERE event_id=:id'),
                          {'id': queued_message['event_id']}).scalar_one() == 'deadletter'


@pytest.mark.parametrize('invalidate', ['cancel', 'consent', 'epoch', 'inactive', 'tenant', 'revision'])
def test_revocation_or_version_change_fences_existing_lease(execution, queued_message, client, owner, student, admin_engine, invalidate):
    lease = execution.claim(queued_message)
    if invalidate == 'cancel':
        response = client.post(f"/api/v1/jobs/{queued_message['job_id']}/cancel", headers=owner['headers'])
        assert response.status_code == 200
    elif invalidate == 'consent':
        response = client.post('/api/v1/consents', headers=owner['headers'], json={
            'student_id': student['id'], 'policy_version': '2026-10-25',
            'child_rules_version': '2026-10-25', 'granted': False,
        })
        assert response.status_code == 201
    else:
        statements = {
            'epoch': 'UPDATE students SET deletion_epoch=deletion_epoch+1 WHERE id=:id',
            'inactive': "UPDATE students SET status='disabled' WHERE id=:id",
            'tenant': "UPDATE tenants SET status='suspended' WHERE id=:id",
            'revision': 'UPDATE execution_receipts SET source_revision=2 WHERE student_id=:id',
        }
        with admin_engine.begin() as db:
            db.execute(text(statements[invalidate]), {'id': owner['tenant_id'] if invalidate == 'tenant' else student['id']})
    assert not execution.heartbeat(lease)
    assert not execution.release(lease)
    assert execution.claim(queued_message) is None


def wait_for_blocker(admin_engine, blocker):
    import time

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with admin_engine.connect() as db:
            if db.execute(text("""SELECT EXISTS(SELECT 1 FROM pg_stat_activity
                WHERE datname=current_database() AND :pid=ANY(pg_blocking_pids(pid)))"""),
                          {'pid': blocker}).scalar_one():
                return
        time.sleep(0.01)
    pytest.fail('Expected real PostgreSQL lock contention was not observed')


@pytest.mark.parametrize('action', ['claim', 'heartbeat', 'release'])
def test_waiting_execution_rechecks_consent_after_lock_is_acquired(execution, queued_message, student, admin_engine, action):
    lease = execution.claim(queued_message) if action != 'claim' else None
    # Hold the same exclusive student fence used by consent withdrawal. Observe
    # actual lock contention before committing, rather than relying on sleeps.
    with ThreadPoolExecutor(max_workers=1) as pool:
        with admin_engine.begin() as db:
            blocker = db.execute(text('SELECT pg_backend_pid()')).scalar_one()
            db.execute(text('UPDATE students SET consent_active=false WHERE id=:id'), {'id': student['id']})
            future = pool.submit(getattr(execution, action), queued_message if action == 'claim' else lease)
            wait_for_blocker(admin_engine, blocker)
            assert not future.done()
        assert future.result(timeout=5) == (None if action == 'claim' else False)


def test_consent_withdrawal_waits_for_execution_then_fences_it(execution, queued_message, client, owner, student, admin_engine):
    lease = execution.claim(queued_message)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with execution.engine.begin() as db:
            blocker = db.execute(text('SELECT pg_backend_pid()')).scalar_one()
            assert db.execute(text('SELECT public.heartbeat_execution(:event_id,:job_id,:source_revision,:deletion_epoch,:lease_token)'), lease).scalar_one()
            future = pool.submit(client.post, '/api/v1/consents', headers=owner['headers'], json={
                'student_id': student['id'], 'policy_version': '2026-10-25',
                'child_rules_version': '2026-10-25', 'granted': False,
            })
            wait_for_blocker(admin_engine, blocker)
            assert not future.done()
        assert future.result(timeout=5).status_code == 201
    assert not execution.heartbeat(lease)
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT status FROM jobs WHERE id=:id'),
                          {'id': lease['job_id']}).scalar_one() == 'cancelled'
        assert db.execute(text('SELECT status FROM quota_reservations WHERE submission_id=:id'),
                          {'id': lease['submission_id']}).scalar_one() == 'released'


@pytest.mark.parametrize('role', ['admin', 'url'])
def test_execution_store_does_not_allow_broad_runtime_identity(database, role):
    from wizzy.execution import ExecutionStore

    engine = create_engine(database[role], hide_parameters=True)
    try:
        with pytest.raises(RuntimeError, match='restricted worker'):
            ExecutionStore(engine)
    finally:
        engine.dispose()
