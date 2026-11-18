"""PDF quarantine intake. A receipt is not a clean scan or usable textbook content."""
import asyncio
import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from sqlalchemy.orm import Session

from wizzy.db import get_db, one
from wizzy.errors import DomainError, data
from wizzy.pagination import decode_cursor, page
from wizzy.security import digest, unauthorized

router = APIRouter(prefix='/api/v1/admin/textbook-editions', tags=['textbook-documents'])
MAX_BYTES = 50 * 1024 * 1024


class UploadIntent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: UUID
    expected_revision: Annotated[StrictInt, Field(ge=1, le=999999999)]
    filename: str = Field(min_length=5, max_length=160, pattern=r'(?i)\.pdf$')
    size_bytes: Annotated[StrictInt, Field(ge=1, le=MAX_BYTES)]
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    reason: str = Field(min_length=1, max_length=500)

    @field_validator('filename', 'reason')
    @classmethod
    def clean_text(cls, value, info):
        if value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('不能包含首尾空白或控制字符')
        if info.field_name == 'filename' and ('/' in value or '\\' in value):
            raise ValueError('文件名不能包含路径')
        return value


def perform(request, db, operation, edition_id, *, document_id=None, payload=None, limit=50, cursor=None):
    scheme, _, token = request.headers.get('authorization', '').partition(' ')
    if scheme.lower() != 'bearer' or not token or len(token) > 128:
        raise unauthorized()
    before, before_id = decode_cursor(cursor)
    result = one(db, '''SELECT public.textbook_document_request(:hash,:op,:request,CAST(:payload AS jsonb),
        :edition,:document,:limit,:before,:before_id) AS result''', hash=digest(token), op=operation,
        request=request.state.request_id, payload=json.dumps(payload.model_dump(mode='json') if payload else {}),
        edition=edition_id, document=document_id, limit=limit, before=before, before_id=before_id)['result']
    status = result['status']
    if status == 401:
        raise unauthorized()
    if status == 403:
        return status, None
    if status not in (200, 201):
        code = result.get('code', {404: 'not_found', 422: 'validation_error'}.get(status, 'conflict'))
        message = {'not_found': '教材或上传记录不存在', 'validation_error': '上传字段不符合约定',
                   'operation_conflict': '操作键已用于其他上传请求', 'upload_expired': '上传凭证已过期，请重新申请',
                   'document_rights_unavailable': '教材授权未批准、已失效或未包含存储用途',
                   'textbook_revision_conflict': '教材版本已变化，请刷新后重新申请上传'}.get(code, '上传状态冲突')
        raise DomainError(status, code, message)
    value = result['value']
    if operation == 'list':
        result_page = page([{**row, 'created_at': datetime.fromisoformat(row['created_at'])} for row in value], limit)
        result_page['items'] = value[:limit]
        value = result_page
    return status, value


def response(request, result):
    status, value = result
    if status == 403:
        return JSONResponse(status_code=403, content={'error': {
            'code': 'forbidden', 'message': '缺少教材权限或不是此文件的上传人',
            'field_errors': [], 'retryable': False}, 'request_id': request.state.request_id})
    return JSONResponse(status_code=status, content=data(request, value))


@router.get('/{edition_id}/documents')
def documents(edition_id: UUID, request: Request, limit: int = Query(default=50, ge=1, le=100),
              cursor: str | None = None, db: Session = Depends(get_db, scope='function')):
    return response(request, perform(request, db, 'list', edition_id, limit=limit, cursor=cursor))


@router.post('/{edition_id}/documents')
def intent(edition_id: UUID, payload: UploadIntent, request: Request,
           db: Session = Depends(get_db, scope='function')):
    return response(request, perform(request, db, 'intent', edition_id, payload=payload))


@router.get('/{edition_id}/documents/{document_id}')
def document(edition_id: UUID, document_id: UUID, request: Request,
             db: Session = Depends(get_db, scope='function')):
    return response(request, perform(request, db, 'detail', edition_id, document_id=document_id))


def verify_existing(path: Path, expected):
    checksum = hashlib.sha256()
    count = 0
    with path.open('rb') as source:
        while chunk := source.read(1024 * 1024):
            checksum.update(chunk)
            count += len(chunk)
            if count > MAX_BYTES:
                break
    if count != expected['size_bytes'] or checksum.hexdigest() != expected['sha256']:
        raise DomainError(503, 'storage_unavailable', '隔离存储校验失败', retryable=True)


@router.put('/{edition_id}/documents/{document_id}/content')
async def upload(edition_id: UUID, document_id: UUID, request: Request):
    # Release authorization locks while receiving bytes; recheck under lock before receipt.
    with Session(request.app.state.engine) as db, db.begin():
        initial = perform(request, db, 'upload', edition_id, document_id=document_id)
    if initial[0] == 403:
        return response(request, initial)
    expected = initial[1]
    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/pdf':
        raise DomainError(422, 'content_type_mismatch', '请使用 application/pdf 上传')
    declared = request.headers.get('content-length')
    if declared is not None and (len(declared) > 8 or not declared.isascii() or not declared.isdecimal()
                                 or int(declared) != expected['size_bytes']):
        raise DomainError(422, 'size_mismatch', '实际大小与上传声明不一致')
    root = request.app.state.settings.storage_root / 'textbook-quarantine'
    temporary = None
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        checksum, count, prefix = hashlib.sha256(), 0, b''
        with tempfile.NamedTemporaryFile(dir=root, prefix='incoming-', delete=False) as output:
            temporary = Path(output.name)
            async with asyncio.timeout(120):
                async for chunk in request.stream():
                    count += len(chunk)
                    if count > MAX_BYTES:
                        raise DomainError(413, 'pdf_too_large', '单份 PDF 不得超过 50 MiB')
                    if count > expected['size_bytes']:
                        raise DomainError(422, 'size_mismatch', '实际大小与上传声明不一致')
                    prefix = (prefix + chunk[:5])[:5]
                    checksum.update(chunk)
                    output.write(chunk)
            if count != expected['size_bytes']:
                raise DomainError(422, 'size_mismatch', '实际大小与上传声明不一致')
            if checksum.hexdigest() != expected['sha256']:
                raise DomainError(422, 'checksum_mismatch', 'PDF 摘要与上传声明不一致')
            if prefix != b'%PDF-':
                raise DomainError(422, 'invalid_pdf_signature', '文件不包含 PDF 签名')
            output.flush()
            os.fsync(output.fileno())
        with Session(request.app.state.engine) as db, db.begin():
            checked = perform(request, db, 'upload', edition_id, document_id=document_id)
            if checked[0] == 403:
                return response(request, checked)
            # Never replace a confirmed object. A matching orphan from a failed commit is reusable.
            target = root / f'{document_id.hex}.pdf'
            try:
                os.link(temporary, target)
            except FileExistsError:
                verify_existing(target, expected)
            directory = os.open(root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            completed = perform(request, db, 'complete', edition_id, document_id=document_id)
        return response(request, completed)
    except TimeoutError as exc:
        raise DomainError(408, 'upload_timeout', '上传超时，可使用原凭证重新上传', retryable=True) from exc
    except OSError as exc:
        raise DomainError(503, 'storage_unavailable', '隔离存储暂不可用，可重试', retryable=True) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
