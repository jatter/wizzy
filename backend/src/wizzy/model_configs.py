"""Callable model configs. API keys are encrypted at rest and never returned to clients."""
import base64
import hashlib
import json
import time
import urllib.error
import urllib.request
from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from wizzy.db import get_db, one
from wizzy.errors import DomainError, data
from wizzy.security import digest, unauthorized

router = APIRouter(prefix='/api/v1/admin/model-configs', tags=['model-configs'])
PROBE_TIMEOUT_SECONDS = 10


class ModelConfigWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=8, max_length=500)
    api_key: str | None = Field(default=None, max_length=1000, repr=False)
    priority: StrictInt = Field(ge=1, le=9999)

    @field_validator('name', 'base_url', 'api_key', mode='before')
    @classmethod
    def clean(cls, value):
        if isinstance(value, str):
            if any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError('不能包含控制字符')
            return value.strip()
        return value

    @field_validator('base_url')
    @classmethod
    def http_url(cls, value):
        parts = urlsplit(value)
        if parts.scheme not in ('http', 'https') or not parts.hostname or any(c.isspace() for c in value):
            raise ValueError('调用地址需以 http:// 或 https:// 开头')
        return value


def cipher(settings):
    material = (settings.model_secret_key or settings.platform_mfa_key or settings.app_password
                or str(settings.database_url or ''))
    if not material:
        raise DomainError(503, 'secret_key_missing', '服务端未配置模型密钥加密密钥', retryable=False)
    key = base64.urlsafe_b64encode(hashlib.sha256(b'wizzy-model-configs:' + material.encode()).digest())
    return Fernet(key)


def key_fields(settings, api_key):
    hint = '****' + api_key[-4:] if len(api_key) >= 8 else '****'
    return {'api_key_cipher': cipher(settings).encrypt(api_key.encode()).decode(), 'api_key_hint': hint}


def bearer(request):
    scheme, _, token = request.headers.get('authorization', '').partition(' ')
    if scheme.lower() != 'bearer' or not token or len(token) > 128:
        raise unauthorized()
    return digest(token)


def call(db, request, token_hash, operation, payload=None, config_id=None):
    return one(db, '''SELECT public.model_config_request(:hash,:op,:request,CAST(:payload AS jsonb),:id) AS result''',
               hash=token_hash, op=operation, request=request.state.request_id,
               payload=json.dumps(payload or {}), id=config_id)['result']


def respond(request, result):
    status = result['status']
    if status == 401:
        raise unauthorized()
    if status == 403:
        # Returning (rather than raising) commits the database's access.denied audit.
        return JSONResponse(status_code=403, content={
            'error': {'code': 'forbidden', 'message': '缺少模型管理权限', 'field_errors': [], 'retryable': False},
            'request_id': request.state.request_id})
    if status not in (200, 201):
        messages = {404: ('not_found', '模型不存在'), 409: ('model_config_conflict', '相同模型名和调用地址已存在')}
        code, message = messages.get(status, ('validation_error', '模型配置不符合约定，请检查字段'))
        raise DomainError(status, code, message)
    return None


def projection(value):
    for key in ('created_at', 'updated_at', 'last_test_at'):
        if value.get(key):
            value[key] = datetime.fromisoformat(value[key])
    return value


def write_payload(request, payload: ModelConfigWrite):
    body = {'name': payload.name, 'base_url': payload.base_url, 'priority': payload.priority}
    if payload.api_key:
        body |= key_fields(request.app.state.settings, payload.api_key)
    return body


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def endpoint(base_url):
    url = base_url.rstrip('/')
    return url if url.endswith('/chat/completions') else url + '/chat/completions'


def provider_detail(raw: bytes, api_key: str | None):
    try:
        body = json.loads(raw.decode('utf-8', 'replace'))
    except ValueError:
        return ''
    detail = ''
    if isinstance(body, dict):
        error = body.get('error')
        if isinstance(error, dict):
            detail = error.get('message') or error.get('code') or ''
        elif isinstance(error, str):
            detail = error
        detail = detail or body.get('message') or body.get('detail') or ''
    if not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False)
    if api_key:
        detail = detail.replace(api_key, '****')
    return ''.join(c if c.isprintable() else ' ' for c in detail)[:160]


