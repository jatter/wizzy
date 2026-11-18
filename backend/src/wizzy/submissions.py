"""Student-scoped submission receipts; all writes share one PostgreSQL transaction."""
import hashlib
import json
from datetime import date, datetime, time
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.orm import Session

from wizzy.db import get_db, many, one, run
from wizzy.errors import DomainError, data
from wizzy.homework_results import page_answers
from wizzy.media import consent_student, ready_asset
from wizzy.pagination import decode_cursor, page
from wizzy.schemas import SubmissionCreate
from wizzy.security import Principal, audit, current, guardian, limited

router = APIRouter(prefix="/api/v1", tags=["submissions"])
SHANGHAI = ZoneInfo("Asia/Shanghai")


def account(db, tenant_id, page_limit):
    period = datetime.now(SHANGHAI).date().replace(day=1)
    run(db, """INSERT INTO quota_accounts(tenant_id,period_start,page_limit)
        VALUES(:tenant,:period,:limit) ON CONFLICT(tenant_id,period_start) DO NOTHING""",
        tenant=tenant_id, period=period, limit=page_limit)
    return one(db, "SELECT * FROM quota_accounts WHERE tenant_id=:tenant AND period_start=:period",
               tenant=tenant_id, period=period)


def receipt(row):
    # Original receipt is stable even if the asynchronous state has subsequently changed.
    return {"submission_id": row["id"], "job_id": row["job_id"], "status": "queued", "source_revision": row["source_revision"]}


