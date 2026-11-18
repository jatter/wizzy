"""Platform authentication uses the real restricted API role and disposable PostgreSQL."""
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from wizzy.security import digest, passwords

PASSWORD = 'Platform-test-only-strong-passphrase'
BASE = '/api/v1/admin'


def test_platform_requires_independent_authentication(client, owner):
    for headers in ({}, owner['headers']):
        response = client.get(BASE + '/auth/me', headers=headers)
        assert response.status_code == 401, response.text


@pytest.fixture
def platform(client, app, admin_engine):
    account_id = uuid4()
    email = f'platform-{account_id}@example.test'
    with admin_engine.begin() as db:
        db.execute(text('''INSERT INTO platform_accounts
            (id,email,password_hash,encrypted_totp,credential_version,role)
            VALUES(:id,:email,:password,'unused',:version,'security_admin')'''),
            {'id': account_id, 'email': email, 'password': passwords.hash(PASSWORD),
             'version': uuid4()})
    return {'id': account_id, 'email': email, 'app': app}


def challenge(client, app=None):
    app = app or client.app
    response = client.get(BASE + '/auth/captcha')
    assert response.status_code == 200, response.text
    body = response.json()['data']
    assert body['image'].startswith('data:image/png;base64,')
    stored = app.state.platform_captchas[body['captcha_id']]
    # PNG base64 can coincidentally contain the four-character code.
    assert set(body) == {'captcha_id', 'image'}
    assert body['captcha_id'] != stored.code
    return {'captcha_id': body['captcha_id'], 'captcha': stored.code}


def login(client, account, **overrides):
    payload = {'email': account['email'], 'password': PASSWORD}
    if 'captcha_id' not in overrides:
        payload.update(challenge(client, account.get('app') if isinstance(account, dict) else None))
    payload.update(overrides)
    return client.post(BASE + '/auth/login', json=payload)


def session(client, account):
    # Historical migration fixtures seed sessions before the current login protocol exists.
    if 'historical_headers' in account:
        return account['historical_headers']
    response = login(client, account)
    assert response.status_code == 200, response.text
    return {'Authorization': 'Bearer ' + response.json()['data']['access_token']}


def test_login_me_and_no_secret_leaks(client, platform, admin_engine):
    response = login(client, platform, email='  ' + platform['email'].upper() + '  ')
    assert response.status_code == 200, response.text
    value = response.json()['data']
    headers = {'Authorization': 'Bearer ' + value['access_token']}
    me = client.get(BASE + '/auth/me', headers=headers)
    assert me.status_code == 200
    assert me.json()['data']['role'] == 'security_admin'
    assert me.json()['data']['audience'] == 'platform'
    assert set(me.json()['data']['permissions']) == {
        'platform.accounts.read', 'platform.audit.read', 'platform.evaluation.read', 'platform.evaluation.run', 'platform.evaluation.curate', 'platform.evaluation.annotate', 'platform.evaluation.adjudicate', 'platform.models.read', 'platform.models.write',
        'platform.accounts.write', 'platform.accounts.approve', 'platform.accounts.sessions.revoke', 'platform.schools.read', 'platform.schools.write', 'platform.students.read', 'platform.students.write', 'platform.textbooks.read', 'platform.textbooks.write', 'platform.textbooks.review'}
    with admin_engine.connect() as db:
        stored = db.execute(text('SELECT token_hash,expires_at-created_at FROM platform_sessions WHERE account_id=:id'),
                            {'id': platform['id']}).one()
        assert stored[0] == digest(value['access_token'])
        assert stored[1].total_seconds() == 1800
    for content in (response.text, me.text):
        assert PASSWORD not in content
        assert 'password_hash' not in content and 'encrypted_totp' not in content


@pytest.mark.parametrize('changes', [
    {'password': 'wrong'}, {'captcha': '0000'}, {'email': 'unknown@example.test'},
])
def test_invalid_credentials_are_uniform(client, platform, changes):
    response = login(client, platform, **changes)
    assert response.status_code == 401
    assert response.json()['error']['code'] == 'authentication_required'


@pytest.mark.parametrize('captcha', ['', 'AB', 'ABCDEF', 'ab_1', 1234])
def test_captcha_input_is_four_alphanumeric(client, platform, captcha):
    assert login(client, platform, captcha=captcha).status_code == 422


