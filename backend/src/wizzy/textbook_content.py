"""Human correction and independent publication of private textbook text."""
import json
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)
from sqlalchemy.orm import Session

from wizzy.db import get_db, one
from wizzy.errors import DomainError, data
from wizzy.pagination import decode_cursor, page
from wizzy.security import digest, unauthorized
from wizzy.textbook_processing import ProcessingRequest

router = APIRouter(prefix='/api/v1/admin/textbook-editions', tags=['textbook-content'])
PATH = '/{edition_id}/contents'
PageNumber = Annotated[StrictInt, Field(ge=1, le=500)]


class ContentPage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    number: PageNumber
    text: str = Field(max_length=100000)
    verified: StrictBool

    @field_validator('text')
    @classmethod
    def safe_text(cls, value):
        if '\0' in value or any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError('正文不能包含 NUL 或孤立代理字符')
        return value


class Chapter(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: str = Field(pattern=r'^[A-Z0-9][A-Z0-9_-]{0,39}$')
    title: str = Field(min_length=1, max_length=200)
    parent_code: str | None = Field(pattern=r'^[A-Z0-9][A-Z0-9_-]{0,39}$')
    first_page: PageNumber
    last_page: PageNumber

    @field_validator('title')
    @classmethod
    def clean_title(cls, value):
        return ProcessingRequest.clean_reason(value)


class ContentBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pages: list[ContentPage] = Field(min_length=1, max_length=500)
    chapters: list[Chapter] = Field(max_length=500)

    @model_validator(mode='after')
    def valid_mapping(self):
        if [p.number for p in self.pages] != list(range(1, len(self.pages) + 1)):
            raise ValueError('正文必须按原 PDF 物理页连续排列')
        if sum(len(p.text) for p in self.pages) > 1000000:
            raise ValueError('正文总长度不能超过 100 万字符')
        seen = {}
        for chapter in self.chapters:
            if chapter.code in seen or not 1 <= chapter.first_page <= chapter.last_page <= len(self.pages):
                raise ValueError('章节编号重复或源页范围无效')
            if chapter.parent_code is not None:
                parent = seen.get(chapter.parent_code)
                if parent is None or chapter.first_page < parent.first_page or chapter.last_page > parent.last_page:
                    raise ValueError('上级章节须先列出，子章节源页范围须在上级内')
            seen[chapter.code] = chapter
        return self


class ContentRevision(ProcessingRequest):
    body: ContentBody


class ContentApproval(ProcessingRequest):
    sample_pages: list[PageNumber] = Field(min_length=1, max_length=500)

    @field_validator('sample_pages')
    @classmethod
    def unique_pages(cls, value):
        if len(set(value)) != len(value):
            raise ValueError('抽检页码不能重复')
        return value


MESSAGES = {
    'not_found': '教材、校核内容或修订版本不存在', 'validation_error': '校核字段不符合约定',
    'operation_conflict': '操作键已用于其他请求', 'textbook_revision_conflict': '教材授权版本已变化，请刷新',
    'content_rights_unavailable': '来源授权已变化、失效或缺少存储、解析、预览用途',
    'content_source_unavailable': '仅完成内嵌文本提取的草稿可创建校核内容',
    'content_already_exists': '该处理任务已创建校核内容，请从列表打开',
    'content_revision_conflict': '内容版本已变化，请刷新后重新操作',
    'content_state_conflict': '当前内容状态不允许此操作',
    'content_review_incomplete': '请确认全部源页、填写章节并覆盖所有源页',
    'independent_reviewer_required': '审核人不能参与本内容的创建、修订或提交',
    'content_sample_incomplete': '抽检须覆盖首末页、空白提取页和所有修订页',
    'content_retrieval_unavailable': '发布需包含检索用途授权',
    'content_publication_exists': '本教材已有发布内容，请先撤回旧版本',
    'content_not_published': '正文未发布或已撤回',
}


def perform(request, db, operation, edition_id, *, target=None, payload=None, revision=None,
            number=None, before=None, limit=50, cursor=None):
    scheme, _, token = request.headers.get('authorization', '').partition(' ')
    if scheme.lower() != 'bearer' or not token or len(token) > 128:
        raise unauthorized()
    before_time, before_id = decode_cursor(cursor)
    result = one(db, '''SELECT public.textbook_content_request(:hash,:op,:request,CAST(:payload AS jsonb),
        :edition,:target,:revision,:number,:before,:limit,:before_time,:before_id) AS result''',
        hash=digest(token), op=operation, request=request.state.request_id,
        payload=json.dumps(payload.model_dump(mode='json') if payload else {}), edition=edition_id,
        target=target, revision=revision, number=number, before=before, limit=limit,
        before_time=before_time, before_id=before_id)['result']
    status = result['status']
    if status == 401:
        raise unauthorized()
    if status == 403:
        return JSONResponse(status_code=403, content={'error': {
            'code': 'forbidden', 'message': '缺少教材操作权限', 'field_errors': [], 'retryable': False},
            'request_id': request.state.request_id})
    if status not in (200, 201):
        code = result.get('code', {404: 'not_found', 422: 'validation_error'}.get(status, 'conflict'))
        raise DomainError(status, code, MESSAGES.get(code, '教材内容状态冲突'))
    value = result['value']
    if operation == 'list':
        for row in value:
            row['source_created_at'] = datetime.fromisoformat(row['source_created_at'])
        value = page(value, limit, timestamp_key='source_created_at')
    elif operation == 'history':
        value = {'items': value[:limit], 'next_before': value[limit-1]['revision'] if len(value) > limit else None}
    return JSONResponse(status_code=status, content=data(request, value))


@router.post('/{edition_id}/processing-jobs/{job_id}/content')
def create(edition_id: UUID, job_id: UUID, payload: ProcessingRequest, request: Request,
           db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'create', edition_id, target=job_id, payload=payload)


@router.get(PATH)
def listing(edition_id: UUID, request: Request, limit: int = Query(50, ge=1, le=100),
            cursor: str | None = None, db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'list', edition_id, limit=limit, cursor=cursor)


@router.get(PATH + '/{content_id}')
def detail(edition_id: UUID, content_id: UUID, request: Request, db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'detail', edition_id, target=content_id)


@router.get(PATH + '/{content_id}/history')
def history(edition_id: UUID, content_id: UUID, request: Request,
            before: int | None = Query(None, ge=1, le=999999999), limit: int = Query(50, ge=1, le=100),
            db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'history', edition_id, target=content_id, before=before, limit=limit)


@router.get(PATH + '/{content_id}/revisions/{revision}/body')
def body(edition_id: UUID, content_id: UUID, revision: int, request: Request,
         db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'body', edition_id, target=content_id, revision=revision)


@router.get(PATH + '/{content_id}/revisions/{revision}/pages/{number}')
def content_page(edition_id: UUID, content_id: UUID, revision: int, request: Request,
                 number: int = Path(ge=1, le=500), db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'page', edition_id, target=content_id, revision=revision, number=number)


@router.get(PATH + '/{content_id}/txt')
def published_txt(edition_id: UUID, content_id: UUID, request: Request,
                  db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'txt', edition_id, target=content_id)


@router.post(PATH + '/{content_id}/revisions')
def revise(edition_id: UUID, content_id: UUID, payload: ContentRevision, request: Request,
           db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'revise', edition_id, target=content_id, payload=payload)


@router.post(PATH + '/{content_id}/approve')
def approve(edition_id: UUID, content_id: UUID, payload: ContentApproval, request: Request,
            db: Session = Depends(get_db, scope='function')):
    return perform(request, db, 'approve', edition_id, target=content_id, payload=payload)


@router.post(PATH + '/{content_id}/{action}')
def transition(edition_id: UUID, content_id: UUID, action: str, payload: ProcessingRequest,
               request: Request, db: Session = Depends(get_db, scope='function')):
    if action not in ('submit', 'reject', 'withdraw'):
        raise DomainError(404, 'not_found', '操作不存在')
    return perform(request, db, action, edition_id, target=content_id, payload=payload)