@router.get("/quota")
def quota(request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    guardian(actor)
    row = dict(account(db, actor.tenant_id, request.app.state.settings.monthly_page_limit))
    row.pop("tenant_id")
    row.update(timezone="Asia/Shanghai", remaining_pages=row["page_limit"] - row["reserved_pages"] - row["used_pages"])
    return data(request, row)


@router.post("/submissions", status_code=202)
def create_submission(body: SubmissionCreate, request: Request, idempotency_key: UUID = Header(),
                      actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    student = consent_student(db, actor, body.student_id)
    hash_body = body.model_dump(mode="json")
    # Default options preserve the request hashes of clients predating dictation support.
    for key, default in (('activity_type', 'auto'), ('reference_text', '')):
        if hash_body[key] == default:
            hash_body.pop(key)
    body_hash = hashlib.sha256(json.dumps(hash_body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    run(db, "SELECT pg_advisory_xact_lock(hashtextextended(:key,0))", key=f"submission:{actor.tenant_id}:{idempotency_key}")
    existing = one(db, """SELECT s.*,j.id AS job_id FROM submissions s JOIN jobs j ON j.submission_id=s.id
        AND j.tenant_id=s.tenant_id WHERE s.idempotency_key=:key""", key=idempotency_key)
    if existing:
        existing_student = consent_student(db, actor, existing["student_id"])
        if existing["deletion_epoch"] != existing_student["deletion_epoch"]:
            raise DomainError(404, "not_found", "提交不存在")
        if existing["body_hash"] != body_hash:
            raise DomainError(409, "idempotency_conflict", "相同幂等键不能用于不同请求")
        return data(request, receipt(existing))
    for media_id in body.media_ids:
        media = ready_asset(db, actor, media_id)
        if media["student_id"] != body.student_id:
            raise DomainError(404, "not_found", "图片不存在")
    quota = account(db, actor.tenant_id, request.app.state.settings.monthly_page_limit)
    reserved = one(db, """UPDATE quota_accounts SET reserved_pages=reserved_pages+:pages
        WHERE tenant_id=:tenant AND period_start=:period
        AND reserved_pages+used_pages+:pages<=page_limit RETURNING tenant_id""",
        tenant=actor.tenant_id, period=quota["period_start"], pages=len(body.media_ids))
    if not reserved:
        raise DomainError(409, "quota_exceeded", "本月剩余页数不足")
    submission_id, job_id = uuid4(), uuid4()
    run(db, """INSERT INTO submissions(id,tenant_id,student_id,deletion_epoch,subject,activity_type,reference_text,idempotency_key,body_hash,client_request_id)
        VALUES(:id,:tenant,:student,:epoch,:subject,:activity,:reference,:key,:hash,:client)""", id=submission_id,
        tenant=actor.tenant_id, student=body.student_id, epoch=student["deletion_epoch"], subject=body.subject,
        activity=body.activity_type, reference=body.reference_text,
        key=idempotency_key, hash=body_hash, client=body.client_request_id)
    for position, media_id in enumerate(body.media_ids, 1):
        run(db, """INSERT INTO submission_pages(tenant_id,student_id,submission_id,position,media_id)
            VALUES(:tenant,:student,:submission,:position,:media)""", tenant=actor.tenant_id,
            student=body.student_id, submission=submission_id, position=position, media=media_id)
    run(db, """INSERT INTO jobs(id,tenant_id,student_id,submission_id,deletion_epoch)
        VALUES(:id,:tenant,:student,:submission,:epoch)""", id=job_id, tenant=actor.tenant_id,
        student=body.student_id, submission=submission_id, epoch=student["deletion_epoch"])
    run(db, """INSERT INTO quota_reservations(id,tenant_id,student_id,submission_id,period_start,pages)
        VALUES(:id,:tenant,:student,:submission,:period,:pages)""", id=uuid4(), tenant=actor.tenant_id,
        student=body.student_id, submission=submission_id, period=quota["period_start"], pages=len(body.media_ids))
    run(db, """INSERT INTO outbox_events(id,tenant_id,student_id,aggregate_id,event_type,payload,request_id)
        VALUES(:id,:tenant,:student,:job,'submission.queued',CAST(:payload AS jsonb),:request)""",
        id=uuid4(), tenant=actor.tenant_id, student=body.student_id, job=job_id,
        payload=json.dumps({"submission_id": str(submission_id), "source_revision": 1, "deletion_epoch": student["deletion_epoch"]}),
        request=request.state.request_id)
    audit(db, request, actor, "submission.created", submission_id)
    return data(request, receipt({"id": submission_id, "job_id": job_id, "source_revision": 1}))


def scoped_record(db, actor, table, identifier):
    # Table is an internal constant, never a request parameter.
    row = one(db, f"SELECT * FROM {table} WHERE id=:id", id=identifier)
    if not row:
        raise DomainError(404, "not_found", "记录不存在")
    student = consent_student(db, actor, row["student_id"])
    if row["deletion_epoch"] != student["deletion_epoch"]:
        raise DomainError(404, "not_found", "记录不存在")
    return row


@router.get("/submissions")
def submissions(request: Request, limit: int = Query(default=20, ge=1, le=100), cursor: str | None = None, student_id: UUID | None = None,
                date_from: date | None = None, date_to: date | None = None, subject: Literal["math", "english", "chinese", "auto"] | None = None,
                status: Literal["queued", "processing", "completed", "partial", "failed", "cancelled"] | None = None,
                actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    if student_id is not None:
        consent_student(db, actor, student_id)
    if date_from and date_to and date_from > date_to:
        raise DomainError(422, "validation_error", "开始日期不能晚于结束日期")
    after, identifier = decode_cursor(cursor)
    start = datetime.combine(date_from, time.min, SHANGHAI) if date_from else None
    end = datetime.combine(date_to, time.max, SHANGHAI) if date_to else None
    rows = many(db, """SELECT s.id,s.student_id,s.subject,s.status,s.source_revision,s.created_at,
        (SELECT count(*) FROM submission_pages p WHERE p.submission_id=s.id) AS page_count
        FROM submissions s JOIN students st ON st.id=s.student_id AND st.tenant_id=s.tenant_id
        WHERE st.status='active' AND st.consent_active AND st.deletion_epoch=s.deletion_epoch
        AND (CAST(:student AS uuid) IS NULL OR s.student_id=:student)
        AND (:owner OR s.student_id=:child OR EXISTS(SELECT 1 FROM student_access a
            WHERE a.student_id=s.student_id AND a.user_id=:user AND a.tenant_id=s.tenant_id))
        AND (CAST(:start AS timestamptz) IS NULL OR s.created_at>=:start)
        AND (CAST(:end AS timestamptz) IS NULL OR s.created_at<=:end)
        AND (CAST(:subject AS text) IS NULL OR s.subject=:subject)
        AND (CAST(:status AS text) IS NULL OR s.status=:status)
        AND (CAST(:date AS timestamptz) IS NULL OR (s.created_at,s.id)<(:date,:id))
        ORDER BY s.created_at DESC,s.id DESC LIMIT :limit""", owner=actor.role == "owner", child=actor.student_id,
        user=actor.user_id, student=student_id, start=start, end=end, subject=subject, status=status,
        date=after, id=identifier, limit=limit + 1)
    return data(request, page(rows, limit))


@router.get("/students/{student_id}/submission-days")
def submission_days(student_id: UUID, request: Request, actor: Principal = Depends(current),
                    db: Session = Depends(get_db, scope="function")):
    student = consent_student(db, actor, student_id)
    rows = many(db, """SELECT (created_at AT TIME ZONE 'Asia/Shanghai')::date AS date,count(*) AS count
        FROM submissions WHERE student_id=:student AND deletion_epoch=:epoch
        GROUP BY 1 ORDER BY 1 DESC""", student=student_id, epoch=student['deletion_epoch'])
    return data(request, {"items": rows})


@router.get("/submissions/{submission_id}")
def submission(submission_id: UUID, request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    source = scoped_record(db, actor, "submissions", submission_id)
    row = {key: source[key] for key in ("id", "student_id", "subject", "activity_type", "reference_text", "status", "source_revision", "created_at")}
    row["pages"] = many(db, "SELECT position,media_id FROM submission_pages WHERE submission_id=:id ORDER BY position", id=submission_id)
    row["job_id"] = one(db, "SELECT id FROM jobs WHERE submission_id=:id", id=submission_id)["id"]
    return data(request, row)


@router.get("/submissions/{submission_id}/result")
def submission_result(submission_id: UUID, request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    # Keep the live student/consent lock until the projection is materialized.
    scoped_record(db, actor, "submissions", submission_id)
    result = run(db, "SELECT public.read_grading_result(:id)", id=submission_id).scalar_one()
    if result is None:
        raise DomainError(409, "result_not_ready", "当前作业尚无可展示的有效结果")
    return data(request, result)


@router.get("/submissions/{submission_id}/model-outputs")
def model_outputs(submission_id: UUID, request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    scoped_record(db, actor, "submissions", submission_id)
    count = run(db, "SELECT count(*) FROM submission_pages WHERE submission_id=:id", id=submission_id).scalar_one()
    rows = many(db, """SELECT id,sequence,prompt_id,prompt_name,prompt_content,prompt_priority,
        model_id,model_name,model_priority,status,content,error_message,created_at,finished_at
        FROM homework_model_outputs WHERE submission_id=:id ORDER BY sequence""", id=submission_id)
    return data(request, {"items": [dict(row, page_results=page_answers(row['content'], count)) for row in rows]})


@router.get("/jobs/{job_id}")
def job(job_id: UUID, request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    source = scoped_record(db, actor, "jobs", job_id)
    return data(request, {key: source[key] for key in ("id", "submission_id", "status", "error_code", "created_at", "finished_at")})


def release_reservation(db, submission_id):
    reservation = one(db, """UPDATE quota_reservations SET status='released',settled_at=now()
        WHERE submission_id=:id AND status='reserved' RETURNING *""", id=submission_id)
    if reservation:
        run(db, """UPDATE quota_accounts SET reserved_pages=reserved_pages-:pages
            WHERE tenant_id=:tenant AND period_start=:period""", pages=reservation["pages"],
            tenant=reservation["tenant_id"], period=reservation["period_start"])


def cancel_student_jobs(db, student_id):
    # Caller holds the student's exclusive lock: submissions/cancellation lock student first.
    jobs = many(db, """SELECT id,submission_id FROM jobs WHERE student_id=:student
        AND status IN ('queued','running','retry_wait') ORDER BY id FOR UPDATE""", student=student_id)
    for job in jobs:
        run(db, "UPDATE jobs SET status='cancelled',finished_at=now() WHERE id=:id", id=job["id"])
        run(db, "UPDATE submissions SET status='cancelled' WHERE id=:id", id=job["submission_id"])
        release_reservation(db, job["submission_id"])


@router.post("/submissions/{submission_id}/regrade", status_code=202)
def regrade(submission_id: UUID, request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    scoped_record(db, actor, "submissions", submission_id)
    limited(request, "regrade", str(submission_id), maximum=10)
    result = run(db, "SELECT public.request_homework_regrade(:id)", id=submission_id).scalar_one()
    if result["status"] == 404:
        raise DomainError(404, "not_found", "记录不存在")
    if result["status"] == 409 and result.get("code") == "quota_exceeded":
        raise DomainError(409, "quota_exceeded", "本月剩余页数不足，暂时不能重新批改")
    if result["status"] != 202:
        raise DomainError(409, "job_state_conflict", "当前作业正在处理，或现在不能重新批改")
    audit(db, request, actor, "submission.regraded", submission_id)
    return data(request, {"submission_id": submission_id, "job_id": result["job_id"]})


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: UUID, request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    scoped_record(db, actor, "jobs", job_id)
    row = one(db, "SELECT * FROM jobs WHERE id=:id FOR UPDATE", id=job_id)
    if row["status"] == "cancelled":
        return data(request, {"job_id": job_id, "status": "cancelled"})
    if row["status"] not in ("queued", "running", "retry_wait"):
        raise DomainError(409, "job_state_conflict", "当前任务不能取消")
    run(db, "UPDATE jobs SET status='cancelled',finished_at=now() WHERE id=:id", id=job_id)
    run(db, "UPDATE submissions SET status='cancelled' WHERE id=:id", id=row["submission_id"])
    release_reservation(db, row["submission_id"])
    audit(db, request, actor, "job.cancelled", job_id)
    return data(request, {"job_id": job_id, "status": "cancelled"})
