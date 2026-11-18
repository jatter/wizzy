"""WebAuthn for learning access only; never grants guardian permissions."""
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from wizzy.db import get_db, many, one, run
from wizzy.errors import DomainError, data
from wizzy.learning_devices import active_learning_device, child_root, require_secure_login
from wizzy.security import (
    Principal,
    browser_request,
    current,
    digest,
    issue,
    limited,
    session_response,
    unauthorized,
)

router = APIRouter(prefix='/api/v1/auth/passkeys', tags=['learning-passkeys'])
CEREMONY_COOKIE = 'wizzy_passkey_ceremony'
CEREMONY_PATH = '/api/v1/auth/passkeys'


class Credential(BaseModel):
    credential: dict[str, Any]
    name: str = Field(default='我的学习设备', min_length=1, max_length=60)

    @field_validator('credential')
    @classmethod
    def bounded_credential(cls, value):
        if len(json.dumps(value)) > 32768:
            raise ValueError('Credential is too large')
        return value


def relying_party(request: Request):
    require_secure_login(request)
    browser_request(request)
    origin = (request.app.state.settings.family_public_origin or str(request.base_url)).rstrip('/')
    parsed = urlsplit(origin)
    if not parsed.hostname or parsed.path or parsed.query or parsed.fragment:
        raise DomainError(503, 'passkeys_unavailable', '免密登录的安全域名尚未配置')
    # WebView FOR_APP uses the APK signing-certificate origin, not the page origin.
    origins = [origin]
    if android_origin := request.app.state.settings.passkey_android_origin:
        origins.append(android_origin)
    return parsed.hostname, origins


def begin(request, db, purpose, options, session_id=None):
    token = secrets.token_urlsafe(32)
    run(db, 'DELETE FROM passkey_ceremonies WHERE expires_at<now()')
    run(db, '''INSERT INTO passkey_ceremonies(token_hash,challenge,purpose,session_id,expires_at)
        VALUES(:hash,:challenge,:purpose,:session,:expires)''', hash=digest(token), challenge=options.challenge,
        purpose=purpose, session=session_id, expires=datetime.now(UTC) + timedelta(minutes=5))
    response = JSONResponse(data(request, json.loads(options_to_json(options))))
    response.set_cookie(CEREMONY_COOKIE, token, httponly=True, secure=request.url.scheme == 'https',
                        samesite='strict', path=CEREMONY_PATH, max_age=300)
    return response


def ceremony(request, db, purpose):
    token = request.cookies.get(CEREMONY_COOKIE, '')
    if not token or len(token) > 128:
        raise unauthorized()
    row = one(db, '''SELECT * FROM passkey_ceremonies WHERE token_hash=:hash
        AND purpose=:purpose AND expires_at>now() FOR UPDATE''', hash=digest(token), purpose=purpose)
    if not row:
        raise unauthorized()
    return row


def finish(request, db, challenge, response):
    run(db, 'DELETE FROM passkey_ceremonies WHERE token_hash=:hash', hash=challenge['token_hash'])
    response.delete_cookie(CEREMONY_COOKIE, path=CEREMONY_PATH, httponly=True,
                           secure=request.url.scheme == 'https', samesite='strict')
    return response


@router.post('/registration/options')
def registration_options(request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope='function')):
    rp_id, _ = relying_party(request)
    root_id = child_root(db, actor)
    limited(request, 'passkey-register', str(root_id), maximum=20)
    root = active_learning_device(db, root_id)
    saved = many(db, 'SELECT credential_id FROM learning_passkeys WHERE session_id=:id AND revoked_at IS NULL', id=root_id)
    if len(saved) >= 10:
        raise DomainError(409, 'passkey_limit', '请先移除不再使用的免密凭证')
    student = one(db, 'SELECT nickname FROM students WHERE id=:id', id=root['student_id'])
    options = generate_registration_options(
        rp_id=rp_id, rp_name='小知学习空间', user_id=root_id.bytes,
        user_name=f'learning-{root_id}', user_display_name=student['nickname'] + '的学习空间',
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED, user_verification=UserVerificationRequirement.REQUIRED),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(row['credential_id'])) for row in saved],
    )
    return begin(request, db, 'register', options, root_id)


