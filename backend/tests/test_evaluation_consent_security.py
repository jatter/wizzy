"""SQL capabilities validate real sessions and payloads independently of HTTP validation."""
import json
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_evaluation_consent import active, decision, endpoint, grant

from wizzy.security import digest


def request(db, token, student, *, payload=None, action='decide', request_id='evaluation-sql-test'):
    return db.execute(text('''SELECT public.evaluation_consent_request(:hash,:student,:action,
        CAST(:payload AS jsonb),:request)'''), {
        'hash': digest(token), 'student': student['id'], 'action': action,
        'payload': json.dumps(payload), 'request': request_id}).scalar_one()


@pytest.mark.parametrize('changes', [
    {'granted': 'true'}, {'granted': 1}, {'expected_revision': True}, {'expected_revision': '0'},
    {'expected_revision': -1}, {'expected_revision': 1000000000}, {'expected_revision': 0.5},
    {'operation_id': None}, {'operation_id': 'invalid'}, {'policy_version': None},
    {'policy_version': 'invented-policy'}, {'guardian_id': str(uuid4())},
    {'authorization_reference': 'caller-asserted-proof'},
])
def test_sql_rejects_payload_bypass(client, owner, student, changes):
    token = owner['headers']['Authorization'].split()[1]
    with client.app.state.engine.begin() as db:
        assert request(db, token, student, payload=decision() | changes)['status'] == 422
    assert client.get(endpoint(student), headers=owner['headers']).json()['data']['revision'] == 0


@pytest.mark.parametrize('kwargs', [
    {'payload': None}, {'payload': []}, {'payload': {}}, {'payload': 'text'},
    {'payload': {}, 'action': 'invented'}, {'payload': {}, 'action': None},
    {'payload': {}, 'action': 'read', 'request_id': None},
    {'payload': {}, 'action': 'read', 'request_id': ''},
    {'payload': {}, 'action': 'read', 'request_id': 'x'*101},
    {'payload': {'extra': True}, 'action': 'read'},
])
def test_sql_rejects_invalid_call_shape(client, owner, student, kwargs):
    token = owner['headers']['Authorization'].split()[1]
    with client.app.state.engine.begin() as db:
        assert request(db, token, student, **kwargs)['status'] == 422


def test_sql_ignores_forged_context_and_finds_real_session(client, owner, student):
    token = owner['headers']['Authorization'].split()[1]
    with client.app.state.engine.begin() as db:
        for key, value in (('tenant_id', owner['tenant_id']), ('student_id', student['id']), ('user_id', str(uuid4()))):
            db.execute(text('SELECT set_config(:key,:value,true)'), {'key': 'app.'+key, 'value': value})
        assert request(db, 'invented-token', student, payload=decision())['status'] == 401
        # A real proof must be the bearer secret's hash, never caller-supplied identity GUCs.
        assert request(db, token, student, payload={}, action='read')['status'] == 200
    state = client.get(endpoint(student), headers=owner['headers']).json()['data']
    assert state['revision'] == 0


@pytest.mark.parametrize('mutation', ["revoked_at=clock_timestamp()", "expires_at=clock_timestamp()-interval '1 second'"])
def test_expired_or_revoked_session_cannot_change_durable_decision(client, owner, student, admin_engine, mutation):
    proof = grant(client, owner, student)['decision']['id']
    token = owner['headers']['Authorization'].split()[1]
    with admin_engine.begin() as db:
        db.execute(text(f'UPDATE auth_sessions SET {mutation} WHERE token_hash=:hash'), {'hash': digest(token)})
    assert client.get(endpoint(student), headers=owner['headers']).status_code == 401
    assert client.post(endpoint(student), headers=owner['headers'], json=decision(granted=False, revision=1)).status_code == 401
    # Login lifetime is independent of a recorded guardian decision.
    assert active(admin_engine, proof)
    with client.app.state.engine.begin() as db:
        assert request(db, token, student, payload=decision(revision=1))['status'] == 401
