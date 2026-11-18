"""Guardian OCR corrections preserve machine evidence and do not grade answers."""
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from wizzy.db import get_db, run
from wizzy.errors import DomainError, data
from wizzy.schemas import OcrCorrection, VerdictOverride
from wizzy.security import Principal, audit, current, guardian
from wizzy.submissions import scoped_record

router = APIRouter(prefix="/api/v1", tags=["question revisions"])


def scoped_question(db, actor, question_id):
    submission_id = run(db, "SELECT public.question_submission(:id)", id=question_id).scalar_one()
    if submission_id is None:
        raise DomainError(404, "not_found", "题目不存在")
    scoped_record(db, actor, "submissions", submission_id)
    # Re-materialize after taking the live consent lock.
    view = run(db, "SELECT public.read_question(:id)", id=question_id).scalar_one()
    if view is None:
        raise DomainError(404, "not_found", "题目不存在")
    return view


@router.get("/submissions/{submission_id}/questions")
def submission_questions(submission_id: UUID, request: Request, actor: Principal = Depends(current),
                         db: Session = Depends(get_db, scope="function")):
    scoped_record(db, actor, "submissions", submission_id)
    view = run(db, "SELECT public.read_submission_questions(:id)", id=submission_id).scalar_one()
    if view is None:
        raise DomainError(409, "result_not_ready", "当前作业尚无可展示的有效结果")
    return data(request, view)


@router.get("/questions/{question_id}")
def question(question_id: UUID, request: Request, actor: Principal = Depends(current),
             db: Session = Depends(get_db, scope="function")):
    return data(request, scoped_question(db, actor, question_id))


@router.post("/questions/{question_id}/ocr-corrections", status_code=201)
def correct_ocr(question_id: UUID, payload: OcrCorrection, request: Request,
                actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    guardian(actor)
    scoped_question(db, actor, question_id)
    run(db, "SELECT set_config('app.request_id',:id,true)", id=request.state.request_id)
    view = run(db, "SELECT public.correct_question_ocr(:id,:version,:stem,:answer,:reason)",
               id=question_id, version=payload.expected_revision, stem=payload.stem,
               answer=payload.student_answer_raw, reason=payload.reason).scalar_one()
    if view.get("error") == "revision_conflict":
        raise DomainError(409, "revision_conflict", "题目已更新，请刷新后重新核对")
    if view.get("error") == "invalid_correction":
        raise DomainError(422, "invalid_correction", "修正内容无效")
    if "error" in view:
        raise DomainError(404, "not_found", "题目不存在")
    audit(db, request, actor, "question.ocr_corrected", question_id)
    return data(request, view)


@router.post("/questions/{question_id}/verdict-overrides", status_code=201)
def override_verdict(question_id: UUID, payload: VerdictOverride, request: Request,
                     actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    guardian(actor)
    scoped_question(db, actor, question_id)
    run(db, "SELECT set_config('app.request_id',:id,true)", id=request.state.request_id)
    view = run(db, "SELECT public.override_question_verdict(:id,:version,:verdict_version,:verdict,:reason)",
               id=question_id, version=payload.expected_revision, verdict_version=payload.expected_verdict_revision,
               verdict=payload.verdict, reason=payload.reason).scalar_one()
    if view.get("error") == "revision_conflict":
        raise DomainError(409, "revision_conflict", "原文或判定已更新，请刷新后重新核对")
    if view.get("error") == "invalid_override":
        raise DomainError(422, "invalid_override", "判定内容无效")
    if "error" in view:
        raise DomainError(404, "not_found", "题目不存在")
    audit(db, request, actor, "question.verdict_overridden", question_id)
    return data(request, view)
