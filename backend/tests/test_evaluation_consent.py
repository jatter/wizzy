"""Evaluation permission is a distinct, revocable guardian decision in real PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import text

POLICY = 'evaluation-2026-10-26-v1'


def endpoint(student):
    return f"/api/v1/students/{student['id']}/evaluation-consent"


def decision(*, granted=True, revision=0, operation_id=None, policy=POLICY):
    return {'operation_id': str(operation_id or uuid4()), 'expected_revision': revision,
            'granted': granted, 'policy_version': policy}


def set_service_consent(client, owner, student, granted):
    result = client.post('/api/v1/consents', headers=owner['headers'], json={
        'student_id': student['id'], 'policy_version': '2026-10-25',
        'child_rules_version': '2026-10-25', 'granted': granted})
    assert result.status_code == 201, result.text


def grant(client, owner, student, **kwargs):
    response = client.post(endpoint(student), headers=owner['headers'], json=decision(**kwargs))
    assert response.status_code == 201, response.text
    return response.json()['data']


def active(admin_engine, authorization_id):
    with admin_engine.connect() as db:
        return db.execute(text('SELECT public.evaluation_authorization_active(:id)'),
                          {'id': authorization_id}).scalar_one()


def test_service_consent_does_not_grant_evaluation(client, owner, student):
    response = client.get(endpoint(student), headers=owner['headers'])
    assert response.status_code == 200, response.text
    state = response.json()['data']
    assert state['revision'] == 0
    assert state['granted'] is False and state['active'] is False
    assert state['authorization_id'] is None and state['last_decision'] is None
    assert state['reason'] == 'not_granted'
    assert state['policy_version'] == POLICY


def test_explicit_grant_is_recorded_with_real_guardian_and_session(client, owner, student, admin_engine):
    result = grant(client, owner, student)
    record, state = result['decision'], result['state']
    assert state['active'] is True and state['revision'] == 1
    assert state['authorization_id'] == record['id']
    assert state['last_decision'] == record and record['granted'] is True
    assert active(admin_engine, record['id']) is True
    with admin_engine.connect() as db:
        proof = db.execute(text('''SELECT e.student_id,e.source_epoch,e.policy_version,e.granted,
            a.user_id=e.guardian_id AND a.tenant_id=e.tenant_id AS bound
            FROM evaluation_consent_records e JOIN auth_sessions a ON a.id=e.session_id
            WHERE e.id=:id'''), {'id': record['id']}).mappings().one()
        assert str(proof['student_id']) == student['id']
        assert proof['bound'] and proof['granted'] and proof['source_epoch'] == 0
        assert proof['policy_version'] == POLICY
        assert db.execute(text("SELECT count(*) FROM audit_events WHERE resource_id=:id AND action='evaluation.consent.granted'"),
                          {'id': student['id']}).scalar_one() == 1


def test_evaluation_withdrawal_keeps_service_available(client, owner, student, admin_engine):
    first = grant(client, owner, student)
    withdrawn = grant(client, owner, student, granted=False, revision=1)
    assert withdrawn['state']['active'] is False and withdrawn['state']['granted'] is False
    assert withdrawn['state']['reason'] == 'withdrawn'
    assert not active(admin_engine, first['decision']['id'])
    assert not active(admin_engine, withdrawn['decision']['id'])
    profile = client.get(f"/api/v1/students/{student['id']}", headers=owner['headers'])
    assert profile.status_code == 200 and profile.json()['data']['consent_active'] is True
    assert client.post(f"/api/v1/students/{student['id']}/pairing-codes", headers=owner['headers']).status_code == 201
    renewed = grant(client, owner, student, revision=2)
    assert renewed['state']['active'] is True
    assert renewed['decision']['id'] != first['decision']['id']
    assert not active(admin_engine, first['decision']['id'])


def test_service_withdrawal_invalidates_evaluation_without_resurrection(client, owner, student, admin_engine):
    first = grant(client, owner, student)
    set_service_consent(client, owner, student, False)
    state = client.get(endpoint(student), headers=owner['headers']).json()['data']
    assert state['granted'] is True and state['active'] is False
    assert state['reason'] == 'service_consent_required'
    assert not active(admin_engine, first['decision']['id'])
    assert client.post(endpoint(student), headers=owner['headers'], json=decision(revision=1)).status_code == 403
    set_service_consent(client, owner, student, True)
    state = client.get(endpoint(student), headers=owner['headers']).json()['data']
    assert state['active'] is False and state['reason'] == 'source_epoch_changed'
    assert state['revision'] == 1
    renewed = grant(client, owner, student, revision=1)
    assert renewed['state']['active'] is True
    assert not active(admin_engine, first['decision']['id'])


def test_revoke_is_allowed_after_service_withdrawal(client, owner, student):
    grant(client, owner, student)
    set_service_consent(client, owner, student, False)
    revoked = grant(client, owner, student, granted=False, revision=1)
    assert revoked['state']['reason'] == 'withdrawn'


def test_retry_does_not_repeat_or_resurrect_a_grant(client, owner, student, admin_engine):
    payload = decision()
    first = client.post(endpoint(student), headers=owner['headers'], json=payload)
    assert first.status_code == 201, first.text
    original = first.json()['data']
    retry = client.post(endpoint(student), headers=owner['headers'], json=payload)
    assert retry.status_code == 200 and retry.json()['data'] == original
    grant(client, owner, student, granted=False, revision=1)
    retry = client.post(endpoint(student), headers=owner['headers'], json=payload)
    assert retry.status_code == 200, retry.text
    assert retry.json()['data']['decision'] == original['decision']
    assert retry.json()['data']['state']['active'] is False
    assert retry.json()['data']['state']['revision'] == 2
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM evaluation_consent_records WHERE student_id=:id'),
                          {'id': student['id']}).scalar_one() == 2
        assert db.execute(text("SELECT count(*) FROM audit_events WHERE resource_id=:id AND action LIKE 'evaluation.consent.%'"),
                          {'id': student['id']}).scalar_one() == 2


def test_operation_key_cannot_be_reused_with_different_decision(client, owner, student):
    payload = decision()
    assert client.post(endpoint(student), headers=owner['headers'], json=payload).status_code == 201
    for changes in ({'granted': False}, {'expected_revision': 1}):
        response = client.post(endpoint(student), headers=owner['headers'], json=payload | changes)
        assert response.status_code == 409, response.text


def test_stale_revision_cannot_overwrite_withdrawal(client, owner, student):
    grant(client, owner, student)
    grant(client, owner, student, granted=False, revision=1)
    response = client.post(endpoint(student), headers=owner['headers'], json=decision(revision=1))
    assert response.status_code == 409, response.text
    state = client.get(endpoint(student), headers=owner['headers']).json()['data']
    assert state['revision'] == 2 and state['active'] is False


def test_concurrent_first_decisions_have_one_winner(client, owner, student, admin_engine):
    def write(granted):
        return client.post(endpoint(student), headers=owner['headers'], json=decision(granted=granted))
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(write, (True, False)))
    assert sorted(row.status_code for row in responses) == [201, 409]
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM evaluation_consent_records WHERE student_id=:id'),
                          {'id': student['id']}).scalar_one() == 1


def test_concurrent_identical_decisions_have_one_record(client, owner, student):
    payload = decision()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post(endpoint(student), headers=owner['headers'], json=payload), range(2)))
    assert sorted(row.status_code for row in responses) == [200, 201]
    assert responses[0].json()['data'] == responses[1].json()['data']


@pytest.mark.parametrize('changes', [
    {'granted': 'true'}, {'granted': 1}, {'expected_revision': True}, {'expected_revision': '0'},
    {'expected_revision': -1}, {'policy_version': 'invented-policy'}, {'policy_version': None},
    {'authorization_reference': 'caller-asserted-proof'}, {'guardian_id': str(uuid4())},
    {'operation_id': 'not-a-uuid'}, {'expected_revision': 1000000000},
])
def test_strict_consent_payload(client, owner, student, changes):
    response = client.post(endpoint(student), headers=owner['headers'], json=decision() | changes)
    assert response.status_code == 422, response.text


def test_public_policy_is_explicit_and_not_a_training_grant(client):
    response = client.get('/api/v1/evaluation-policy')
    assert response.status_code == 200, response.text
    policy = response.json()['data']
    assert policy['version'] == POLICY and policy['purpose'] == 'offline_grading_evaluation'
    assert policy['training_allowed'] is False and policy['public_sharing_allowed'] is False
    assert policy['provider_transfer_allowed'] is False
    assert policy['optional'] is True
    assert '撤回' in policy['text'] and '脱敏' in policy['text']


def test_only_authorized_guardian_can_read_and_decide(client, owner, student):
    from test_identity_security import registered

    code = client.post(f"/api/v1/students/{student['id']}/pairing-codes",
                       headers=owner['headers']).json()['data']['code']
    child = client.post('/api/v1/child-sessions', json={'code': code, 'device_name': 'test'}).json()['data']
    child_headers = {'Authorization': 'Bearer '+child['access_token']}
    _, stranger = registered(client)
    tenant = client.post('/api/v1/tenants', headers={'Authorization': 'Bearer '+stranger['access_token']},
                         json={'name': '其他家庭'}).json()['data']
    other_headers = {'Authorization': 'Bearer '+tenant['access_token']}
    for method in ('get', 'post'):
        kwargs = {'json': decision()} if method == 'post' else {}
        call = getattr(client, method)
        assert call(endpoint(student), **kwargs).status_code == 401
        assert call(endpoint(student), headers=child_headers, **kwargs).status_code == 403
        assert call(endpoint(student), headers=other_headers, **kwargs).status_code == 404


def test_scoped_guardian_loses_access_after_removal(client, owner, student, admin_engine):
    from test_identity_security import registered

    email, user = registered(client)
    invitation = client.post('/api/v1/guardian-invitations', headers=owner['headers'],
        json={'email': email, 'student_ids': [student['id']]}).json()['data']['code']
    accepted = client.post('/api/v1/guardian-invitations/accept',
        headers={'Authorization': 'Bearer '+user['access_token']}, json={'code': invitation}).json()['data']
    guardian = {'headers': {'Authorization': 'Bearer '+accepted['access_token']}}
    other = client.post('/api/v1/students', headers=owner['headers'],
                        json={'nickname': '其他孩子', 'grade': 4}).json()['data']
    assert client.get(endpoint(other), headers=guardian['headers']).status_code == 404
    result = grant(client, guardian, student)
    assert client.delete('/api/v1/tenants/members/'+user['user_id'], headers=owner['headers']).status_code == 200
    assert client.get(endpoint(student), headers=guardian['headers']).status_code == 401
    assert client.post(endpoint(student), headers=guardian['headers'], json=decision(revision=1)).status_code == 401
    assert not active(admin_engine, result['decision']['id'])


def test_grant_requires_service_consent_but_read_and_revoke_do_not(client, owner):
    student = client.post('/api/v1/students', headers=owner['headers'],
                          json={'nickname': '未授权孩子', 'grade': 4}).json()['data']
    assert client.get(endpoint(student), headers=owner['headers']).status_code == 200
    assert client.post(endpoint(student), headers=owner['headers'], json=decision()).status_code == 403
    assert grant(client, owner, student, granted=False)['state']['reason'] == 'withdrawn'


@pytest.mark.parametrize('table,changes', [
    ('tenants', "deletion_epoch=deletion_epoch+1"), ('tenants', "status='suspended'"),
    ('students', "deletion_epoch=deletion_epoch+1"), ('students', "status='disabled'"),
])
def test_source_lifecycle_invalidates_proof(client, owner, student, admin_engine, table, changes):
    result = grant(client, owner, student)
    with admin_engine.begin() as db:
        db.execute(text(f'UPDATE {table} SET {changes} WHERE id=:id'),
                   {'id': owner['tenant_id'] if table == 'tenants' else student['id']})
    assert not active(admin_engine, result['decision']['id'])


def test_audit_failure_rolls_back_decision_and_state(client, owner, student, admin_engine):
    from fastapi.testclient import TestClient

    with admin_engine.begin() as db:
        db.execute(text('''CREATE FUNCTION public.fail_eval_audit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.action LIKE 'evaluation.consent.%' THEN RAISE EXCEPTION 'audit unavailable';
            END IF; RETURN NEW; END $$;
            CREATE TRIGGER fail_eval_audit BEFORE INSERT ON public.audit_events
            FOR EACH ROW EXECUTE FUNCTION public.fail_eval_audit()'''))
    try:
        with TestClient(client.app, raise_server_exceptions=False) as failing:
            response = failing.post(endpoint(student), headers=owner['headers'], json=decision())
            assert response.status_code == 500, response.text
            assert response.json()['error']['code'] == 'internal_error'
        with admin_engine.connect() as db:
            for table in ('evaluation_consent_records', 'evaluation_consent_states'):
                assert db.execute(text(f'SELECT count(*) FROM {table} WHERE student_id=:id'),
                                  {'id': student['id']}).scalar_one() == 0
    finally:
        with admin_engine.begin() as db:
            db.execute(text('DROP TRIGGER fail_eval_audit ON public.audit_events; DROP FUNCTION public.fail_eval_audit()'))
    assert grant(client, owner, student)['state']['revision'] == 1
