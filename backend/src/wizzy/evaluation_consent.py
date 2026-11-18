"""Optional evaluation consent is separate from permission to process homework."""
import json
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from sqlalchemy.orm import Session

from wizzy.db import get_db, one
from wizzy.errors import DomainError, data
from wizzy.security import digest, family_token, unauthorized

router = APIRouter(prefix='/api/v1', tags=['evaluation-consent'])
POLICY_VERSION = 'evaluation-2026-10-26-v1'
POLICY = {
    'version': POLICY_VERSION,
    'purpose': 'offline_grading_evaluation',
    'training_allowed': False,
    'public_sharing_allowed': False,
    'provider_transfer_allowed': False,
    'optional': True,
    'text': '自愿单独授权将该学生经脱敏且通过隐私检查的作业样本用于平台内部离线批改评测。'
            '本授权不包含模型训练、公开分享或向模型提供方传输；需要这些用途时必须另行征得同意。'
            '可随时撤回，拒绝或撤回评测授权不影响已同意的作业处理服务。'
            '撤回后停止后续使用；再次授权不能恢复旧授权。',
}


class EvaluationDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: UUID
    expected_revision: Annotated[StrictInt, Field(ge=0, le=999999999)]
    granted: StrictBool
    policy_version: Literal['evaluation-2026-10-26-v1']


def perform(request, response, db, student_id, action, payload=None):
    token = family_token(request)
    result = one(db, '''SELECT public.evaluation_consent_request(:hash,:student,:action,
        CAST(:payload AS jsonb),:request) AS result''', hash=digest(token), student=student_id,
        action=action, payload=json.dumps(payload.model_dump(mode='json') if payload else {}),
        request=request.state.request_id)['result']
    status = result['status']
    if status == 401:
        raise unauthorized()
    if status not in (200, 201):
        code, message = {
            403: ('forbidden', '需要监护人权限；授权评测前请先完成儿童信息处理同意'),
            404: ('not_found', '学生不存在'),
            409: ('conflict', '操作键或授权版本冲突，请刷新后重试'),
        }.get(status, ('validation_error', '评测授权内容不符合约定'))
        raise DomainError(status, code, message)
    response.status_code = status
    return data(request, result['value'])


@router.get('/evaluation-policy')
def policy(request: Request):
    return data(request, POLICY)


@router.get('/students/{student_id}/evaluation-consent')
def consent(student_id: UUID, request: Request, response: Response,
            db: Session = Depends(get_db, scope='function')):
    return perform(request, response, db, student_id, 'read')


@router.post('/students/{student_id}/evaluation-consent', status_code=201)
def decide(student_id: UUID, payload: EvaluationDecision, request: Request, response: Response,
           db: Session = Depends(get_db, scope='function')):
    return perform(request, response, db, student_id, 'decide', payload)