def probe(base_url: str, model: str, api_key: str | None):
    """One minimal OpenAI-compatible chat completion; redirects are refused to keep the key on-host."""
    body = json.dumps({'model': model, 'messages': [{'role': 'user', 'content': 'ping'}],
                       'max_tokens': 1, 'stream': False}).encode()
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json', 'User-Agent': 'wizzy-model-test/1'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    req = urllib.request.Request(endpoint(base_url), data=body, headers=headers, method='POST')
    opener = urllib.request.build_opener(NoRedirect)
    started = time.monotonic()
    try:
        with opener.open(req, timeout=PROBE_TIMEOUT_SECONDS) as response:
            response.read(65536)
            elapsed = round((time.monotonic() - started) * 1000)
            return True, f'连接成功：HTTP {response.status}，耗时 {elapsed} ms', elapsed
    except urllib.error.HTTPError as exc:
        elapsed = round((time.monotonic() - started) * 1000)
        detail = provider_detail(exc.read(65536), api_key)
        return False, f'HTTP {exc.code}' + (f'：{detail}' if detail else ''), elapsed
    except (urllib.error.URLError, OSError) as exc:
        elapsed = round((time.monotonic() - started) * 1000)
        reason = getattr(exc, 'reason', exc)
        if isinstance(reason, TimeoutError) or 'timed out' in str(reason).lower():
            return False, f'连接超时（{PROBE_TIMEOUT_SECONDS} 秒）', elapsed
        return False, f'无法连接：{type(reason).__name__}', elapsed


@router.get('')
def model_configs(request: Request, db: Session = Depends(get_db, scope='function')):
    result = call(db, request, bearer(request), 'list')
    if denied := respond(request, result):
        return denied
    return data(request, {'items': [projection(item) for item in result['value']]})


@router.post('', status_code=201)
def create_model_config(payload: ModelConfigWrite, request: Request,
                        db: Session = Depends(get_db, scope='function')):
    result = call(db, request, bearer(request), 'create', write_payload(request, payload))
    if denied := respond(request, result):
        return denied
    return data(request, projection(result['value']))


@router.post('/{config_id}')
def update_model_config(config_id: UUID, payload: ModelConfigWrite, request: Request,
                        db: Session = Depends(get_db, scope='function')):
    result = call(db, request, bearer(request), 'update', write_payload(request, payload), config_id)
    if denied := respond(request, result):
        return denied
    return data(request, projection(result['value']))


@router.post('/{config_id}/delete')
def delete_model_config(config_id: UUID, request: Request, db: Session = Depends(get_db, scope='function')):
    result = call(db, request, bearer(request), 'delete', config_id=config_id)
    if denied := respond(request, result):
        return denied
    return data(request, result['value'])


@router.post('/{config_id}/test')
def test_model_config(config_id: UUID, request: Request):
    # Separate short transactions: the outbound call must not hold session or account locks.
    token_hash, engine = bearer(request), request.app.state.engine
    with engine.begin() as connection:
        result = connection.execute(text('''SELECT public.model_config_request(:hash,'secret',:request,'{}'::jsonb,:id) AS result'''),
                                    {'hash': token_hash, 'request': request.state.request_id, 'id': config_id}).scalar_one()
    if denied := respond(request, result):
        return denied
    target = result['value']
    api_key = None
    if target['api_key_cipher']:
        try:
            api_key = cipher(request.app.state.settings).decrypt(target['api_key_cipher'].encode()).decode()
        except InvalidToken:
            ok, message, elapsed = False, '密钥无法解密，请编辑模型重新填写密钥', 0
    if not target['api_key_cipher'] or api_key is not None:
        ok, message, elapsed = probe(target['base_url'], target['name'], api_key)
    with engine.begin() as connection:
        result = connection.execute(text('''SELECT public.model_config_request(:hash,'record',:request,CAST(:payload AS jsonb),:id) AS result'''),
                                    {'hash': token_hash, 'request': request.state.request_id, 'id': config_id,
                                     'payload': json.dumps({'ok': ok, 'message': message})}).scalar_one()
    if denied := respond(request, result):
        return denied
    return data(request, {'ok': ok, 'message': message, 'elapsed_ms': elapsed, 'config': projection(result['value'])})