def test_captcha_replay_rejected(client, platform):
    payload = challenge(client, platform['app'])
    assert login(client, platform, **payload).status_code == 200
    assert login(client, platform, **payload).status_code == 401
    assert login(client, platform).status_code == 200


def test_parallel_captcha_consumed_once(app, platform, admin_engine):
    payload = challenge(TestClient(app), app)
    def attempt(_):
        with TestClient(app, client=(f'parallel-{uuid4()}', 50000)) as other:
            return login(other, platform, **payload).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == [200, 401]
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM platform_sessions WHERE account_id=:id'),
                          {'id': platform['id']}).scalar_one() == 1


def test_expired_captcha_rejected(client, platform, monkeypatch):
    payload = challenge(client, platform['app'])
    later = time.time() + 121
    monkeypatch.setattr('wizzy.platform_identity.time.time', lambda: later)
    assert login(client, platform, **payload).status_code == 401


def test_login_does_not_require_mfa_key(client, platform, app):
    app.state.settings.platform_mfa_key = ''
    assert login(client, platform).status_code == 200


def test_failed_attempts_persist_and_throttle(client, platform):
    for _ in range(10):
        assert login(client, platform, password='wrong').status_code == 401
    assert login(client, platform).status_code == 429


@pytest.mark.parametrize('change', ['disabled', 'version', 'expired', 'revoked'])
def test_session_revalidated_each_request(client, platform, admin_engine, change):
    headers = session(client, platform)
    sql = {
        'disabled': "UPDATE platform_accounts SET status='disabled' WHERE id=:id",
        'version': 'UPDATE platform_accounts SET credential_version=:version WHERE id=:id',
        'expired': "UPDATE platform_sessions SET expires_at=now()-interval '1 second' WHERE account_id=:id",
        'revoked': 'UPDATE platform_sessions SET revoked_at=now() WHERE account_id=:id',
    }[change]
    with admin_engine.begin() as db:
        db.execute(text(sql), {'id': platform['id'], 'version': uuid4()})
    assert client.get(BASE + '/auth/me', headers=headers).status_code == 401


def test_role_and_permissions_are_live(client, platform, admin_engine):
    headers = session(client, platform)
    assert client.get(BASE + '/accounts', headers=headers).status_code == 200
    with admin_engine.begin() as db:
        db.execute(text("UPDATE platform_accounts SET role='operator' WHERE id=:id"), {'id': platform['id']})
    assert client.get(BASE + '/auth/me', headers=headers).json()['data']['permissions'] == ['platform.models.read', 'platform.schools.read', 'platform.students.read', 'platform.textbooks.read']
    assert client.get(BASE + '/accounts', headers=headers).status_code == 403
    assert client.get(BASE + '/audit-events', headers=headers).status_code == 403
    assert client.get(BASE + '/roles', headers=headers).status_code == 200


def test_platform_token_has_no_family_access(client, platform, student):
    headers = session(client, platform)
    assert client.get('/api/v1/auth/me', headers=headers).status_code == 401
    assert client.get('/api/v1/students', headers=headers).status_code == 401
    assert client.get(f"/api/v1/students/{student['id']}/terms", headers=headers).status_code == 401


@pytest.mark.parametrize('route', ['logout', 'logout-all'])
def test_logout_revokes_sessions(client, platform, admin_engine, route):
    headers = session(client, platform)
    second = secrets.token_urlsafe(32)
    with admin_engine.begin() as db:
        db.execute(text('''INSERT INTO platform_sessions(id,account_id,credential_version,token_hash)
            SELECT :sid,id,credential_version,:hash FROM platform_accounts WHERE id=:id'''),
            {'id': platform['id'], 'sid': uuid4(), 'hash': digest(second)})
    assert client.post(BASE + '/auth/' + route, headers=headers).status_code == 200
    assert client.get(BASE + '/auth/me', headers=headers).status_code == 401
    other = client.get(BASE + '/auth/me', headers={'Authorization': 'Bearer ' + second})
    assert other.status_code == (401 if route == 'logout-all' else 200)


