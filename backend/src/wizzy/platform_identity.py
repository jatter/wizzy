"""Platform identities never set family context or reuse guardian sessions."""
import base64
import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from PIL import Image, ImageDraw, ImageFont
from pydantic import Field, model_validator
from sqlalchemy.orm import Session

from wizzy.db import get_db, one
from wizzy.errors import DomainError, data
from wizzy.pagination import decode_cursor, page
from wizzy.schemas import Input, Login
from wizzy.security import DUMMY_HASH, digest, limited, passwords, unauthorized

router = APIRouter(prefix='/api/v1/admin', tags=['platform'])
CAPTCHA_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
CAPTCHA_TTL_SECONDS = 120


class PlatformLogin(Login):
    captcha_id: UUID
    captcha: str = Field(pattern=r'^[A-Za-z0-9]{4}$')


class PlatformPasswordChange(Input):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)

    @model_validator(mode='after')
    def require_new_password(self):
        if self.current_password == self.new_password:
            raise ValueError('新密码必须与当前密码不同')
        return self


@dataclass
class CaptchaChallenge:
    code: str
    expires: float


def generate_captcha_code():
    return ''.join(secrets.choice(CAPTCHA_ALPHABET) for _ in range(4))


def render_captcha(code: str):
    image = Image.new('RGB', (140, 48), (247, 250, 248))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=28)
    for index, char in enumerate(code):
        draw.text((12 + index * 30, 6 + (index % 2) * 4), char, fill=(23, 123, 101), font=font)
    for _ in range(5):
        draw.line([(secrets.randbelow(140), secrets.randbelow(48)),
                   (secrets.randbelow(140), secrets.randbelow(48))], fill=(200, 214, 208), width=1)
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return 'data:image/png;base64,' + base64.standard_b64encode(buffer.getvalue()).decode()


def captcha_store(app):
    store = getattr(app.state, 'platform_captchas', None)
    if store is None:
        app.state.platform_captchas = store = {}
    now = time.time()
    for key in [key for key, item in store.items() if item.expires <= now]:
        store.pop(key, None)
    return store


def consume_captcha(app, captcha_id: UUID, answer: str):
    challenge = captcha_store(app).pop(str(captcha_id), None)
    if (challenge is None or challenge.expires <= time.time()
            or not secrets.compare_digest(challenge.code, answer.strip().upper())):
        raise unauthorized()


@router.get('/auth/captcha')
def captcha(request: Request):
    limited(request, 'platform-captcha-ip', maximum=60)
    code = generate_captcha_code()
    identifier = str(uuid4())
    captcha_store(request.app)[identifier] = CaptchaChallenge(code=code, expires=time.time() + CAPTCHA_TTL_SECONDS)
    return data(request, {'captcha_id': identifier, 'image': render_captcha(code)})


@router.post('/auth/login')
def login(payload: PlatformLogin, request: Request, db: Session = Depends(get_db, scope='function')):
    email = payload.email.strip().lower()
    limited(request, 'platform-login', email)
    limited(request, 'platform-login-ip', maximum=100)
    account = one(db, 'SELECT * FROM public.platform_credentials(:email)', email=email)
    valid = passwords.verify(payload.password, account['password_hash'] if account else DUMMY_HASH)
    consume_captcha(request.app, payload.captcha_id, payload.captcha)
    if not valid or not account:
        raise unauthorized()
    token, identifier = secrets.token_urlsafe(32), uuid4()
    saved = one(db, '''SELECT public.platform_login(:account,:version,:step,:hash,:id,:request) AS id''',
                account=account['id'], version=account['credential_version'], step=0,
                hash=digest(token), id=identifier, request=request.state.request_id)
    if saved['id'] is None:
        raise unauthorized()
    return data(request, {'access_token': token, 'token_type': 'bearer', 'session_id': identifier,
                          'audience': 'platform', 'expires_in': 1800})


def perform(request, db, operation, *, limit=50, cursor=None):
    authorization = request.headers.get('authorization', '')
    scheme, _, token = authorization.partition(' ')
    if scheme.lower() != 'bearer' or not token or len(token) > 128:
        raise unauthorized()
    before, before_id = decode_cursor(cursor)
    result = one(db, '''SELECT public.platform_request(:hash,:op,:request,:limit,:before,:before_id) AS result''',
                 hash=digest(token), op=operation, request=request.state.request_id,
                 limit=limit, before=before, before_id=before_id)['result']
    if result['status'] == 401:
        raise unauthorized()
    if result['status'] == 403:
        # Returning (rather than raising) commits the database's access.denied audit.
        return JSONResponse(status_code=403, content={
            'error': {'code': 'forbidden', 'message': '缺少平台操作权限', 'field_errors': [], 'retryable': False},
            'request_id': request.state.request_id})
    if result['status'] != 200:
        raise DomainError(422, 'validation_error', '请求参数不符合约定')
    value = result['value']
    if operation in ('accounts', 'audit'):
        for row in value:
            row['created_at'] = datetime.fromisoformat(row['created_at'])
        value = page(value, limit)
    return data(request, value)


@router.post('/auth/password')
def change_password(payload: PlatformPasswordChange, request: Request,
                    db: Session = Depends(get_db, scope='function')):
    scheme, _, token = request.headers.get('authorization', '').partition(' ')
    if scheme.lower() != 'bearer' or not token or len(token) > 128:
        raise unauthorized()
    token_hash = digest(token)
    account = one(db, 'SELECT * FROM public.platform_password_credentials(:hash)', hash=token_hash)
    if not account:
        raise unauthorized()
    limited(request, 'platform-password', str(account['id']))
    limited(request, 'platform-password-ip', maximum=50)
    valid = passwords.verify(payload.current_password, account['password_hash'])
    new_hash = passwords.hash(payload.new_password) if valid else None
    result = one(db, 'SELECT public.platform_change_password(:hash,:version,:password,:request) AS result',
                 hash=token_hash, version=account['credential_version'], password=new_hash,
                 request=request.state.request_id)['result']
    if result['status'] == 401:
        raise unauthorized()
    if result['status'] == 403:
        # Commit the denied audit without exposing credentials in the response.
        return JSONResponse(status_code=403, content={
            'error': {'code': 'invalid_current_password', 'message': '当前密码不正确',
                      'field_errors': [], 'retryable': False},
            'request_id': request.state.request_id})
    if result['status'] != 200:
        raise DomainError(422, 'validation_error', '请求参数不符合约定')
    return data(request, {'changed': True, 'reauthentication_required': True})


@router.get('/auth/me')
def me(request: Request, db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'me')


@router.post('/auth/logout')
def logout(request: Request, db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'logout')


@router.post('/auth/logout-all')
def logout_all(request: Request, db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'logout_all')


@router.get('/roles')
def roles(request: Request, db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'roles')


@router.get('/accounts')
def accounts(request: Request, limit: int = Query(default=50, ge=1, le=100),
             cursor: str | None = None, db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'accounts', limit=limit, cursor=cursor)


@router.get('/audit-events')
def audit_events(request: Request, limit: int = Query(default=50, ge=1, le=100),
                 cursor: str | None = None, db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'audit', limit=limit, cursor=cursor)
