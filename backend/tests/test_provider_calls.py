"""Real DB call fences and accounting metadata; these tests never call an AI."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_execution import (  # noqa: F401
    execution as execution,
)
from test_execution import (
    expire,
    wait_for_blocker,
)
from test_execution import (
    queued_message as queued_message,
)

CALL = {'stage': 'ocr', 'provider': 'contract-test', 'model': 'test-model', 'model_version': 'v1'}


def begin(execution, lease, **changes):
    assert callable(getattr(execution, 'begin_call', None)), 'Persistent provider call fence is missing'
    return execution.begin_call(lease, **(CALL | changes))


def test_begin_commits_one_call_per_stage_without_claiming_grading_success(execution, queued_message, admin_engine):
    lease = execution.claim(queued_message)
    call = begin(execution, lease)
    assert set(call) == {'run_id', 'operation_key'}
    assert call['run_id'] != call['operation_key']
    assert begin(execution, lease) is None
    with admin_engine.connect() as db:
        row = db.execute(text('SELECT * FROM model_runs WHERE id=:id'), {'id': call['run_id']}).mappings().one()
        assert row['event_id'] == lease['event_id']
        assert row['tenant_id'] == lease['tenant_id']
        assert row['student_id'] == lease['student_id']
        assert row['source_revision'] == lease['source_revision']
        assert row['deletion_epoch'] == lease['deletion_epoch']
        assert row['outcome'] == 'in_flight' and row['billing_state'] == 'unknown'
        assert row['cost_microyuan'] is None
        assert row['provider'] == CALL['provider'] and row['model_version'] == 'v1'
        assert db.execute(text('SELECT status FROM quota_reservations WHERE submission_id=:id'),
                          {'id': lease['submission_id']}).scalar_one() == 'reserved'
        assert db.execute(text('SELECT status FROM jobs WHERE id=:id'), {'id': lease['job_id']}).scalar_one() == 'running'


def test_concurrent_begin_only_one_caller_gets_external_call_permission(execution, queued_message, admin_engine):
    lease = execution.claim(queued_message)
    with ThreadPoolExecutor(max_workers=6) as pool:
        calls = list(pool.map(lambda _: begin(execution, lease), range(6)))
    assert len([call for call in calls if call]) == 1
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM model_runs WHERE event_id=:id'),
                          {'id': lease['event_id']}).scalar_one() == 1


def test_uncertain_call_blocks_release_reclaim_and_relay_even_after_lease_expiry(execution, queued_message, admin_engine, database):
    from wizzy.dispatch import Dispatcher

    lease = execution.claim(queued_message)
    begin(execution, lease)
    assert execution.heartbeat(lease)
    assert not execution.release(lease)
    expire(admin_engine, queued_message)
    assert execution.claim(queued_message) is None
    assert begin(execution, lease) is None
    engine = create_engine(database['dispatcher_url'], hide_parameters=True)
    try:
        with admin_engine.begin() as db:
            db.execute(text("UPDATE outbox_events SET next_dispatch_at='infinity' WHERE id<>:id"),
                       {'id': lease['event_id']})
            db.execute(text("UPDATE execution_receipts SET lease_until='infinity' WHERE event_id<>:id AND state='running'"),
                       {'id': lease['event_id']})
        assert Dispatcher(engine, lambda event: None).claim() is None
    finally:
        engine.dispose()
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT attempt,state FROM execution_receipts WHERE event_id=:id'),
                          {'id': lease['event_id']}).one() == (1, 'running')
        assert db.execute(text('SELECT status FROM quota_reservations WHERE submission_id=:id'),
                          {'id': lease['submission_id']}).scalar_one() == 'reserved'


@pytest.mark.parametrize('change', [
    {'lease_token': uuid4()}, {'source_revision': 2}, {'deletion_epoch': 1},
    {'event_id': uuid4()}, {'job_id': uuid4()},
])
def test_forged_execution_cannot_start_call(execution, queued_message, change):
    lease = execution.claim(queued_message)
    assert begin(execution, lease | change) is None
    assert begin(execution, lease)


@pytest.mark.parametrize('invalidate', ['cancel', 'consent', 'epoch', 'expiry'])
def test_start_rechecks_live_authorization(execution, queued_message, client, owner, student, admin_engine, invalidate):
    lease = execution.claim(queued_message)
    if invalidate == 'cancel':
        assert client.post(f"/api/v1/jobs/{lease['job_id']}/cancel", headers=owner['headers']).status_code == 200
    elif invalidate == 'consent':
        with admin_engine.begin() as db:
            db.execute(text('UPDATE students SET consent_active=false WHERE id=:id'), {'id': student['id']})
    elif invalidate == 'epoch':
        with admin_engine.begin() as db:
            db.execute(text('UPDATE students SET deletion_epoch=deletion_epoch+1 WHERE id=:id'), {'id': student['id']})
    else:
        expire(admin_engine, queued_message)
    assert begin(execution, lease) is None


def test_begin_waits_for_consent_lock_and_rechecks_after_commit(execution, queued_message, student, admin_engine):
    lease = execution.claim(queued_message)
    # No AssertionError inside a pending future: ensure the API exists first.
    assert callable(getattr(execution, 'begin_call', None))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with admin_engine.begin() as db:
            blocker = db.execute(text('SELECT pg_backend_pid()')).scalar_one()
            db.execute(text('UPDATE students SET consent_active=false WHERE id=:id'), {'id': student['id']})
            future = pool.submit(begin, execution, lease)
            wait_for_blocker(admin_engine, blocker)
            assert not future.done()
        assert future.result(timeout=5) is None


@pytest.mark.parametrize('outcome', ['succeeded', 'failed'])
@pytest.mark.parametrize('cost', [0, 1234567, None])
def test_terminal_call_metadata_is_idempotent_and_not_a_business_result(execution, queued_message, admin_engine, outcome, cost):
    lease = execution.claim(queued_message)
    call = begin(execution, lease)
    params = {'outcome': outcome, 'provider_request_id': 'test-request-1', 'cost_microyuan': cost}
    assert execution.finish_call(lease, call, **params)
    assert execution.finish_call(lease, call, **params)
    assert not execution.finish_call(lease, call, **(params | {'outcome': 'failed' if outcome == 'succeeded' else 'succeeded'}))
    assert not execution.finish_call(lease, call, **(params | {'provider_request_id': 'conflicting-request'}))
    if cost is not None:
        assert not execution.finish_call(lease, call, **(params | {'cost_microyuan': cost + 1}))
        assert not execution.finish_call(lease, call, **(params | {'cost_microyuan': None}))
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT outcome,billing_state,cost_microyuan FROM model_runs WHERE id=:id'),
                          {'id': call['run_id']}).one() == (outcome, 'unknown' if cost is None else 'known', cost)
        assert db.execute(text('SELECT status FROM jobs WHERE id=:id'), {'id': lease['job_id']}).scalar_one() == 'running'
        assert db.execute(text('SELECT reserved_pages,used_pages FROM quota_accounts WHERE tenant_id=:id'),
                          {'id': lease['tenant_id']}).one() == (1, 0)


def test_unknown_cost_can_be_reconciled_but_never_overwrites_known_cost(execution, queued_message):
    lease = execution.claim(queued_message)
    call = begin(execution, lease)
    args = {'outcome': 'succeeded', 'provider_request_id': 'test-request-2'}
    assert execution.finish_call(lease, call, **args)
    assert begin(execution, lease, stage='structure') is None
    assert execution.finish_call(lease, call, **args, cost_microyuan=20001)
    assert execution.finish_call(lease, call, **args, cost_microyuan=20001)
    assert not execution.finish_call(lease, call, **args, cost_microyuan=20002)
    assert begin(execution, lease, stage='structure')
    assert begin(execution, lease, stage='ocr') is None


@pytest.mark.parametrize('invalidate', ['cancel', 'consent', 'epoch', 'expiry'])
def test_late_billing_survives_revocation_without_resurrecting_business_data(execution, queued_message, client, owner, student, admin_engine, invalidate):
    lease = execution.claim(queued_message)
    call = begin(execution, lease)
    if invalidate in ('cancel', 'consent'):
        if invalidate == 'cancel':
            assert client.post(f"/api/v1/jobs/{lease['job_id']}/cancel", headers=owner['headers']).status_code == 200
        else:
            assert client.post('/api/v1/consents', headers=owner['headers'], json={
                'student_id': student['id'], 'policy_version': '2026-10-25',
                'child_rules_version': '2026-10-25', 'granted': False,
            }).status_code == 201
    elif invalidate == 'epoch':
        with admin_engine.begin() as db:
            db.execute(text('UPDATE students SET deletion_epoch=deletion_epoch+1 WHERE id=:id'), {'id': student['id']})
    else:
        expire(admin_engine, queued_message)
    with admin_engine.connect() as db:
        before = db.execute(text('SELECT j.status,s.status,q.status FROM jobs j JOIN submissions s ON s.id=j.submission_id JOIN quota_reservations q ON q.submission_id=s.id WHERE j.id=:id'),
                            {'id': lease['job_id']}).one()
    args = {'outcome': 'succeeded', 'provider_request_id': 'late-request', 'cost_microyuan': 2000}
    assert not execution.finish_call(lease | {'lease_token': uuid4()}, call, **args)
    assert not execution.finish_call(lease, call | {'operation_key': uuid4()}, **args)
    assert execution.finish_call(lease, call, **args)
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT j.status,s.status,q.status FROM jobs j JOIN submissions s ON s.id=j.submission_id JOIN quota_reservations q ON q.submission_id=s.id WHERE j.id=:id'),
                          {'id': lease['job_id']}).one() == before
        assert db.execute(text('SELECT cost_microyuan FROM model_runs WHERE id=:id'), {'id': call['run_id']}).scalar_one() == 2000


@pytest.mark.parametrize('change', [
    {'stage': 'unapproved-stage'}, {'provider': 'https://untrusted.test'},
    {'model': 'x' * 129}, {'model_version': 'contains\nprivate-text'},
])
def test_invalid_call_config_does_not_persist_sensitive_input(execution, queued_message, change):
    from wizzy.execution import InvalidMessage

    lease = execution.claim(queued_message)
    with pytest.raises(InvalidMessage, match='^Invalid grading message$'):
        begin(execution, lease, **change)


@pytest.mark.parametrize('cost', [-1, True, 1.5, '100', 2**63])
def test_costs_must_be_nonnegative_int64_not_floats_or_bools(execution, queued_message, cost):
    from wizzy.execution import InvalidMessage

    lease = execution.claim(queued_message)
    call = begin(execution, lease)
    with pytest.raises(InvalidMessage, match='^Invalid grading message$'):
        execution.finish_call(lease, call, outcome='succeeded', provider_request_id='test-request', cost_microyuan=cost)


def test_fence_functions_and_tables_are_restricted(execution, queued_message, database, admin_engine):
    lease = execution.claim(queued_message)
    begin(execution, lease)
    for url in (database['url'], database['dispatcher_url']):
        engine = create_engine(url, hide_parameters=True)
        try:
            with engine.connect() as db, pytest.raises(DBAPIError):
                db.execute(text("SELECT * FROM public.begin_model_call(:event_id,:job_id,:source_revision,:deletion_epoch,:lease_token,'ocr','test','test','v1')"), lease)
        finally:
            engine.dispose()
    for statement in (
        'SELECT * FROM model_runs',
        'DELETE FROM model_runs',
        'SELECT * FROM public.claim_execution_unfenced(:event_id,:job_id,:source_revision,:deletion_epoch,:request_id)',
        'SELECT public.release_execution_unfenced(:event_id,:job_id,:source_revision,:deletion_epoch,:lease_token)',
        'SELECT * FROM public.claim_outbox_unfenced(1)',
    ):
        with execution.engine.connect() as db, pytest.raises(DBAPIError):
            db.execute(text(statement), lease)
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT bool_and(prosecdef AND proconfig=ARRAY['search_path=pg_catalog, pg_temp']) FROM pg_proc JOIN pg_namespace n ON n.oid=pronamespace WHERE n.nspname='public' AND proname IN ('begin_model_call','finish_model_call','claim_execution','release_execution','claim_outbox')")).scalar_one()


def test_concurrent_stages_cannot_bypass_an_unfinished_call(execution, queued_message, admin_engine):
    lease = execution.claim(queued_message)
    stages = ('ocr', 'structure', 'verify', 'tutor')
    barrier = Barrier(len(stages), timeout=10)

    def start(stage):
        barrier.wait()
        return begin(execution, lease, stage=stage)

    with ThreadPoolExecutor(max_workers=len(stages)) as pool:
        calls = list(pool.map(start, stages))
    assert sum(call is not None for call in calls) == 1
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM model_runs WHERE event_id=:id'),
                          {'id': lease['event_id']}).scalar_one() == 1


def test_concurrent_conflicting_costs_cannot_overwrite_the_winning_receipt(execution, queued_message, admin_engine):
    lease = execution.claim(queued_message)
    call = begin(execution, lease)
    costs = (100, 200, 300, 400)
    barrier = Barrier(len(costs), timeout=10)

    def finish(cost):
        barrier.wait()
        return execution.finish_call(lease, call, outcome='succeeded',
                                     provider_request_id='concurrent-receipt', cost_microyuan=cost)

    with ThreadPoolExecutor(max_workers=len(costs)) as pool:
        accepted = list(pool.map(finish, costs))
    assert sum(accepted) == 1
    winning_cost = costs[accepted.index(True)]
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT outcome,billing_state,cost_microyuan FROM model_runs WHERE id=:id'),
                          {'id': call['run_id']}).one() == ('succeeded', 'known', winning_cost)
    assert execution.finish_call(lease, call, outcome='succeeded',
                                 provider_request_id='concurrent-receipt', cost_microyuan=winning_cost)


def test_failed_call_blocks_next_stage_even_with_known_cost(execution, queued_message):
    lease = execution.claim(queued_message)
    call = begin(execution, lease)
    assert execution.finish_call(lease, call, outcome='failed',
                                 provider_request_id='failed-receipt', cost_microyuan=100)
    assert begin(execution, lease, stage='structure') is None
    assert not execution.release(lease)

