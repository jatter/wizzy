"""Password rotation through the restricted API role and real PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_platform_identity import BASE, PASSWORD, login, session
from test_platform_identity import platform as platform  # noqa: F401

from wizzy.security import digest, passwords

NEW_PASSWORD = 'Changed-platform-password-only-for-tests'
PATH = BASE + '/auth/password'


def change(client, headers, current=PASSWORD, new=NEW_PASSWORD):
    return client.post(PATH, headers=headers, json={'current_password': current, 'new_password': new})


def test_change_revokes_all_own_sessions_and_rotates_credentials(client, platform, admin_engine):
    first, second = session(client, platform), session(client, platform)
    with admin_engine.connect() as db:
        before = db.execute(text('SELECT credential_version FROM platform_accounts WHERE id=:id'), platform).scalar_one()
    result = change(client, first)
    assert result.status_code == 200, result.text
    assert result.json()['data'] == {'changed': True, 'reauthentication_required': True}
    for headers in (first, second):
        assert client.get(BASE + '/auth/me', headers=headers).status_code == 401
        assert change(client, headers).status_code == 401
    assert login(client, platform).status_code == 401
    assert login(client, platform, password=NEW_PASSWORD).status_code == 200
    with admin_engine.connect() as db:
        row = db.execute(text('SELECT password_hash,credential_version FROM platform_accounts WHERE id=:id'), platform).one()
        assert passwords.verify(NEW_PASSWORD, row.password_hash)
        assert row.credential_version != before
        audits = db.execute(text("SELECT action,request_id FROM platform_audit_events WHERE actor_id=:id AND action LIKE 'password.%'"), platform).all()
        assert len(audits) == 1 and audits[0].action == 'password.changed'
        assert audits[0].request_id == result.json()['request_id']
    assert PASSWORD not in result.text and NEW_PASSWORD not in result.text


def test_wrong_current_password_is_audited_without_revoking_session(client, platform, admin_engine):
    headers = session(client, platform)
    result = change(client, headers, current='wrong-current-password')
    assert result.status_code == 403
    assert result.json()['error']['code'] == 'invalid_current_password'
    assert client.get(BASE + '/auth/me', headers=headers).status_code == 200
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE actor_id=:id AND action='password.denied' AND request_id=:request"),
                          dict(platform, request=result.json()['request_id'])).scalar_one() == 1
    assert change(client, headers).status_code == 200


@pytest.mark.parametrize('current,new', [(PASSWORD, PASSWORD), ('', NEW_PASSWORD), (PASSWORD, 'short'),
                                        (PASSWORD, 'x' * 129), ('x' * 129, NEW_PASSWORD)])
def test_password_validation_does_not_change_account(client, platform, current, new):
    headers = session(client, platform)
    response = change(client, headers, current=current, new=new)
    assert response.status_code == 422
    assert PASSWORD not in response.text and NEW_PASSWORD not in response.text
    assert client.get(BASE + '/auth/me', headers=headers).status_code == 200


def test_independent_identity_required(client, owner):
    for headers in ({}, owner['headers'], {'Authorization': 'Basic bad'}, {'Authorization': 'Bearer ' + 'x' * 129}):
        assert change(client, headers).status_code == 401


@pytest.mark.parametrize('state', ['expired', 'revoked', 'disabled', 'version'])
def test_live_identity_is_rechecked(client, platform, admin_engine, state):
    headers = session(client, platform)
    with admin_engine.begin() as db:
        if state == 'expired':
            db.execute(text("UPDATE platform_sessions SET expires_at=clock_timestamp()-interval '1 second' WHERE account_id=:id"), platform)
        elif state == 'revoked':
            db.execute(text('UPDATE platform_sessions SET revoked_at=clock_timestamp() WHERE account_id=:id'), platform)
        elif state == 'disabled':
            db.execute(text("UPDATE platform_accounts SET status='disabled' WHERE id=:id"), platform)
        else:
            db.execute(text('UPDATE platform_accounts SET credential_version=gen_random_uuid() WHERE id=:id'), platform)
    assert change(client, headers).status_code == 401


def test_operator_can_change_only_own_password(client, platform, admin_engine):
    with admin_engine.begin() as db:
        db.execute(text("UPDATE platform_accounts SET role='operator' WHERE id=:id"), platform)
    headers = session(client, platform)
    assert client.post(PATH, headers=headers, json={'current_password': PASSWORD, 'new_password': NEW_PASSWORD,
                                                  'account_id': str(platform['id'])}).status_code == 422
    assert change(client, headers).status_code == 200


def test_wrong_password_is_rate_limited(client, platform):
    headers = session(client, platform)
    for _ in range(10):
        assert change(client, headers, current='wrong').status_code == 403
    assert change(client, headers, current='wrong').status_code == 429
    assert client.get(BASE + '/auth/me', headers=headers).status_code == 200


def test_concurrent_verified_changes_have_one_winner(client, platform, app, admin_engine, monkeypatch):
    from wizzy import platform_identity

    headers = session(client, platform)
    barrier = Barrier(2)
    original = platform_identity.passwords.verify

    def synchronized_verify(password, hashed):
        valid = original(password, hashed)
        barrier.wait(timeout=10)
        return valid

    monkeypatch.setattr(platform_identity.passwords, 'verify', synchronized_verify)

    def submit(_):
        with TestClient(app) as browser:
            return change(browser, headers).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit, range(2))) == [200, 401]
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE actor_id=:id AND action='password.changed'"), platform).scalar_one() == 1


def test_credentials_revoked_after_verification_cannot_write(client, platform, admin_engine, monkeypatch):
    from wizzy import platform_identity

    headers = session(client, platform)
    original = platform_identity.passwords.verify

    def revoke_while_verifying(password, hashed):
        valid = original(password, hashed)
        with admin_engine.begin() as db:
            db.execute(text('UPDATE platform_sessions SET revoked_at=clock_timestamp() WHERE account_id=:id'), platform)
        return valid

    monkeypatch.setattr(platform_identity.passwords, 'verify', revoke_while_verifying)
    assert change(client, headers).status_code == 401
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE actor_id=:id AND action='password.changed'"), platform).scalar_one() == 0


def test_audit_failure_rolls_back_password_and_sessions(client, platform, admin_engine):
    headers = session(client, platform)
    token_hash = digest(headers['Authorization'].split()[1])
    with admin_engine.connect() as db:
        before = db.execute(text('SELECT password_hash,credential_version FROM platform_accounts WHERE id=:id'), platform).one()
    with pytest.raises(DBAPIError), admin_engine.begin() as db:
        db.execute(text('SELECT public.platform_change_password(:hash,:version,:password,NULL)'),
                   {'hash': token_hash, 'version': before.credential_version, 'password': passwords.hash(NEW_PASSWORD)})
    with admin_engine.connect() as db:
        after = db.execute(text('SELECT password_hash,credential_version FROM platform_accounts WHERE id=:id'), platform).one()
        assert after == before
    assert client.get(BASE + '/auth/me', headers=headers).status_code == 200
