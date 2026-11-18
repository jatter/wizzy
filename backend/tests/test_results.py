"""Isolated PostgreSQL writeback tests. Payloads/usage are synthetic, not AI evidence."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_execution import execution as execution  # noqa: F401
from test_execution import expire, wait_for_blocker
from test_media import upload
from test_submissions import payload, submit


@pytest.fixture
def result_context(execution, client, owner, student, admin_engine):
    images = [upload(client, owner['headers'], student['id']) for _ in range(3)]
    response = submit(client, owner['headers'], payload(student['id'], images))
    assert response.status_code == 202
    receipt = response.json()['data']
    with admin_engine.connect() as db:
        event = db.execute(text('SELECT id,request_id FROM outbox_events WHERE aggregate_id=:id'),
                           {'id': receipt['job_id']}).mappings().one()
    message = {'event_id': str(event['id']), 'job_id': receipt['job_id'], 'source_revision': 1,
               'deletion_epoch': 0, 'request_id': event['request_id']}
    lease = execution.claim(message)
    call = execution.begin_call(lease, stage='structure', provider='contract-test',
                                model='synthetic-model', model_version='test-v1')
    return lease, call, message


def document(call, *, succeeded=3):
    pages = []
    for position in range(1, 4):
        if position > succeeded:
            pages.append({'position': position, 'status': 'failed', 'error_code': 'invalid_model_output'})
            continue
        pages.append({'position': position, 'status': 'succeeded', 'model_run_id': str(call['run_id']),
                      'questions': [{
                          'ordinal': 1, 'question_type': 'integer_arithmetic', 'stem': '12 + 8 = ?',
                          'student_answer_raw': '21', 'bbox': [0.1, 0.2, 0.8, 0.4],
                          'ocr_confidence': None, 'verdict': 'pending_review',
                          'reason_codes': ['UNCALIBRATED_GATE'], 'needs_guardian_review': True,
                          'evidence': [{'kind': 'answer_region', 'page': position,
                                        'bbox': [0.5, 0.2, 0.8, 0.4]}],
                      }]})
    return {'schema_version': '1.0-conservative', 'prompt_hash': 'a' * 64,
            'rule_version': 'unreleased', 'eval_set_version': 'unreleased',
            'image_pipeline_version': 'test-v1', 'pages': pages}


def finish(execution, lease, call, *, outcome='succeeded', cost=123):
    assert execution.finish_call(lease, call, outcome=outcome,
                                 provider_request_id='synthetic-receipt', cost_microyuan=cost)


def complete(execution, lease, result):
    method = getattr(execution, 'complete', None)
    assert callable(method), 'Atomic result writeback and quota settlement are missing'
    return method(lease, result)


def snapshot(admin_engine, lease):
    with admin_engine.connect() as db:
        state = db.execute(text('''SELECT j.status,s.status,q.status,q.consumed_pages,
            a.reserved_pages,a.used_pages,r.state,r.lease_token,r.lease_until
            FROM jobs j JOIN submissions s ON s.id=j.submission_id
            JOIN quota_reservations q ON q.submission_id=s.id
            JOIN quota_accounts a ON a.tenant_id=q.tenant_id AND a.period_start=q.period_start
            JOIN execution_receipts r ON r.job_id=j.id WHERE j.id=:job_id'''), lease).one()
        counts = db.execute(text('''SELECT
            (SELECT count(*) FROM grading_results WHERE event_id=:event_id),
            (SELECT count(*) FROM outbox_events WHERE aggregate_id=:job_id
                AND event_type='submission.processed')'''), lease).one()
    return tuple(state), tuple(counts)


@pytest.mark.parametrize(('successes', 'job', 'submission', 'reservation'), [
    (3, 'succeeded', 'completed', 'consumed'),
    (2, 'succeeded', 'partial', 'consumed'),
    (0, 'failed', 'failed', 'released'),
])
def test_result_quota_and_event_commit_once(execution, result_context, admin_engine, client, owner,
                                          successes, job, submission, reservation):
    lease, call, message = result_context
    finish(execution, lease, call, outcome='failed' if successes == 0 else 'succeeded')
    result = document(call, succeeded=successes)
    assert complete(execution, lease, result)
    expected = ((job, submission, reservation, successes, 0, successes, 'settled', None, None), (1, 1))
    assert snapshot(admin_engine, lease) == expected
    assert complete(execution, lease, deepcopy(result))
    assert snapshot(admin_engine, lease) == expected
    assert not execution.heartbeat(lease) and not execution.release(lease)
    assert execution.claim(message) is None
    with admin_engine.connect() as db:
        saved = db.execute(text('SELECT payload FROM grading_results WHERE event_id=:event_id'), lease).scalar_one()
        assert saved == result
        event = db.execute(text("SELECT payload FROM outbox_events WHERE aggregate_id=:job_id AND event_type='submission.processed'"), lease).scalar_one()
        assert event == {'submission_id': str(lease['submission_id']), 'source_revision': 1,
                         'deletion_epoch': 0, 'status': submission, 'successful_pages': successes,
                         'failed_pages': 3 - successes}
    status = client.get(f"/api/v1/submissions/{lease['submission_id']}", headers=owner['headers'])
    assert status.json()['data']['status'] == submission
    # Internal results are not accidentally exposed before audience-safe serializers exist.
    assert 'questions' not in status.json()['data']


def test_conflicting_result_is_immutable(execution, result_context, admin_engine):
    lease, call, _ = result_context
    finish(execution, lease, call)
    result = document(call)
    assert complete(execution, lease, result)
    before = snapshot(admin_engine, lease)
    changed = deepcopy(result)
    changed['pages'][0]['questions'][0]['student_answer_raw'] = '20'
    assert not complete(execution, lease, changed)
    assert not complete(execution, lease | {'lease_token': uuid4()}, result)
    assert snapshot(admin_engine, lease) == before


@pytest.mark.parametrize('receipt', ['missing', 'unknown', 'failed', 'additional_in_flight'])
def test_unfinished_or_failed_calls_cannot_create_success(execution, result_context, admin_engine, receipt):
    lease, call, _ = result_context
    if receipt != 'missing':
        finish(execution, lease, call, outcome='failed' if receipt == 'failed' else 'succeeded',
               cost=None if receipt == 'unknown' else 123)
    if receipt == 'additional_in_flight':
        assert execution.begin_call(lease, stage='verify', provider='contract-test', model='test', model_version='v1')
    assert not complete(execution, lease, document(call))
    state, counts = snapshot(admin_engine, lease)
    assert state[:8] == ('running', 'processing', 'reserved', 0, 3, 0, 'running', lease['lease_token'])
    assert state[8] is not None and counts == (0, 0)
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT reserved_pages,used_pages FROM quota_accounts WHERE tenant_id=:tenant_id'), lease).one() == (3, 0)


@pytest.mark.parametrize('invalidate', ['cancel', 'consent', 'epoch', 'inactive', 'tenant', 'revision', 'expiry', 'token'])
def test_late_results_never_revive_invalid_execution(execution, result_context, client, owner,
                                                   student, admin_engine, invalidate):
    lease, call, message = result_context
    finish(execution, lease, call)
    if invalidate == 'cancel':
        assert client.post(f"/api/v1/jobs/{lease['job_id']}/cancel", headers=owner['headers']).status_code == 200
    elif invalidate == 'consent':
        assert client.post('/api/v1/consents', headers=owner['headers'], json={
            'student_id': student['id'], 'policy_version': '2026-10-25',
            'child_rules_version': '2026-10-25', 'granted': False,
        }).status_code == 201
    elif invalidate == 'expiry':
        expire(admin_engine, message)
    elif invalidate == 'token':
        lease = lease | {'lease_token': uuid4()}
    else:
        statements = {
            'epoch': 'UPDATE students SET deletion_epoch=deletion_epoch+1 WHERE id=:id',
            'inactive': "UPDATE students SET status='disabled' WHERE id=:id",
            'tenant': "UPDATE tenants SET status='suspended' WHERE id=:id",
            'revision': 'UPDATE execution_receipts SET source_revision=2 WHERE student_id=:id',
        }
        with admin_engine.begin() as db:
            db.execute(text(statements[invalidate]), {'id': owner['tenant_id'] if invalidate == 'tenant' else student['id']})
    before = snapshot(admin_engine, lease)
    assert not complete(execution, lease, document(call))
    assert snapshot(admin_engine, lease) == before


def test_concurrent_result_and_cancel_have_single_settlement(execution, result_context, client, owner, admin_engine):
    lease, call, _ = result_context
    finish(execution, lease, call)
    barrier = Barrier(2, timeout=10)

    def write():
        barrier.wait()
        return complete(execution, lease, document(call, succeeded=2))

    def cancel():
        barrier.wait()
        return client.post(f"/api/v1/jobs/{lease['job_id']}/cancel", headers=owner['headers']).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        writing, cancelling = pool.submit(write), pool.submit(cancel)
        accepted, status = writing.result(timeout=10), cancelling.result(timeout=10)
    state, counts = snapshot(admin_engine, lease)
    if accepted:
        assert status == 409
        assert state[:7] == ('succeeded', 'partial', 'consumed', 2, 0, 2, 'settled')
        assert counts == (1, 1)
    else:
        assert status == 200
        assert state[:6] == ('cancelled', 'cancelled', 'released', 0, 0, 0)
        assert counts == (0, 0)


def test_waiting_result_rechecks_withdrawal(execution, result_context, student, admin_engine):
    lease, call, _ = result_context
    finish(execution, lease, call)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with admin_engine.begin() as db:
            blocker = db.execute(text('SELECT pg_backend_pid()')).scalar_one()
            db.execute(text('UPDATE students SET consent_active=false,deletion_epoch=deletion_epoch+1 WHERE id=:id'), {'id': student['id']})
            future = pool.submit(complete, execution, lease, document(call))
            wait_for_blocker(admin_engine, blocker)
            assert not future.done()
        assert not future.result(timeout=5)
    assert snapshot(admin_engine, lease)[1] == (0, 0)


def test_concurrent_conflicting_results_only_one_wins(execution, result_context, admin_engine):
    lease, call, _ = result_context
    finish(execution, lease, call)
    documents = [document(call, succeeded=n) for n in (1, 2, 3)]
    barrier = Barrier(3, timeout=10)

    def write(result):
        barrier.wait()
        return complete(execution, lease, result)

    with ThreadPoolExecutor(max_workers=3) as pool:
        accepted = list(pool.map(write, documents))
    assert sum(accepted) == 1
    winner = accepted.index(True) + 1
    assert snapshot(admin_engine, lease)[0][3:6] == (winner, 0, winner)
    assert snapshot(admin_engine, lease)[1] == (1, 1)



def test_quota_lock_wait_rechecks_expired_lease(execution, result_context, admin_engine):
    lease, call, _ = result_context
    finish(execution, lease, call)
    with admin_engine.begin() as db:
        db.execute(text("UPDATE execution_receipts SET lease_until=clock_timestamp()+interval '3 seconds' WHERE event_id=:event_id"), lease)
    before = snapshot(admin_engine, lease)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with admin_engine.begin() as db:
            blocker = db.execute(text('SELECT pg_backend_pid()')).scalar_one()
            db.execute(text('SELECT 1 FROM quota_accounts WHERE tenant_id=:tenant_id FOR UPDATE'), lease)
            future = pool.submit(complete, execution, lease, document(call))
            wait_for_blocker(admin_engine, blocker)
            assert not future.done()
            deadline = time.monotonic() + 10
            while True:
                expired = db.execute(text('SELECT lease_until<=clock_timestamp() FROM execution_receipts WHERE event_id=:event_id'), lease).scalar_one()
                if expired:
                    break
                assert time.monotonic() < deadline, 'Execution lease did not expire'
                time.sleep(0.01)
        assert not future.result(timeout=5)
    assert snapshot(admin_engine, lease) == before


@pytest.mark.parametrize('invalidate', ['consent', 'epoch'])
def test_settled_replay_rechecks_authorization(execution, result_context, client, owner,
                                             student, admin_engine, invalidate):
    lease, call, _ = result_context
    finish(execution, lease, call)
    result = document(call)
    assert complete(execution, lease, result)
    if invalidate == 'consent':
        assert client.post('/api/v1/consents', headers=owner['headers'], json={
            'student_id': student['id'], 'policy_version': '2026-10-25',
            'child_rules_version': '2026-10-25', 'granted': False,
        }).status_code == 201
    else:
        with admin_engine.begin() as db:
            db.execute(text('UPDATE students SET deletion_epoch=deletion_epoch+1 WHERE id=:id'), {'id': student['id']})
    before = snapshot(admin_engine, lease)
    assert not complete(execution, lease, deepcopy(result))
    assert snapshot(admin_engine, lease) == before
    assert before[0][3:6] == (3, 0, 3) and before[1] == (1, 1)


def test_concurrent_identical_results_settle_once(execution, result_context, admin_engine):
    lease, call, _ = result_context
    finish(execution, lease, call)
    result = document(call, succeeded=2)
    barrier = Barrier(3, timeout=10)

    def write(_):
        barrier.wait()
        return complete(execution, lease, deepcopy(result))

    with ThreadPoolExecutor(max_workers=3) as pool:
        assert list(pool.map(write, range(3))) == [True, True, True]
    assert snapshot(admin_engine, lease) == (
        ('succeeded', 'partial', 'consumed', 2, 0, 2, 'settled', None, None), (1, 1))

def test_result_transaction_rolls_back_all_effects(execution, result_context, admin_engine):
    lease, call, _ = result_context
    finish(execution, lease, call)
    with admin_engine.begin() as db:
        db.execute(text('''CREATE FUNCTION public.reject_result_event() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.event_type='submission.processed' THEN RAISE EXCEPTION 'test rollback'; END IF;
            RETURN NEW; END $$;
            CREATE TRIGGER test_reject_result_event BEFORE INSERT ON outbox_events
            FOR EACH ROW EXECUTE FUNCTION public.reject_result_event();'''))
    before = snapshot(admin_engine, lease)
    try:
        with pytest.raises(DBAPIError):
            complete(execution, lease, document(call))
        assert snapshot(admin_engine, lease) == before
    finally:
        with admin_engine.begin() as db:
            db.execute(text('DROP TRIGGER test_reject_result_event ON outbox_events; DROP FUNCTION public.reject_result_event()'))
    assert complete(execution, lease, document(call))


def test_result_permissions(execution, result_context, database, admin_engine):
    lease, call, _ = result_context
    finish(execution, lease, call)
    assert complete(execution, lease, document(call))
    for url in (database['url'], database['dispatcher_url'], database['worker_url']):
        engine = create_engine(url, hide_parameters=True)
        try:
            for statement in ('SELECT * FROM grading_results', 'DELETE FROM grading_results',
                              'UPDATE quota_reservations SET consumed_pages=pages'):
                with engine.begin() as db, pytest.raises(DBAPIError):
                    db.execute(text(statement))
            if url != database['worker_url']:
                with engine.begin() as db, pytest.raises(DBAPIError):
                    db.execute(text('SELECT public.complete_execution(:event_id,:job_id,:source_revision,:deletion_epoch,:lease_token,CAST(:payload AS jsonb))'),
                               lease | {'payload': '{}'})
        finally:
            engine.dispose()
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid='public.grading_results'::regclass")).scalar_one()
        assert db.execute(text("SELECT prosecdef AND proconfig=ARRAY['search_path=pg_catalog, pg_temp'] FROM pg_proc WHERE oid='public.complete_execution(uuid,uuid,integer,bigint,uuid,jsonb)'::regprocedure")).scalar_one()


INVALID_RESULTS = [
    (('schema_version',), '1.0'),
    (('prompt_hash',), 'untracked'),
    (('rule_version',), ''),
    (('pages',), []),
    (('pages', 0, 'position'), True),
    (('pages', 0, 'position'), 1.5),
    (('pages', 0, 'position'), 10),
    (('pages', 1, 'position'), 1),
    (('pages', 0, 'questions'), []),
    (('pages', 0, 'questions', 0, 'verdict'), 'correct'),
    (('pages', 0, 'questions', 0, 'verdict'), 'incorrect'),
    (('pages', 0, 'questions', 0, 'verdict'), 'unanswered'),
    (('pages', 0, 'questions', 0, 'needs_guardian_review'), False),
    (('pages', 0, 'questions', 0, 'bbox'), [0.8, 0.2, 0.1, 0.4]),
    (('pages', 0, 'questions', 0, 'bbox'), [-0.1, 0.2, 0.8, 0.4]),
    (('pages', 0, 'questions', 0, 'bbox'), [True, 0.2, 0.8, 0.4]),
    (('pages', 0, 'questions', 0, 'evidence'), []),
    (('pages', 0, 'questions', 0, 'evidence', 0, 'page'), 2),
    (('pages', 0, 'questions', 0, 'evidence', 0, 'bbox'), [0, 0, 0, 0]),
    (('pages', 0, 'questions', 0, 'evidence', 0, 'kind'), 'guess'),
    (('pages', 0, 'questions', 0, 'reason_codes'), []),
    (('pages', 0, 'questions', 0, 'reason_codes'), ['LOOKS_CORRECT']),
    (('pages', 0, 'questions', 0, 'stem'), 'x' * 8001),
    (('pages', 0, 'questions', 0, 'stem'), 'private\x00text'),
    (('pages', 0, 'questions', 0, 'question_type'), 'invented'),
    (('pages', 0, 'questions', 0, 'ordinal'), 0),
    (('pages', 0, 'questions', 0, 'reference_answer'), '20'),
    (('pages', 0, 'model_run_id'), 'not-a-uuid'),
]


@pytest.mark.parametrize(('path', 'value'), INVALID_RESULTS)
@pytest.mark.parametrize('boundary', ['python', 'database'])
def test_invalid_result_rejected_at_both_boundaries(execution, result_context, admin_engine,
                                                   path, value, boundary):
    from wizzy.execution import InvalidMessage

    lease, call, _ = result_context
    finish(execution, lease, call)
    result = document(call)
    node = result
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    before = snapshot(admin_engine, lease)
    if boundary == 'python':
        with pytest.raises(InvalidMessage):
            complete(execution, lease, result)
    else:
        # PostgreSQL JSONB itself rejects NUL before the function is entered.
        if value == 'private\x00text':
            with execution.engine.begin() as db, pytest.raises(DBAPIError):
                db.execute(text('SELECT public.complete_execution(:event_id,:job_id,:source_revision,:deletion_epoch,:lease_token,CAST(:payload AS jsonb))'),
                           lease | {'payload': json.dumps(result)})
        else:
            with execution.engine.begin() as db:
                assert not db.execute(text('SELECT public.complete_execution(:event_id,:job_id,:source_revision,:deletion_epoch,:lease_token,CAST(:payload AS jsonb))'),
                                      lease | {'payload': json.dumps(result)}).scalar_one()
    assert snapshot(admin_engine, lease) == before


@pytest.mark.parametrize('mismatch', ['page_count', 'run', 'stage', 'no_call'])
def test_result_must_match_actual_submission_and_call(execution, result_context, admin_engine, mismatch):
    lease, call, _ = result_context
    finish(execution, lease, call)
    result = document(call)
    if mismatch == 'page_count':
        result['pages'].pop()
    elif mismatch == 'run':
        result['pages'][0]['model_run_id'] = str(uuid4())
    else:
        with admin_engine.begin() as db:
            if mismatch == 'stage':
                db.execute(text("UPDATE model_runs SET stage='tutor' WHERE id=:run_id"), call)
            else:
                db.execute(text('DELETE FROM model_runs WHERE id=:run_id'), call)
    before = snapshot(admin_engine, lease)
    assert not complete(execution, lease, result)
    assert snapshot(admin_engine, lease) == before


def test_unsupported_and_zero_cost_are_not_correctness(execution, result_context, admin_engine):
    lease, call, _ = result_context
    finish(execution, lease, call, cost=0)
    result = document(call)
    for page in result['pages']:
        page['questions'][0].update(verdict='unsupported', reason_codes=['UNSUPPORTED_TYPE'])
    assert complete(execution, lease, result)
    assert snapshot(admin_engine, lease)[0][3:6] == (3, 0, 3)