def test_lists_paginate_and_audit_without_credentials(client, platform, admin_engine):
    headers = session(client, platform)
    accounts = client.get(BASE + '/accounts?limit=1', headers=headers)
    assert accounts.status_code == 200
    assert any(row['id'] == str(platform['id']) for row in
               client.get(BASE + '/accounts', headers=headers).json()['data']['items'])
    roles = client.get(BASE + '/roles', headers=headers).json()['data']['items']
    assert {r['name'] for r in roles} == {'security_admin', 'operator', 'evaluation_annotator', 'evaluation_adjudicator'}
    first = client.get(BASE + '/audit-events?limit=1', headers=headers).json()['data']
    assert first['next_cursor']
    second = client.get(BASE + '/audit-events', params={'limit': 1, 'cursor': first['next_cursor']}, headers=headers)
    assert second.status_code == 200
    assert first['items'][0]['id'] != second.json()['data']['items'][0]['id']
    assert client.get(BASE + '/accounts?cursor=invalid', headers=headers).status_code == 422
    assert client.get(BASE + '/accounts?limit=101', headers=headers).status_code == 422
    for body in (accounts.text, second.text):
        assert 'encrypted_totp' not in body and 'password_hash' not in body and PASSWORD not in body


def test_audit_failure_rolls_back_login_and_counter(client, platform, admin_engine, app):
    with admin_engine.begin() as db:
        db.execute(text('''CREATE FUNCTION public.fail_platform_audit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'injected platform audit failure'; END $$;
            CREATE TRIGGER fail_platform_audit BEFORE INSERT ON platform_audit_events
            FOR EACH ROW EXECUTE FUNCTION public.fail_platform_audit()'''))
    try:
        with TestClient(app, raise_server_exceptions=False) as failing_client:
            response = login(failing_client, platform)
        assert response.status_code == 500
        assert 'injected platform' not in response.text
        with admin_engine.connect() as db:
            assert db.execute(text('SELECT last_totp_step FROM platform_accounts WHERE id=:id'), {'id': platform['id']}).scalar_one() == -1
            assert db.execute(text('SELECT count(*) FROM platform_sessions WHERE account_id=:id'), {'id': platform['id']}).scalar_one() == 0
    finally:
        with admin_engine.begin() as db:
            db.execute(text('DROP TRIGGER fail_platform_audit ON platform_audit_events; DROP FUNCTION public.fail_platform_audit()'))
    assert login(client, platform).status_code == 200


@pytest.mark.parametrize('change', ['disabled', 'version'])
def test_platform_login_rejects_inactive_or_stale_version(platform, database, admin_engine, change):
    from sqlalchemy import create_engine

    runtime = create_engine(database['url'])
    try:
        with admin_engine.begin() as db:
            if change == 'disabled':
                db.execute(text("UPDATE platform_accounts SET status='disabled' WHERE id=:id"), {'id': platform['id']})
            else:
                db.execute(text('UPDATE platform_accounts SET credential_version=:version WHERE id=:id'),
                           {'id': platform['id'], 'version': uuid4()})
            version = db.execute(text('SELECT credential_version FROM platform_accounts WHERE id=:id'),
                                 {'id': platform['id']}).scalar_one()
            if change == 'version':
                version = uuid4()
        with runtime.begin() as db:
            result = db.execute(text('''SELECT public.platform_login(:account,:version,0,:hash,:id,'login-guard')'''),
                {'account': platform['id'], 'version': version,
                 'hash': digest(secrets.token_urlsafe(32)), 'id': uuid4()}).scalar_one()
            assert result is None
        with admin_engine.connect() as db:
            assert db.execute(text('SELECT count(*) FROM platform_sessions WHERE account_id=:id'),
                              {'id': platform['id']}).scalar_one() == 0
    finally:
        runtime.dispose()


