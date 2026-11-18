"""Revision-bound private processing requests and review-only page access."""
import json
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from sqlalchemy.orm import Session

from wizzy.db import get_db, one
from wizzy.errors import DomainError, data
from wizzy.pagination import decode_cursor, page
from wizzy.pdf_preview import PreviewUnavailable, render_page
from wizzy.security import digest, unauthorized
from wizzy.textbook_documents import perform as document_request

router = APIRouter(prefix='/api/v1/admin/textbook-editions', tags=['textbook-processing'])
PATH = '/{edition_id}/documents/{document_id}/processing-jobs'


class ProcessingRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: UUID
    expected_revision: Annotated[StrictInt, Field(ge=1, le=999999999)]
    reason: str = Field(min_length=1, max_length=500)

    @field_validator('reason')
    @classmethod
    def clean_reason(cls, value):
        if value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('不能包含首尾空白或控制字符')
        return value


def perform(request, db, operation, edition_id, document_id, *, payload=None,
            job_id=None, limit=50, cursor=None, page_number=None):
    scheme, _, token = request.headers.get('authorization', '').partition(' ')
    if scheme.lower() != 'bearer' or not token or len(token) > 128:
        raise unauthorized()
    before, before_id = decode_cursor(cursor)
    result = one(db, '''SELECT public.textbook_processing_request(:hash,:op,:request,CAST(:payload AS jsonb),
        :edition,:document,:job,:limit,:before,:before_id,:page) AS result''', hash=digest(token), op=operation,
        request=request.state.request_id, payload=json.dumps(payload.model_dump(mode='json') if payload else {}),
        edition=edition_id, document=document_id, job=job_id, limit=limit, before=before,
        before_id=before_id, page=page_number)['result']
    status = result['status']
    if status == 401:
        raise unauthorized()
    if status == 403:
        return status, None
    if status not in (200, 201):
        code = result.get('code', {404: 'not_found', 422: 'validation_error'}.get(status, 'conflict'))
        message = {
            'not_found': '教材、处理任务或草稿页面不存在', 'validation_error': '处理字段不符合约定',
            'operation_conflict': '操作键已用于其他处理请求', 'processing_active': '该文档已有待处理或运行中的任务',
            'document_not_received': '请先完成 PDF 上传',
            'document_rights_unavailable': '教材授权未批准、已失效或未包含存储和解析用途',
            'draft_rights_unavailable': '当前版本或授权不允许预览此草稿',
            'textbook_revision_conflict': '教材版本已变化，请刷新教材并重新上传',
        }.get(code, '处理状态冲突')
        raise DomainError(status, code, message)
    value = result['value']
    if operation == 'list':
        for row in value:
            row['created_at'] = datetime.fromisoformat(row['created_at'])
        value = page(value, limit)
    return status, value


def response(request, result):
    status, value = result
    if status == 403:
        return JSONResponse(status_code=403, content={'error': {
            'code': 'forbidden', 'message': '缺少教材操作权限', 'field_errors': [], 'retryable': False},
            'request_id': request.state.request_id})
    return JSONResponse(status_code=status, content=data(request, value))


@router.post(PATH)
def create(edition_id: UUID, document_id: UUID, payload: ProcessingRequest, request: Request,
           db: Session = Depends(get_db, scope='function')):
    return response(request, perform(request, db, 'create', edition_id, document_id, payload=payload))


@router.get(PATH)
def jobs(edition_id: UUID, document_id: UUID, request: Request,
         limit: int = Query(default=50, ge=1, le=100), cursor: str | None = None,
         db: Session = Depends(get_db, scope='function')):
    return response(request, perform(request, db, 'list', edition_id, document_id, limit=limit, cursor=cursor))


@router.get(PATH + '/{job_id}')
def detail(edition_id: UUID, document_id: UUID, job_id: UUID, request: Request,
           db: Session = Depends(get_db, scope='function')):
    return response(request, perform(request, db, 'detail', edition_id, document_id, job_id=job_id))


@router.get(PATH + '/{job_id}/pages/{page_number}')
def draft_page(edition_id: UUID, document_id: UUID, job_id: UUID, request: Request,
               page_number: int = Path(ge=1, le=500),
               db: Session = Depends(get_db, scope='function')):
    return response(request, perform(request, db, 'page', edition_id, document_id, job_id=job_id, page_number=page_number))


@router.get(PATH + '/{job_id}/pages/{page_number}/image')
def page_image(edition_id: UUID, document_id: UUID, job_id: UUID, request: Request,
               page_number: int = Path(ge=1, le=500)):
    def authorised(db):
        result = perform(request, db, 'page', edition_id, document_id,
                         job_id=job_id, page_number=page_number)
        if result[0] == 403:
            return result
        return document_request(request, db, 'detail', edition_id, document_id=document_id)

    # Release database locks while parsing native PDF content; authorise again
    # under the same revision/rights/session checks before returning any pixels.
    with Session(request.app.state.engine) as db, db.begin():
        initial = authorised(db)
    if initial[0] == 403:
        return response(request, initial)
    metadata = initial[1]
    root = request.app.state.settings.storage_root
    try:
        content = render_page(root / 'textbook-quarantine' / f'{document_id.hex}.pdf',
                              workspace=root / 'textbook-preview', size_bytes=metadata['size_bytes'],
                              sha256=metadata['sha256'], page_number=page_number)
    except PreviewUnavailable as exc:
        mismatch = exc.code == 'preview_source_mismatch'
        raise DomainError(409 if mismatch else 503, exc.code,
                          '原 PDF 与上传记录不一致，已停止预览' if mismatch else '原 PDF 预览暂不可用，请重试',
                          retryable=not mismatch) from None
    with Session(request.app.state.engine) as db, db.begin():
        checked = authorised(db)
        if checked[0] == 403:
            return response(request, checked)
        if any(checked[1][k] != metadata[k] for k in ('sha256', 'size_bytes', 'edition_revision', 'status')):
            raise DomainError(409, 'preview_source_mismatch', '原 PDF 记录已变化，请刷新')
        return Response(content=content, media_type='image/png', headers={
            'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
            'Content-Disposition': 'inline; filename="textbook-page.png"',
        })
