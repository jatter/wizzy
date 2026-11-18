"""Controlled consumer tests: real DB, synthetic responses are NOT AI quality evidence."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event

import pytest
from sqlalchemy import text
from test_execution import execution as execution  # noqa: F401
from test_execution import expire
from test_media import upload
from test_results import document, snapshot
from test_submissions import payload, submit

from wizzy.consumer import GradingConsumer, ProviderIdentity, ProviderResponse


class ContractProvider:
    """Test-only injection. Never import this from production or use for grading."""
    identity = ProviderIdentity(provider='contract-test', model='synthetic', model_version='test-v1')

    def __init__(self):
        self.prepared = []
        self.sent = []
        self.before_prepare = lambda: None
        self.before_send = lambda: None
        self.result_change = lambda result: result
        self.outcome = 'succeeded'
        self.cost = 123

    def prepare(self, lease):
        self.before_prepare()
        self.prepared.append(deepcopy(lease))
        return 'private-test-input'

    def invoke(self, prepared, *, run_id, operation_key, timeout_seconds):
        assert prepared == 'private-test-input'
        assert timeout_seconds == 30
        self.sent.append((run_id, operation_key))
        self.before_send()
        result = document({'run_id': run_id}, succeeded=0 if self.outcome == 'failed' else 3)
        return ProviderResponse(outcome=self.outcome, provider_request_id='synthetic-receipt',
                                cost_microyuan=self.cost, result=self.result_change(result))


@pytest.fixture
def consumer_context(execution, client, owner, student, admin_engine):
    images = [upload(client, owner['headers'], student['id']) for _ in range(3)]
    response = submit(client, owner['headers'], payload(student['id'], images))
    assert response.status_code == 202
    job_id = response.json()['data']['job_id']
    with admin_engine.connect() as db:
        event = db.execute(text('SELECT id,request_id FROM outbox_events WHERE aggregate_id=:id'),
                           {'id': job_id}).mappings().one()
    message = {'event_id': str(event['id']), 'job_id': job_id, 'source_revision': 1,
               'deletion_epoch': 0, 'request_id': event['request_id']}
    provider = ContractProvider()
    return GradingConsumer(execution, provider), provider, message


def fail():
    raise RuntimeError('SECRET private child data, signed URL and credentials')


def runs(admin_engine, message):
    with admin_engine.connect() as db:
        return db.execute(text('SELECT outcome,billing_state,cost_microyuan FROM model_runs WHERE event_id=:event_id'),
                          message).all()


def test_unconfigured_provider_is_rejected_before_claim(execution):
    with pytest.raises(RuntimeError, match='No released grading provider configured'):
        GradingConsumer(execution, None)


@pytest.mark.parametrize('change', [{'tenant_id': 'SECRET'}, {'request_id': ''},
                                    {'source_revision': True}, {'job_id': 'SECRET'}])
def test_invalid_envelope_never_prepares_or_claims(consumer_context, admin_engine, change):
    consumer, provider, message = consumer_context
    assert consumer.process(message | change) == 'invalid_message'
    assert provider.prepared == provider.sent == []
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM execution_receipts WHERE event_id=:event_id'), message).scalar_one() == 0


def test_flow_settles_once_and_duplicate_never_calls(consumer_context, admin_engine):
    consumer, provider, message = consumer_context
    assert consumer.process(message) == 'settled'
    assert consumer.process(message) == 'ignored'
    assert len(provider.sent) == len(provider.prepared) == 1
    state, counts = snapshot(admin_engine, provider.prepared[0])
    assert state[:7] == ('succeeded', 'completed', 'consumed', 3, 0, 3, 'settled')
    assert counts == (1, 1)
    assert runs(admin_engine, message) == [('succeeded', 'known', 123)]


def test_concurrent_delivery_has_one_provider_invocation(consumer_context, admin_engine):
    consumer, provider, message = consumer_context
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(consumer.process, [message] * 4))
    assert sorted(statuses) == ['ignored', 'ignored', 'ignored', 'settled']
    assert len(provider.sent) == 1
    assert snapshot(admin_engine, provider.prepared[0])[1] == (1, 1)


def test_preflight_failure_releases_without_call_and_bounded_retry(consumer_context, admin_engine):
    consumer, provider, message = consumer_context
    provider.before_prepare = fail
    for attempt in range(1, 4):
        assert consumer.process(message) == 'released'
        assert provider.sent == [] and runs(admin_engine, message) == []
        with admin_engine.connect() as db:
            row = db.execute(text('SELECT state,attempt FROM execution_receipts WHERE event_id=:event_id'), message).one()
        assert row == ('deadletter' if attempt == 3 else 'retry_wait', attempt)
        if attempt < 3:
            expire(admin_engine, message, retry=True)
    assert consumer.process(message) == 'ignored'
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT status FROM jobs WHERE id=:job_id'), message).scalar_one() == 'failed'
        assert db.execute(text('SELECT q.status FROM quota_reservations q JOIN jobs j USING(submission_id) WHERE j.id=:job_id'), message).scalar_one() == 'released'


@pytest.mark.parametrize('phase', ['prepare', 'invoke'])
@pytest.mark.parametrize('invalidate', ['cancel', 'consent', 'expiry'])
def test_revoked_or_expired_context_cannot_send_or_settle(consumer_context, admin_engine, client,
                                                        owner, student, phase, invalidate):
    consumer, provider, message = consumer_context

    def revoke():
        if invalidate == 'cancel':
            assert client.post(f"/api/v1/jobs/{message['job_id']}/cancel", headers=owner['headers']).status_code == 200
        elif invalidate == 'consent':
            assert client.post('/api/v1/consents', headers=owner['headers'], json={
                'student_id': student['id'], 'policy_version': '2026-10-25',
                'child_rules_version': '2026-10-25', 'granted': False}).status_code == 201
        else:
            expire(admin_engine, message)

    if phase == 'prepare':
        provider.before_prepare = revoke
    else:
        provider.before_send = revoke
    assert consumer.process(message) == 'stopped'
    assert len(provider.sent) == (1 if phase == 'invoke' else 0)
    if phase == 'invoke':
        assert runs(admin_engine, message) == [('succeeded', 'known', 123)]
    else:
        assert runs(admin_engine, message) == []
    assert snapshot(admin_engine, provider.prepared[0])[1] == (0, 0)


def test_timeout_after_call_fence_never_releases_or_retries(consumer_context, admin_engine):
    consumer, provider, message = consumer_context
    provider.before_send = fail
    assert consumer.process(message) == 'reconciliation_required'
    assert runs(admin_engine, message) == [('in_flight', 'unknown', None)]
    assert consumer.process(message) == 'ignored'
    expire(admin_engine, message)
    assert consumer.process(message) == 'ignored'
    assert len(provider.sent) == 1
    assert snapshot(admin_engine, provider.prepared[0])[0][:7] == (
        'running', 'processing', 'reserved', 0, 3, 0, 'running')


def test_lost_fence_commit_ack_cannot_trigger_send_or_release(consumer_context, admin_engine, monkeypatch):
    consumer, provider, message = consumer_context
    begin = consumer.store.begin_call

    def lost_ack(*args, **kwargs):
        assert begin(*args, **kwargs)
        fail()

    monkeypatch.setattr(consumer.store, 'begin_call', lost_ack)
    assert consumer.process(message) == 'reconciliation_required'
    assert provider.sent == []
    assert runs(admin_engine, message) == [('in_flight', 'unknown', None)]
    expire(admin_engine, message)
    assert consumer.process(message) == 'ignored'


@pytest.mark.parametrize('cost', [None, -1, True, 2**63])
def test_unknown_or_invalid_cost_is_not_zero_or_success(consumer_context, admin_engine, cost):
    consumer, provider, message = consumer_context
    provider.cost = cost
    assert consumer.process(message) == 'reconciliation_required'
    assert runs(admin_engine, message) == [('succeeded' if cost is None else 'in_flight', 'unknown', None)]
    assert snapshot(admin_engine, provider.prepared[0])[1] == (0, 0)
    assert consumer.process(message) == 'ignored'
    assert len(provider.sent) == 1


def test_invalid_result_preserves_valid_billing_not_result(consumer_context, admin_engine):
    consumer, provider, message = consumer_context
    provider.result_change = lambda result: result | {'SECRET': 'private content'}
    assert consumer.process(message) == 'reconciliation_required'
    assert runs(admin_engine, message) == [('succeeded', 'known', 123)]
    assert snapshot(admin_engine, provider.prepared[0])[1] == (0, 0)
    assert len(provider.sent) == 1


def test_known_provider_failure_settles_no_successful_pages(consumer_context, admin_engine):
    consumer, provider, message = consumer_context
    provider.outcome = 'failed'
    assert consumer.process(message) == 'settled'
    assert snapshot(admin_engine, provider.prepared[0])[0][:7] == (
        'failed', 'failed', 'released', 0, 0, 0, 'settled')
    assert runs(admin_engine, message) == [('failed', 'known', 123)]


def test_zero_is_only_accepted_when_explicitly_reported(consumer_context, admin_engine):
    consumer, provider, message = consumer_context
    provider.cost = 0
    assert consumer.process(message) == 'settled'
    assert runs(admin_engine, message) == [('succeeded', 'known', 0)]


def test_competing_delivery_while_provider_blocked_never_sends_twice(consumer_context):
    consumer, provider, message = consumer_context
    entered, unblock = Event(), Event()

    def wait():
        entered.set()
        assert unblock.wait(10)

    provider.before_send = wait
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(consumer.process, message)
        try:
            assert entered.wait(10)
            assert consumer.process(message) == 'ignored'
        finally:
            unblock.set()
        assert future.result(timeout=10) == 'settled'
    assert len(provider.sent) == 1


@pytest.mark.parametrize('method', ['finish_call', 'complete'])
def test_lost_postcall_commit_ack_does_not_repeat_business_effects(
        consumer_context, admin_engine, monkeypatch, method):
    consumer, provider, message = consumer_context
    original = getattr(consumer.store, method)

    def lost_ack(*args, **kwargs):
        assert original(*args, **kwargs)
        fail()

    monkeypatch.setattr(consumer.store, method, lost_ack)
    assert consumer.process(message) == 'reconciliation_required'
    assert consumer.process(message) == 'ignored'
    assert len(provider.sent) == 1
    assert runs(admin_engine, message) == [('succeeded', 'known', 123)]
    state, counts = snapshot(admin_engine, provider.prepared[0])
    if method == 'complete':
        assert counts == (1, 1)
        assert state[:7] == ('succeeded', 'completed', 'consumed', 3, 0, 3, 'settled')
    else:
        assert counts == (0, 0)
        assert state[:7] == ('running', 'processing', 'reserved', 0, 3, 0, 'running')


@pytest.mark.parametrize('method', ['claim', 'heartbeat'])
def test_database_outage_before_fence_never_sends(consumer_context, admin_engine, monkeypatch, method):
    consumer, provider, message = consumer_context
    monkeypatch.setattr(consumer.store, method, lambda *args: fail())
    assert consumer.process(message) == ('unavailable' if method == 'claim' else 'released')
    assert provider.sent == [] and runs(admin_engine, message) == []