def test_permission_revocation_is_live_and_denial_commits_audit(client, platform, admin_engine):
    headers = session(client, platform)
    with admin_engine.begin() as db:
        db.execute(text("DELETE FROM platform_role_permissions WHERE role='security_admin' AND permission='platform.accounts.read'"))
    try:
        response = client.get(BASE + '/accounts', headers=headers)
        assert response.status_code == 403
        me = client.get(BASE + '/auth/me', headers=headers).json()['data']
        assert me['role'] == 'security_admin'
        assert me['permissions'] == ['platform.accounts.approve', 'platform.accounts.sessions.revoke', 'platform.accounts.write',
                                     'platform.audit.read', 'platform.evaluation.adjudicate', 'platform.evaluation.annotate',
                                     'platform.evaluation.curate', 'platform.evaluation.read', 'platform.evaluation.run', 'platform.models.read', 'platform.models.write',
                                     'platform.schools.read', 'platform.schools.write', 'platform.students.read', 'platform.students.write', 'platform.textbooks.read', 'platform.textbooks.review', 'platform.textbooks.write']
        with admin_engine.connect() as db:
            assert db.execute(text('''SELECT action FROM platform_audit_events
                WHERE actor_id=:id AND request_id=:request'''),
                {'id': platform['id'], 'request': response.json()['request_id']}).scalar_one() == 'access.denied'
    finally:
        with admin_engine.begin() as db:
            db.execute(text("INSERT INTO platform_role_permissions VALUES ('security_admin','platform.accounts.read')"))
    assert client.get(BASE + '/accounts', headers=headers).status_code == 200


@pytest.mark.parametrize('operation', ['me', 'accounts', 'audit', 'logout', 'logout-all', 'denied'])
def test_request_audit_failure_fails_closed_and_rolls_back(client, platform, admin_engine, app, operation):
    headers = session(client, platform)
    method = 'post' if operation in ('logout', 'logout-all') else 'get'
    path = {'me': '/auth/me', 'audit': '/audit-events', 'denied': '/accounts',
            'logout': '/auth/logout', 'logout-all': '/auth/logout-all'}.get(operation, '/accounts')
    with admin_engine.begin() as db:
        if operation == 'denied':
            db.execute(text("UPDATE platform_accounts SET role='operator' WHERE id=:id"), {'id': platform['id']})
        before = db.execute(text('SELECT count(*) FROM platform_audit_events WHERE actor_id=:id'),
                            {'id': platform['id']}).scalar_one()
        db.execute(text('''CREATE FUNCTION public.fail_request_audit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'injected request audit failure'; END $$;
            CREATE TRIGGER fail_request_audit BEFORE INSERT ON platform_audit_events
            FOR EACH ROW EXECUTE FUNCTION public.fail_request_audit()'''))
    try:
        with TestClient(app, raise_server_exceptions=False) as failing:
            response = failing.request(method, BASE + path, headers=headers)
        assert response.status_code == 500
        assert 'injected request' not in response.text
        with admin_engine.connect() as db:
            assert db.execute(text('SELECT count(*) FROM platform_audit_events WHERE actor_id=:id'),
                              {'id': platform['id']}).scalar_one() == before
            assert db.execute(text('SELECT revoked_at FROM platform_sessions WHERE account_id=:id'),
                              {'id': platform['id']}).scalar_one() is None
    finally:
        with admin_engine.begin() as db:
            db.execute(text('DROP TRIGGER fail_request_audit ON platform_audit_events; DROP FUNCTION public.fail_request_audit()'))
    assert client.request(method, BASE + path, headers=headers).status_code == (403 if operation == 'denied' else 200)


@pytest.mark.parametrize('operation', ['login', 'me'])
@pytest.mark.parametrize('change', ['disabled', 'version'])
def test_waiting_request_rechecks_account_after_lock(app, client, platform, admin_engine, operation, change):
    from test_execution import wait_for_blocker

    headers = session(client, platform) if operation == 'me' else None
    def attempt():
        with TestClient(app, client=(f'lock-{uuid4()}', 50000)) as other:
            response = (login(other, platform) if operation == 'login'
                        else other.get(BASE + '/auth/me', headers=headers))
            return response.status_code
    with ThreadPoolExecutor(max_workers=1) as pool:
        with admin_engine.begin() as db:
            blocker = db.execute(text('SELECT pg_backend_pid()')).scalar_one()
            statement = ("UPDATE platform_accounts SET status='disabled' WHERE id=:id" if change == 'disabled'
                         else 'UPDATE platform_accounts SET credential_version=:version WHERE id=:id')
            db.execute(text(statement), {'id': platform['id'], 'version': uuid4()})
            future = pool.submit(attempt)
            wait_for_blocker(admin_engine, blocker)
            assert not future.done()
        assert future.result(timeout=5) == 401


def test_captcha_is_case_insensitive(client, platform):
    payload = challenge(client, platform['app'])
    payload['captcha'] = payload['captcha'].lower()
    assert login(client, platform, **payload).status_code == 200
