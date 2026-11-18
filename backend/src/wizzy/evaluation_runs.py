"""Fixed-version offline prediction imports and server-computed quality reports."""
import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field, StrictInt, field_validator
from sqlalchemy.orm import Session

from wizzy.db import get_db, one
from wizzy.errors import DomainError, data
from wizzy.evaluation_datasets import Command
from wizzy.evaluation_statistics import (
    EvaluationGold,
    EvaluationPrediction,
    EvaluationStratum,
    build_evaluation_report,
)
from wizzy.security import digest, unauthorized

router = APIRouter(prefix='/api/v1/admin/evaluation-runs', tags=['evaluation-runs'])
Version = Annotated[StrictInt, Field(ge=1, le=999999999)]
VersionName = Annotated[str, Field(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,119}$')]
Hash = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]


class RunCreate(Command):
    dataset_id: UUID
    dataset_revision: Version
    model_id: UUID
    model_revision: Version
    prompt_hash: Hash
    schema_version: VersionName
    rule_version: VersionName
    image_pipeline_version: VersionName
    required_strata: list[EvaluationStratum] = Field(min_length=1, max_length=100)

    @field_validator('required_strata')
    @classmethod
    def unique_strata(cls, value):
        if len(set(value)) != len(value):
            raise ValueError('验收分层不能重复')
        return value


class RunPrediction(EvaluationPrediction):
    # The statistics kernel permits arbitrary stable IDs; persisted samples use UUIDs.
    @field_validator('question_id')
    @classmethod
    def sample_uuid(cls, value):
        return str(UUID(value))


class PredictionsImport(Command):
    manifest_digest: Hash
    source_reference: str = Field(min_length=1, max_length=200)
    predictions: list[RunPrediction] = Field(max_length=50000)

    @field_validator('source_reference')
    @classmethod
    def safe_reference(cls, value):
        if value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('离线来源标识不能含控制字符或首尾空格')
        return value


def perform(request, response, db, operation, *, run_id=None, payload=None, limit=50, offset=0):
    scheme, _, token = request.headers.get('authorization', '').partition(' ')
    if scheme.lower() != 'bearer' or not token or len(token) > 128:
        raise unauthorized()
    result = one(db, '''SELECT public.evaluation_run_request(:hash,:op,:request,
        CAST(:payload AS jsonb),:run,:limit,:offset) AS result''',
        hash=digest(token), op=operation, request=request.state.request_id,
        payload=json.dumps(payload.model_dump(mode='json') if payload else {}),
        run=run_id, limit=limit, offset=offset)['result']
    status = result['status']
    if status == 401:
        raise unauthorized()
    if status == 403:
        return JSONResponse(status_code=403, content={'error': {
            'code': 'forbidden', 'message': '缺少模型评测操作权限', 'field_errors': [], 'retryable': False},
            'request_id': request.state.request_id})
    if status not in (200, 201):
        messages = {404: ('not_found', '评测运行不存在'),
                    409: ('evaluation_conflict', '版本、完整题集或授权状态冲突，请刷新核对'),
                    422: ('validation_error', '评测运行数据不符合约定')}
        code, message = messages.get(status, ('evaluation_unavailable', '模型评测暂不可用'))
        raise DomainError(status, code, message)
    value = result['value']
    if operation == 'report':
        # The transaction keeps authorization locks until all statistics finish.
        report = build_evaluation_report(
            [EvaluationGold.model_validate(x) for x in value['golds']],
            [EvaluationPrediction.model_validate(x) for x in value['predictions']],
            required_strata=[EvaluationStratum.model_validate(x) for x in value['required_strata']])
        value = {'run_id': value['run_id'], 'manifest_digest': value['manifest_digest'], 'report': report}
    response.status_code = status
    return data(request, value)


@router.get('')
def runs(request: Request, response: Response, limit: int = Query(50, ge=1, le=100),
         offset: int = Query(0, ge=0, le=1000000), db: Session = Depends(get_db, scope='function')):
    return perform(request, response, db, 'list', limit=limit, offset=offset)


@router.post('', status_code=201)
def create(request: Request, response: Response, payload: RunCreate,
           db: Session = Depends(get_db, scope='function')):
    return perform(request, response, db, 'create', payload=payload)


@router.get('/{run_id}')
def detail(run_id: UUID, request: Request, response: Response,
           db: Session = Depends(get_db, scope='function')):
    return perform(request, response, db, 'detail', run_id=run_id)


@router.post('/{run_id}/predictions', status_code=201)
def predictions(run_id: UUID, payload: PredictionsImport, request: Request, response: Response,
                db: Session = Depends(get_db, scope='function')):
    return perform(request, response, db, 'import', run_id=run_id, payload=payload)


@router.get('/{run_id}/report')
def report(run_id: UUID, request: Request, response: Response,
           db: Session = Depends(get_db, scope='function')):
    return perform(request, response, db, 'report', run_id=run_id)
