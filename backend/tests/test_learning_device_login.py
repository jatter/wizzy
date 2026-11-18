"""Remembered APP credentials remain child-only and revocable after session expiry."""
from uuid import uuid4

from sqlalchemy import text


def pair_device(client, owner, student):
    code = client.post(f"/api/v1/students/{student['id']}/pairing-codes", headers=owner['headers']).json()['data']['code']
    paired = client.post('/api/v1/child-sessions', json={'code': code, 'device_name': '学习平板'}).json()['data']
    return paired, {'Authorization': f"Bearer {paired['access_token']}"}


def test_remembered_device_restores_expired_session_without_guardian_permissions(client, owner, student, admin_engine):
    paired, headers = pair_device(client, owner, student)
    enrolled = client.post('/api/v1/auth/learning-device/register', headers=headers)
    assert enrolled.status_code == 201, enrolled.text
    key = enrolled.json()['data']['device_token']
    with admin_engine.begin() as db:
        assert db.execute(text('SELECT token_hash FROM learning_devices WHERE session_id=:id'), {'id': paired['session_id']}).scalar_one() != key
        db.execute(text("UPDATE auth_sessions SET expires_at=now()-interval '1 second' WHERE id=:id"), {'id': paired['session_id']})
    assert client.get('/api/v1/auth/me', headers=headers).status_code == 401
    response = client.post('/api/v1/auth/learning-device/resume', json={'device_token': key})
    assert response.status_code == 200, response.text
    restored = {'Authorization': f"Bearer {response.json()['data']['access_token']}"}
    identity = client.get('/api/v1/auth/me', headers=restored).json()['data']
    assert identity['audience'] == 'child' and identity['student_id'] == student['id']
    assert client.get('/api/v1/tenants', headers=restored).status_code == 403
    assert client.get('/api/v1/auth/me', headers=headers).status_code == 401
    assert any(d['id'] == paired['session_id'] for d in client.get('/api/v1/child-sessions', headers=owner['headers']).json()['data']['items'])
    assert client.delete(f"/api/v1/child-sessions/{paired['session_id']}", headers=owner['headers']).status_code == 200
    assert client.post('/api/v1/auth/learning-device/resume', json={'device_token': key}).status_code == 401
    assert client.get('/api/v1/auth/me', headers=restored).status_code == 401


def test_only_paired_child_can_register_remembered_device(client, owner):
    assert client.post('/api/v1/auth/learning-device/register', headers=owner['headers']).status_code == 403
    assert client.post('/api/v1/auth/learning-device/register').status_code == 401
    assert client.post('/api/v1/auth/learning-device/resume', json={'device_token': uuid4().hex}).status_code == 401


def test_revocation_still_lists_expired_remembered_device(client, owner, student, admin_engine):
    paired, headers = pair_device(client, owner, student)
    assert client.post('/api/v1/auth/learning-device/register', headers=headers).status_code == 201
    with admin_engine.begin() as db:
        db.execute(text("UPDATE auth_sessions SET expires_at=now()-interval '1 day' WHERE id=:id"), {'id': paired['session_id']})
    devices = client.get('/api/v1/child-sessions', headers=owner['headers']).json()['data']['items']
    assert any(item['id'] == paired['session_id'] for item in devices)


def test_device_expiry_and_replacement_reject_old_credentials(client, owner, student, admin_engine):
    paired, headers = pair_device(client, owner, student)
    old = client.post('/api/v1/auth/learning-device/register', headers=headers).json()['data']['device_token']
    new = client.post('/api/v1/auth/learning-device/register', headers=headers).json()['data']['device_token']
    assert client.post('/api/v1/auth/learning-device/resume', json={'device_token': old}).status_code == 401
    with admin_engine.begin() as db:
        db.execute(text("UPDATE learning_devices SET expires_at=now()-interval '1 second' WHERE session_id=:id"), {'id': paired['session_id']})
    assert client.post('/api/v1/auth/learning-device/resume', json={'device_token': new}).status_code == 401


def test_consent_withdrawal_disables_device_resume(client, owner, student):
    _, headers = pair_device(client, owner, student)
    key = client.post('/api/v1/auth/learning-device/register', headers=headers).json()['data']['device_token']
    assert client.post('/api/v1/consents', headers=owner['headers'], json={'student_id': student['id'], 'policy_version': '2026-10-25', 'child_rules_version': '2026-10-25', 'granted': False}).status_code == 201
    assert client.post('/api/v1/auth/learning-device/resume', json={'device_token': key}).status_code == 401


def test_cookie_resume_keeps_device_key_out_of_browser_response(client, owner, student):
    _, headers = pair_device(client, owner, student)
    key = client.post('/api/v1/auth/learning-device/register', headers=headers).json()['data']['device_token']
    browser = {'X-Family-Session': 'browser', 'Origin': 'http://testserver'}
    response = client.post('/api/v1/auth/learning-device/resume', headers=browser, json={'device_token': key})
    assert response.status_code == 200, response.text
    assert response.json()['data']['token_type'] == 'cookie'
    assert 'access_token' not in response.json()['data'] and key not in response.text
    assert 'HttpOnly' in response.headers['set-cookie']
    assert client.get('/api/v1/auth/me', headers=browser).json()['data']['audience'] == 'child'
    assert client.post('/api/v1/auth/logout', headers=browser).status_code == 200
    assert client.post('/api/v1/auth/learning-device/resume', json={'device_token': key}).status_code == 401


def test_production_device_keys_require_https(client, app, owner, student):
    _, headers = pair_device(client, owner, student)
    app.state.settings.environment = 'production'
    assert client.post('/api/v1/auth/learning-device/register', headers=headers).status_code == 503
