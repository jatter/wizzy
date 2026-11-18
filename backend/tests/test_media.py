from io import BytesIO
from uuid import uuid4

from PIL import Image
from sqlalchemy import text


def photo():
    image = Image.new("RGB", (60, 80), "white")
    exif = Image.Exif()
    exif[0x010E] = "Sensitive location / student name"
    stream = BytesIO()
    image.save(stream, format="JPEG", exif=exif)
    return stream.getvalue()


def intent(client, headers, student_id, payload=None):
    content = payload or photo()
    result = client.post("/api/v1/media/upload-intents", headers=headers, json={
        "student_id": student_id, "content_type": "image/jpeg", "size_bytes": len(content),
    })
    assert result.status_code == 201, result.text
    return result.json()["data"], content


def upload(client, headers, student_id):
    value, content = intent(client, headers, student_id)
    response = client.put(value["upload_url"], headers=headers | {"Content-Type": "image/jpeg"}, content=content)
    assert response.status_code == 200, response.text
    response = client.post(f"/api/v1/media/{value['media_id']}/complete", headers=headers)
    assert response.status_code == 200, response.text
    return value["media_id"]


def test_private_image_sanitization_and_idempotent_completion(client, owner, student):
    headers = owner["headers"]
    media_id = upload(client, headers, student["id"])
    completed = client.post(f"/api/v1/media/{media_id}/complete", headers=headers)
    assert completed.json()["data"]["status"] == "ready"
    view = client.get(f"/api/v1/media/{media_id}/view-url", headers=headers).json()["data"]
    assert "token" not in view["url"]
    assert client.get(view["url"]).status_code == 401
    result = client.get(view["url"], headers=headers)
    assert result.status_code == 200
    with Image.open(BytesIO(result.content)) as image:
        assert not image.getexif()
        assert image.size == (60, 80)
    assert result.headers["cache-control"] == "no-store"


def test_decode_and_size_validation(client, owner, student):
    headers = owner["headers"]
    result = client.post("/api/v1/media/upload-intents", headers=headers, json={
        "student_id": student["id"], "content_type": "image/jpeg", "size_bytes": 10*1024*1024+1,
    })
    assert result.status_code == 413
    value, _ = intent(client, headers, student["id"], b"not-an-image")
    assert client.put(value["upload_url"], headers=headers | {"Content-Type": "image/jpeg"}, content=b"not-an-image").status_code == 200
    assert client.post(f"/api/v1/media/{value['media_id']}/complete", headers=headers).status_code == 422
    value, content = intent(client, headers, student["id"])
    assert client.put(value["upload_url"], headers=headers | {"Content-Type": "image/jpeg"}, content=content[:-1]).status_code == 422
    assert client.post(f"/api/v1/media/{value['media_id']}/complete", headers=headers).status_code == 409


def test_no_consent_no_upload_and_revocation_blocks_old_views(client, owner, student):
    headers = owner["headers"]
    media_id = upload(client, headers, student["id"])
    pending, content = intent(client, headers, student["id"])
    assert client.post("/api/v1/consents", headers=headers, json={
        "student_id": student["id"], "policy_version": "2026-10-25", "child_rules_version": "2026-10-25", "granted": False,
    }).status_code == 201
    assert client.get(f"/api/v1/media/{media_id}/content", headers=headers).status_code == 403
    assert client.post("/api/v1/media/upload-intents", headers=headers, json={
        "student_id": student["id"], "content_type": "image/jpeg", "size_bytes": len(content),
    }).status_code == 403
    assert client.put(pending["upload_url"], headers=headers | {"Content-Type": "image/jpeg"}, content=content).status_code == 403
    # Granting consent again must not resurrect media from a previous deletion epoch.
    assert client.post("/api/v1/consents", headers=headers, json={
        "student_id": student["id"], "policy_version": "2026-10-25", "child_rules_version": "2026-10-25", "granted": True,
    }).status_code == 201
    assert client.get(f"/api/v1/media/{media_id}/content", headers=headers).status_code == 404


