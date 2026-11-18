"""Real P-256 WebAuthn signatures, one-time ceremonies and child-only access."""
import base64
import hashlib
import json
from uuid import UUID, uuid4

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import ValidationError
from sqlalchemy import text
from test_learning_device_login import pair_device

BROWSER = {'X-Family-Session': 'browser', 'Origin': 'http://testserver'}


def encoded(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


class Authenticator:
    def __init__(self, session_id):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.id = bytes.fromhex(uuid4().hex + uuid4().hex)
        self.user_handle = UUID(session_id).bytes

    def registration(self, options, *, origin='http://testserver', verified=True):
        numbers = self.key.public_key().public_numbers()
        public = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32), -3: numbers.y.to_bytes(32)})
        auth = hashlib.sha256(b'testserver').digest() + bytes([0x45 if verified else 0x41]) + bytes(4) + bytes(16) + len(self.id).to_bytes(2) + self.id + public
        return {'id': encoded(self.id), 'rawId': encoded(self.id), 'type': 'public-key', 'response': {
            'clientDataJSON': encoded(json.dumps({'type': 'webauthn.create', 'challenge': options['challenge'], 'origin': origin}).encode()),
            'attestationObject': encoded(cbor2.dumps({'fmt': 'none', 'attStmt': {}, 'authData': auth})), 'transports': ['internal']}}

    def assertion(self, options, *, origin='http://testserver', verified=True, counter=1):
        client = json.dumps({'type': 'webauthn.get', 'challenge': options['challenge'], 'origin': origin}).encode()
        auth = hashlib.sha256(b'testserver').digest() + bytes([5 if verified else 1]) + counter.to_bytes(4)
        signature = self.key.sign(auth + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {'id': encoded(self.id), 'rawId': encoded(self.id), 'type': 'public-key', 'response': {
            'clientDataJSON': encoded(client), 'authenticatorData': encoded(auth), 'signature': encoded(signature), 'userHandle': encoded(self.user_handle)}}


@pytest.fixture
def registered(client, owner, student):
    paired, headers = pair_device(client, owner, student)
    auth = Authenticator(paired['session_id'])
    options = client.post('/api/v1/auth/passkeys/registration/options', headers={**BROWSER, **headers})
    assert options.status_code == 200, options.text
    response = client.post('/api/v1/auth/passkeys/registration/verify', headers={**BROWSER, **headers}, json={'credential': auth.registration(options.json()['data']), 'name': '学习平板'})
    assert response.status_code == 201, response.text
    return auth, paired, headers, response.json()['data']


def test_registered_passkey_logs_into_child_space_and_cannot_replay(client, registered):
    auth, _, _, _ = registered
    options = client.post('/api/v1/auth/passkeys/authentication/options', headers=BROWSER).json()['data']
    credential = auth.assertion(options)
    response = client.post('/api/v1/auth/passkeys/authentication/verify', headers=BROWSER, json={'credential': credential})
    assert response.status_code == 200, response.text
    assert response.json()['data']['token_type'] == 'cookie' and 'access_token' not in response.json()['data']
    assert client.get('/api/v1/auth/me', headers=BROWSER).json()['data']['audience'] == 'child'
    assert client.get('/api/v1/tenants', headers=BROWSER).status_code == 403
    assert client.post('/api/v1/auth/passkeys/authentication/verify', headers=BROWSER, json={'credential': credential}).status_code == 401


@pytest.mark.parametrize('failure', ['origin', 'challenge', 'signature', 'user-verification', 'user-handle'])
def test_passkey_rejects_forged_assertions(client, registered, failure):
    auth, _, _, _ = registered
    options = client.post('/api/v1/auth/passkeys/authentication/options', headers=BROWSER).json()['data']
    if failure == 'challenge':
        options['challenge'] = encoded(b'another-challenge')
    credential = auth.assertion(options, origin='https://attacker.test' if failure == 'origin' else 'http://testserver', verified=failure != 'user-verification')
    if failure == 'signature':
        credential['response']['signature'] = encoded(bytes(64))
    if failure == 'user-handle':
        credential['response']['userHandle'] = encoded(uuid4().bytes)
    response = client.post('/api/v1/auth/passkeys/authentication/verify', headers=BROWSER, json={'credential': credential})
    assert response.status_code == 401, response.text


def test_ceremony_is_bound_to_browser_cookie(client, registered):
    auth, _, _, _ = registered
    options = client.post('/api/v1/auth/passkeys/authentication/options', headers=BROWSER).json()['data']
    client.cookies.clear()
    assert client.post('/api/v1/auth/passkeys/authentication/verify', headers=BROWSER, json={'credential': auth.assertion(options)}).status_code == 401


def test_guardian_cannot_register_passkey_that_would_unlock_parent_centre(client, owner):
    assert client.post('/api/v1/auth/passkeys/registration/options', headers={**BROWSER, **owner['headers']}).status_code == 403


def test_logout_ends_session_but_explicit_passkey_can_log_in_again(client, registered):
    auth, _, headers, _ = registered
    assert client.post('/api/v1/auth/logout', headers=headers).status_code == 200
    assert client.get('/api/v1/auth/me', headers=headers).status_code == 401
    options = client.post('/api/v1/auth/passkeys/authentication/options', headers=BROWSER).json()['data']
    result = client.post('/api/v1/auth/passkeys/authentication/verify', headers=BROWSER, json={'credential': auth.assertion(options)})
    assert result.status_code == 200, result.text


def test_passkey_device_revocation_blocks_existing_and_new_sessions(client, owner, registered):
    auth, paired, _, _ = registered
    options = client.post('/api/v1/auth/passkeys/authentication/options', headers=BROWSER).json()['data']
    response = client.post('/api/v1/auth/passkeys/authentication/verify', headers=BROWSER, json={'credential': auth.assertion(options)})
    assert response.status_code == 200
    assert client.delete(f"/api/v1/child-sessions/{paired['session_id']}", headers=owner['headers']).status_code == 200
    assert client.get('/api/v1/auth/me', headers=BROWSER).status_code == 401
    options = client.post('/api/v1/auth/passkeys/authentication/options', headers=BROWSER).json()['data']
    assert client.post('/api/v1/auth/passkeys/authentication/verify', headers=BROWSER, json={'credential': auth.assertion(options, counter=2)}).status_code == 401


def test_passkeys_can_be_listed_and_removed_only_by_their_own_learning_device(client, owner, student, registered):
    _, _, headers, saved = registered
    items = client.get('/api/v1/auth/passkeys', headers=headers).json()['data']['items']
    assert any(item['id'] == saved['id'] and item['name'] == '学习平板' for item in items)
    assert all('public_key' not in item and 'credential_id' not in item for item in items)
    _, other = pair_device(client, owner, student)
    assert client.delete(f"/api/v1/auth/passkeys/{saved['id']}", headers=other).status_code == 404
    assert client.delete(f"/api/v1/auth/passkeys/{saved['id']}", headers=headers).status_code == 200


def test_expired_ceremony_and_revoked_consent_reject_login(client, owner, student, registered, admin_engine):
    auth, _, _, _ = registered
    options = client.post('/api/v1/auth/passkeys/authentication/options', headers=BROWSER).json()['data']
    with admin_engine.begin() as db:
        db.execute(text("UPDATE passkey_ceremonies SET expires_at=now()-interval '1 second'"))
    assert client.post('/api/v1/auth/passkeys/authentication/verify', headers=BROWSER, json={'credential': auth.assertion(options)}).status_code == 401
    client.post('/api/v1/consents', headers=owner['headers'], json={'student_id': student['id'], 'policy_version': '2026-10-25', 'child_rules_version': '2026-10-25', 'granted': False})
    options = client.post('/api/v1/auth/passkeys/authentication/options', headers=BROWSER).json()['data']
    assert client.post('/api/v1/auth/passkeys/authentication/verify', headers=BROWSER, json={'credential': auth.assertion(options)}).status_code == 401


def test_registration_rejects_missing_user_verification_and_wrong_origin(client, owner, student):
    paired, headers = pair_device(client, owner, student)
    auth = Authenticator(paired['session_id'])
    for overrides in ({'verified': False}, {'origin': 'https://attacker.test'}):
        options = client.post('/api/v1/auth/passkeys/registration/options', headers={**BROWSER, **headers}).json()['data']
        result = client.post('/api/v1/auth/passkeys/registration/verify', headers={**BROWSER, **headers}, json={'credential': auth.registration(options, **overrides)})
        assert result.status_code == 401, result.text


@pytest.mark.parametrize('client_origin,configured,accepted', [
    ('android:apk-key-hash:' + encoded(bytes(range(32))), True, True),
    ('android:apk-key-hash:' + encoded(bytes(range(32))), False, False),
    ('android:apk-key-hash:' + encoded(bytes(reversed(range(32)))), True, False),
    ('android:apk-key-hash:', True, False),
])
def test_android_passkey_requires_the_configured_release_signature(client, app, owner, student,
                                                                   client_origin, configured, accepted):
    if configured:
        app.state.settings.passkey_android_origin = 'android:apk-key-hash:' + encoded(bytes(range(32)))
    paired, headers = pair_device(client, owner, student)
    auth = Authenticator(paired['session_id'])
    options = client.post('/api/v1/auth/passkeys/registration/options', headers={**BROWSER, **headers}).json()['data']
    result = client.post('/api/v1/auth/passkeys/registration/verify', headers={**BROWSER, **headers},
                         json={'credential': auth.registration(options, origin=client_origin)})
    assert result.status_code == (201 if accepted else 401), result.text
    if not accepted:
        # Register a web credential so rejection also exercises a valid signed login.
        options = client.post('/api/v1/auth/passkeys/registration/options', headers={**BROWSER, **headers}).json()['data']
        assert client.post('/api/v1/auth/passkeys/registration/verify', headers={**BROWSER, **headers},
                           json={'credential': auth.registration(options)}).status_code == 201
    assert client.post('/api/v1/auth/logout', headers=headers).status_code == 200
    options = client.post('/api/v1/auth/passkeys/authentication/options', headers=BROWSER).json()['data']
    result = client.post('/api/v1/auth/passkeys/authentication/verify', headers=BROWSER,
                         json={'credential': auth.assertion(options, origin=client_origin)})
    assert result.status_code == (200 if accepted else 401), result.text
    if accepted:
        assert client.get('/api/v1/auth/me', headers=BROWSER).json()['data']['audience'] == 'child'
        assert client.get('/api/v1/tenants', headers=BROWSER).status_code == 403


@pytest.mark.parametrize('origin', ['*', 'https://attacker.test', 'android:apk-key-hash:',
                                  'android:apk-key-hash:' + 'A' * 43 + ',https://attacker.test'])
def test_android_passkey_configuration_rejects_non_certificate_origins(origin):
    from wizzy.config import Settings

    with pytest.raises(ValidationError):
        Settings(passkey_android_origin=origin)
