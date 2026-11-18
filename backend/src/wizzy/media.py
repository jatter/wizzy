"""Local private object adapter. Raw input is never exposed by an HTTP route."""
import hashlib
import os
import tempfile
from datetime import UTC, datetime, timedelta
from io import BytesIO
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import Response
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from wizzy.db import get_db, one, run
from wizzy.errors import DomainError, data
from wizzy.schemas import UploadIntent
from wizzy.security import Principal, audit, current, student_record

router = APIRouter(prefix="/api/v1/media", tags=["private media"])
MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 20_000_000
FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


def consent_student(db, actor, student_id):
    student_record(db, actor, student_id)
    # SHARE coordinates with consent withdrawal without lock-upgrading child sessions.
    student = one(db, "SELECT * FROM students WHERE id=:id FOR SHARE", id=student_id)
    if not student["consent_active"]:
        raise DomainError(403, "consent_required", "需要监护人同意后才能处理作业图片")
    return student


def asset(db, actor, media_id, *, lock=False):
    row = one(db, "SELECT * FROM media_assets WHERE id=:id", id=media_id)
    if not row:
        raise DomainError(404, "not_found", "图片不存在")
    student = consent_student(db, actor, row["student_id"])
    if row["deletion_epoch"] != student["deletion_epoch"]:
        raise DomainError(404, "not_found", "图片不存在")
    if lock:
        row = one(db, "SELECT * FROM media_assets WHERE id=:id FOR UPDATE", id=media_id)
    return row


def live_intent(row):
    if row["expires_at"] <= datetime.now(UTC):
        raise DomainError(409, "upload_expired", "上传凭证已过期，请重新创建")


@router.post("/upload-intents", status_code=201)
def create_intent(body: UploadIntent, request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    student = consent_student(db, actor, body.student_id)
    if body.size_bytes > MAX_BYTES:
        raise DomainError(413, "image_too_large", "单张图片不得超过10 MiB")
    media_id, key = uuid4(), f"{uuid4().hex}.raw"
    expires = datetime.now(UTC) + timedelta(minutes=5)
    run(db, """INSERT INTO media_assets(id,tenant_id,student_id,deletion_epoch,content_type,
        size_bytes,raw_key,expires_at) VALUES(:id,:tenant,:student,:epoch,:type,:size,:key,:expires)""",
        id=media_id, tenant=actor.tenant_id, student=body.student_id, epoch=student["deletion_epoch"],
        type=body.content_type, size=body.size_bytes, key=key, expires=expires)
    audit(db, request, actor, "media.intent_created", media_id)
    return data(request, {"media_id": media_id, "upload_url": f"/api/v1/media/{media_id}/upload",
                          "expires_at": expires, "max_size_bytes": MAX_BYTES})


@router.put("/{media_id}/upload")
async def upload_bytes(media_id: UUID, request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    row = asset(db, actor, media_id, lock=True)
    if row["status"] != "intent":
        raise DomainError(409, "upload_state_conflict", "图片已上传，不能覆盖")
    live_intent(row)
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != row["content_type"]:
        raise DomainError(422, "content_type_mismatch", "上传格式与声明不一致")
    root = request.app.state.settings.storage_root
    # Temporary files are 0600; the root is private and keys are server-generated.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix="upload-", delete=False) as output:
            temporary = output.name
            count = 0
            async for chunk in request.stream():
                count += len(chunk)
                if count > MAX_BYTES:
                    raise DomainError(413, "image_too_large", "单张图片不得超过10 MiB")
                if count > row["size_bytes"]:
                    raise DomainError(422, "size_mismatch", "实际大小与上传声明不一致")
                output.write(chunk)
        if count != row["size_bytes"]:
            raise DomainError(422, "size_mismatch", "实际大小与上传声明不一致")
        os.replace(temporary, root / row["raw_key"])
        run(db, "UPDATE media_assets SET status='uploaded' WHERE id=:id", id=media_id)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return data(request, {"media_id": media_id, "status": "uploaded"})


def sanitize(path, content_type):
    try:
        with Image.open(path, formats=[FORMATS[content_type]]) as image:
            width, height = image.size
            if width * height > MAX_PIXELS or width < 1 or height < 1:
                raise DomainError(422, "pixel_limit_exceeded", "图片像素超过2000万上限")
            if getattr(image, "n_frames", 1) != 1:
                raise DomainError(422, "animated_image", "不支持动画图片")
            image.load()  # Decode all pixels, not just trusting the file header.
            oriented = ImageOps.exif_transpose(image)
            clean = Image.new("RGB", oriented.size, "white")
            if "A" in oriented.getbands():
                clean.paste(oriented, mask=oriented.getchannel("A"))
            else:
                clean.paste(oriented.convert("RGB"))
            output = BytesIO()
            clean.save(output, format="JPEG", quality=90)  # No EXIF, ICC, XMP or source info.
            return output.getvalue(), clean.width, clean.height
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise DomainError(422, "invalid_image", "图片解码失败或实际格式与声明不符") from exc


@router.post("/{media_id}/complete")
def complete(media_id: UUID, request: Request, background: BackgroundTasks, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    row = asset(db, actor, media_id, lock=True)
    if row["status"] == "ready":
        return data(request, {"media_id": media_id, "status": "ready", "width": row["width"], "height": row["height"]})
    live_intent(row)
    if row["status"] != "uploaded":
        raise DomainError(409, "upload_incomplete", "图片尚未完整上传")
    root = request.app.state.settings.storage_root
    payload, width, height = sanitize(root / row["raw_key"], row["content_type"])
    key = f"{uuid4().hex}.jpg"
    with (root / key).open("xb") as output:
        os.chmod(root / key, 0o600)
        output.write(payload)
    run(db, """UPDATE media_assets SET status='ready',clean_key=:key,width=:width,height=:height,
        sha256=:sha WHERE id=:id""", id=media_id, key=key, width=width, height=height,
        sha=hashlib.sha256(payload).hexdigest())
    audit(db, request, actor, "media.sanitized", media_id)
    background.add_task((root / row["raw_key"]).unlink, missing_ok=True)
    return data(request, {"media_id": media_id, "status": "ready", "width": width, "height": height})


def ready_asset(db, actor, media_id):
    row = asset(db, actor, media_id)
    if row["status"] != "ready":
        raise DomainError(409, "media_not_ready", "图片尚未通过校验")
    return row


@router.get("/{media_id}/view-url")
def view_url(media_id: UUID, request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    ready_asset(db, actor, media_id)
    return data(request, {"url": f"/api/v1/media/{media_id}/content", "requires_authorization": True})


@router.get("/{media_id}/content", response_class=Response)
def content(media_id: UUID, request: Request, actor: Principal = Depends(current), db: Session = Depends(get_db, scope="function")):
    row = ready_asset(db, actor, media_id)
    path = request.app.state.settings.storage_root / row["clean_key"]
    if not path.is_file():
        raise DomainError(503, "storage_unavailable", "图片暂不可用", retryable=True)
    # Materialize while holding the consent lock; never serve untrusted raw uploads.
    return Response(path.read_bytes(), media_type="image/jpeg", headers={"Content-Disposition": 'inline; filename="homework.jpg"'})