def test_media_cross_family_and_sibling_isolation(client, owner, student):
    headers = owner["headers"]
    media_id = upload(client, headers, student["id"])
    other = client.post("/api/v1/tenants", headers=headers, json={"name": "Other"}).json()["data"]
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}
    for endpoint in ("complete", "view-url", "content"):
        method = client.post if endpoint == "complete" else client.get
        assert method(f"/api/v1/media/{media_id}/{endpoint}", headers=other_headers).status_code == 404
    assert client.get(f"/api/v1/media/{uuid4()}/content", headers=headers).status_code == 404


def test_reject_mismatched_format_and_oversized_pixels(client, owner, student):
    headers = owner["headers"]
    stream = BytesIO()
    Image.new("RGB", (10, 10)).save(stream, "PNG")
    value, payload = intent(client, headers, student["id"], stream.getvalue())
    assert client.put(value["upload_url"], headers=headers | {"Content-Type": "image/jpeg"}, content=payload).status_code == 200
    assert client.post(f"/api/v1/media/{value['media_id']}/complete", headers=headers).status_code == 422
    stream = BytesIO()
    Image.new("RGB", (4001, 5000)).save(stream, "JPEG")
    value, payload = intent(client, headers, student["id"], stream.getvalue())
    assert client.put(value["upload_url"], headers=headers | {"Content-Type": "image/jpeg"}, content=payload).status_code == 200
    result = client.post(f"/api/v1/media/{value['media_id']}/complete", headers=headers)
    assert result.status_code == 422
    assert result.json()["error"]["code"] == "pixel_limit_exceeded"


def test_expired_upload_and_concurrent_completion(client, owner, student, admin_engine):
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import text

    headers = owner["headers"]
    value, payload = intent(client, headers, student["id"])
    with admin_engine.begin() as db:
        db.execute(text("UPDATE media_assets SET expires_at=now()-interval '1 minute' WHERE id=:id"), {"id": value["media_id"]})
    assert client.put(value["upload_url"], headers=headers | {"Content-Type": "image/jpeg"}, content=payload).status_code == 409
    value, payload = intent(client, headers, student["id"])
    assert client.put(value["upload_url"], headers=headers | {"Content-Type": "image/jpeg"}, content=payload).status_code == 200
    def complete(_):
        return client.post(f"/api/v1/media/{value['media_id']}/complete", headers=headers)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(complete, range(2)))
    assert [result.status_code for result in results] == [200, 200]
    with admin_engine.connect() as db:
        count = db.execute(text("SELECT count(*) FROM audit_events WHERE resource_id=:id AND action='media.sanitized'"), {"id": value["media_id"]}).scalar_one()
    assert count == 1


def test_child_cannot_view_sibling_media_and_raw_not_exposed(client, owner, student, app, admin_engine):
    headers = owner["headers"]
    sibling = client.post("/api/v1/students", headers=headers, json={"nickname": "另一个孩子", "grade": 2}).json()["data"]
    client.post("/api/v1/consents", headers=headers, json={"student_id": sibling["id"], "policy_version": "2026-10-25", "child_rules_version": "2026-10-25", "granted": True})
    sibling_media = upload(client, headers, sibling["id"])
    code = client.post(f"/api/v1/students/{student['id']}/pairing-codes", headers=headers).json()["data"]["code"]
    token = client.post("/api/v1/child-sessions", json={"code": code, "device_name": "平板"}).json()["data"]["access_token"]
    child = {"Authorization": f"Bearer {token}"}
    assert client.get(f"/api/v1/media/{sibling_media}/content", headers=child).status_code == 404
    own = upload(client, child, student["id"])
    assert client.get(f"/api/v1/media/{own}/content", headers=child).status_code == 200
    # Sanitized files contain no EXIF and their original quarantine file has been removed.
    with admin_engine.connect() as db:
        keys = db.execute(text("SELECT raw_key FROM media_assets WHERE id IN (:own,:sibling)"),
                          {"own": own, "sibling": sibling_media}).scalars().all()
    assert all(not (app.state.settings.storage_root / key).exists() for key in keys)
    assert client.get("/private-media/", headers=headers).status_code == 404