@router.post('/registration/verify', status_code=201)
def registration_verify(payload: Credential, request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope='function')):
    rp_id, origin = relying_party(request)
    root_id = child_root(db, actor)
    limited(request, 'passkey-register-verify', str(root_id), maximum=20)
    challenge = ceremony(request, db, 'register')
    if challenge['session_id'] != root_id:
        raise unauthorized()
    active_learning_device(db, root_id)
    try:
        verified = verify_registration_response(credential=payload.credential, expected_challenge=bytes(challenge['challenge']),
            expected_rp_id=rp_id, expected_origin=origin, require_user_verification=True)
    except (WebAuthnException, ValueError, TypeError, KeyError) as exc:
        raise unauthorized() from exc
    credential_id = bytes_to_base64url(verified.credential_id)
    if one(db, 'SELECT id FROM learning_passkeys WHERE credential_id=:id', id=credential_id):
        raise DomainError(409, 'passkey_exists', '该免密凭证已经绑定，请使用已有凭证')
    saved = one(db, '''INSERT INTO learning_passkeys(id,session_id,credential_id,public_key,sign_count,name)
        VALUES(:id,:session,:credential,:key,:count,:name) RETURNING id,name,created_at,last_used_at''',
        id=uuid4(), session=root_id, credential=credential_id, key=verified.credential_public_key,
        count=verified.sign_count, name=payload.name.strip() or '我的学习设备')
    return finish(request, db, challenge, JSONResponse(data(request, dict(saved)), status_code=201))


@router.post('/authentication/options')
def authentication_options(request: Request, db: Session = Depends(get_db, scope='function')):
    rp_id, _ = relying_party(request)
    limited(request, 'passkey-login-options', maximum=60)
    options = generate_authentication_options(rp_id=rp_id, user_verification=UserVerificationRequirement.REQUIRED)
    return begin(request, db, 'authenticate', options)


@router.post('/authentication/verify')
def authentication_verify(payload: Credential, request: Request, db: Session = Depends(get_db, scope='function')):
    rp_id, origin = relying_party(request)
    limited(request, 'passkey-login', maximum=30)
    challenge = ceremony(request, db, 'authenticate')
    credential_id = payload.credential.get('id')
    if not isinstance(credential_id, str) or len(credential_id) > 2048:
        raise unauthorized()
    saved = one(db, 'SELECT * FROM learning_passkeys WHERE credential_id=:id AND revoked_at IS NULL FOR UPDATE', id=credential_id)
    if not saved:
        raise unauthorized()
    root = active_learning_device(db, saved['session_id'])
    try:
        handle = payload.credential.get('response', {}).get('userHandle')
        if not handle or base64url_to_bytes(handle) != saved['session_id'].bytes:
            raise ValueError('Wrong learning-device handle')
        verified = verify_authentication_response(credential=payload.credential, expected_challenge=bytes(challenge['challenge']),
            expected_rp_id=rp_id, expected_origin=origin, credential_public_key=bytes(saved['public_key']),
            credential_current_sign_count=saved['sign_count'], require_user_verification=True)
    except (WebAuthnException, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise unauthorized() from exc
    run(db, 'UPDATE learning_passkeys SET sign_count=:count,last_used_at=now() WHERE id=:id', count=verified.new_sign_count, id=saved['id'])
    value = issue(db, request.app.state.settings, tenant_id=root['tenant_id'], student_id=root['student_id'],
                  device_name=root['device_name'], source_session_id=root['id'])
    return finish(request, db, challenge, session_response(request, value))


@router.get('')
def list_passkeys(request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope='function')):
    root_id = child_root(db, actor)
    return data(request, {'items': many(db, '''SELECT id,name,created_at,last_used_at FROM learning_passkeys
        WHERE session_id=:id AND revoked_at IS NULL ORDER BY created_at,id''', id=root_id)})


@router.delete('/{credential_id}')
def delete_passkey(credential_id: UUID, request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope='function')):
    root_id = child_root(db, actor)
    row = one(db, '''UPDATE learning_passkeys SET revoked_at=now() WHERE id=:id AND session_id=:session
        AND revoked_at IS NULL RETURNING id''', id=credential_id, session=root_id)
    if not row:
        raise DomainError(404, 'not_found', '免密凭证不存在')
    return data(request, {'revoked': True})
