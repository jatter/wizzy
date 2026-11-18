"""Internal, authorized and independently annotated evaluation corpora."""
import json
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
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
from wizzy.security import digest, unauthorized

router = APIRouter(prefix='/api/v1/admin/evaluation-datasets', tags=['evaluation-datasets'])
Version = Annotated[StrictInt, Field(ge=0, le=999999999)]
Coordinate = Annotated[StrictInt, Field(ge=0, le=10000)]


class Command(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: UUID
    expected_revision: Version


class DatasetCreate(Command):
    name: str = Field(min_length=1, max_length=120)
    split: Literal['train', 'validation', 'test']
    handbook_version: str = Field(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,119}$')

    @field_validator('name')
    @classmethod
    def name_valid(cls, value):
        if value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('名称不能含控制字符或首尾空格')
        return value


class SampleCreate(Command):
    question_id: UUID
    source_revision: Annotated[StrictInt, Field(ge=1, le=999999999)]
    authorization_id: UUID
    near_duplicate_group: str = Field(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,119}$')
    redacted_stem: str = Field(min_length=1, max_length=8000)
    redacted_answer: str = Field(max_length=8000)
    redaction_note: str = Field(min_length=1, max_length=1000)


class GoldLabel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    stem: str = Field(min_length=1, max_length=8000)
    student_answer: str = Field(max_length=8000)
    correct_answer: str = Field(max_length=8000)
    verdict: Literal['correct', 'incorrect', 'unanswered', 'pending_review', 'unsupported']
    question_type: str = Field(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,119}$')
    difficulty: Literal['easy', 'medium', 'hard']
    requires_abstention: StrictBool
    redaction_safe: StrictBool
    box: tuple[Coordinate, Coordinate, Coordinate, Coordinate]

    @model_validator(mode='after')
    def valid(self):
        if self.box[0] >= self.box[2] or self.box[1] >= self.box[3]:
            raise ValueError('题目坐标必须形成有效矩形')
        if self.verdict in ('pending_review', 'unsupported') and not self.requires_abstention:
            raise ValueError('不可判题目必须保留拒评标记')
        if self.verdict == 'unanswered' and self.student_answer.strip():
            raise ValueError('未作答判定不能包含学生答案')
        if self.verdict in ('correct', 'incorrect') and not self.correct_answer.strip():
            raise ValueError('可判题目必须填写正确答案')
        return self


class Annotation(Command):
    label: GoldLabel


class Adjudication(Annotation):
    reason: str = Field(min_length=1, max_length=1000)


def perform(request, response, db, operation, *, dataset_id=None, sample_id=None, payload=None,
            limit=50, offset=0):
    scheme, _, token = request.headers.get('authorization', '').partition(' ')
    if scheme.lower() != 'bearer' or not token or len(token) > 128:
        raise unauthorized()
    value = one(db, '''SELECT public.evaluation_dataset_request(:hash,:op,:request,
        CAST(:payload AS jsonb),:dataset,:sample,:limit,:offset) AS result''',
        hash=digest(token), op=operation, request=request.state.request_id,
        payload=json.dumps(payload.model_dump(mode='json') if payload else {}),
        dataset=dataset_id, sample=sample_id, limit=limit, offset=offset)['result']
    status = value['status']
    if status == 401:
        raise unauthorized()
    if status == 403:
        return JSONResponse(status_code=403, content={'error': {
            'code': 'forbidden', 'message': '缺少评测数据操作权限', 'field_errors': [], 'retryable': False},
            'request_id': request.state.request_id})
    if status not in (200, 201):
        messages = {404: ('not_found', '数据集或授权题目修订不存在'),
                    409: ('evaluation_conflict', '版本、独立标注、数据隔离或授权状态冲突，请刷新核对'),
                    422: ('validation_error', '评测数据不符合约定')}
        code, message = messages.get(status, ('evaluation_unavailable', '评测数据暂不可用'))
        raise DomainError(status, code, message)
    response.status_code = status
    return data(request, value['value'])


@router.get('')
def datasets(request: Request, response: Response, limit: int = Query(50, ge=1, le=100),
             offset: int = Query(0, ge=0, le=1000000), db: Session = Depends(get_db, scope='function')):
    return perform(request, response, db, 'list', limit=limit, offset=offset)


@router.post('', status_code=201)
def create(payload: DatasetCreate, request: Request, response: Response,
           db: Session = Depends(get_db, scope='function')):
    return perform(request, response, db, 'create', payload=payload)


@router.get('/{dataset_id}')
@router.get('/{dataset_id}/samples')
def dataset(dataset_id: UUID, request: Request, response: Response,
            limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=1000000),
            db: Session = Depends(get_db, scope='function')):
    return perform(request, response, db, 'detail', dataset_id=dataset_id, limit=limit, offset=offset)


@router.post('/{dataset_id}/samples', status_code=201)
def add_sample(dataset_id: UUID, payload: SampleCreate, request: Request, response: Response,
               db: Session = Depends(get_db, scope='function')):
    return perform(request, response, db, 'intake', dataset_id=dataset_id, payload=payload)


@router.post('/{dataset_id}/samples/{sample_id}/annotations', status_code=201)
def annotate(dataset_id: UUID, sample_id: UUID, payload: Annotation, request: Request, response: Response,
             db: Session = Depends(get_db, scope='function')):
    return perform(request, response, db, 'annotate', dataset_id=dataset_id, sample_id=sample_id, payload=payload)


@router.post('/{dataset_id}/samples/{sample_id}/adjudications', status_code=201)
def adjudicate(dataset_id: UUID, sample_id: UUID, payload: Adjudication, request: Request, response: Response,
               db: Session = Depends(get_db, scope='function')):
    return perform(request, response, db, 'adjudicate', dataset_id=dataset_id, sample_id=sample_id, payload=payload)


@router.post('/{dataset_id}/lock', status_code=201)
def lock(dataset_id: UUID, payload: Command, request: Request, response: Response,
         db: Session = Depends(get_db, scope='function')):
    return perform(request, response, db, 'lock', dataset_id=dataset_id, payload=payload)
