from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from sqlalchemy import text
from test_media import upload


def payload(student_id, media_ids):
    return {"student_id": student_id, "subject": "math", "completed_attempt": True,
            "media_ids": media_ids, "client_request_id": str(uuid4())}


def submit(client, headers, body, key=None):
    return client.post("/api/v1/submissions", headers=headers | {"Idempotency-Key": key or str(uuid4())}, json=body)


def test_dictation_options_are_persisted_and_part_of_the_idempotent_request(client, owner, student):
    body = payload(student['id'], [upload(client, owner['headers'], student['id'])])
    body.update(activity_type='dictation', reference_text='第1张：\n1. whether\n2. take care of')
    key = str(uuid4())
    first = submit(client, owner['headers'], body, key)
    assert first.status_code == 202, first.text
    identifier = first.json()['data']['submission_id']
    detail = client.get(f'/api/v1/submissions/{identifier}', headers=owner['headers']).json()['data']
    assert detail['activity_type'] == 'dictation' and detail['reference_text'] == body['reference_text']
    assert submit(client, owner['headers'], body, key).json()['data'] == first.json()['data']
    assert submit(client, owner['headers'], body | {'reference_text': 'changed'}, key).status_code == 409
    for extra in ({'activity_type': 'invalid'}, {'reference_text': 'a' * 8001}, {'reference_text': 'bad\x00'}):
        assert submit(client, owner['headers'], body | extra).status_code == 422


def test_old_idempotency_hash_stays_valid_with_default_dictation_options(client, owner, student, admin_engine):
    import hashlib
    import json

    body = payload(student['id'], [upload(client, owner['headers'], student['id'])])
    key = str(uuid4())
    saved = submit(client, owner['headers'], body, key)
    identifier = saved.json()['data']['submission_id']
    old_hash = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    with admin_engine.begin() as db:
        db.execute(text('UPDATE submissions SET body_hash=:hash WHERE id=:id'), {'hash': old_hash, 'id': identifier})
    assert submit(client, owner['headers'], body, key).json()['data'] == saved.json()['data']


def test_atomic_submission_idempotency_and_quota(client, owner, student, admin_engine):
    headers = owner["headers"]
    media_id = upload(client, headers, student["id"])
    body, key = payload(student["id"], [media_id]), str(uuid4())
    first = submit(client, headers, body, key)
    assert first.status_code == 202, first.text
    value = first.json()["data"]
    assert value["status"] == "queued" and value["source_revision"] == 1
    assert submit(client, headers, body, key).json()["data"] == value
    assert submit(client, headers, body | {"client_request_id": str(uuid4())}, key).status_code == 409
    quota = client.get("/api/v1/quota", headers=headers).json()["data"]
    assert quota["reserved_pages"] == 1 and quota["used_pages"] == 0
    assert quota["timezone"] == "Asia/Shanghai"
    detail = client.get(f"/api/v1/submissions/{value['submission_id']}", headers=headers)
    assert detail.status_code == 200
    assert [page["media_id"] for page in detail.json()["data"]["pages"]] == [media_id]
    job = client.get(f"/api/v1/jobs/{value['job_id']}", headers=headers)
    assert job.json()["data"]["status"] == "queued"
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM outbox_events WHERE aggregate_id=:id"), {"id": value["job_id"]}).scalar_one() == 1
        assert db.execute(text("SELECT count(*) FROM quota_reservations WHERE submission_id=:id"), {"id": value["submission_id"]}).scalar_one() == 1


def test_submission_concurrency_and_cancel_once(client, owner, student):
    headers = owner["headers"]
    body = payload(student["id"], [upload(client, headers, student["id"])])
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: submit(client, headers, body, key), range(4)))
    assert [result.status_code for result in results] == [202] * 4
    assert len({result.json()["data"]["submission_id"] for result in results}) == 1
    value = results[0].json()["data"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.post(f"/api/v1/jobs/{value['job_id']}/cancel", headers=headers), range(2)))
    assert [result.status_code for result in results] == [200, 200]
    quota = client.get("/api/v1/quota", headers=headers).json()["data"]
    assert quota["reserved_pages"] == 0 and quota["used_pages"] == 0
    assert client.get(f"/api/v1/jobs/{value['job_id']}", headers=headers).json()["data"]["status"] == "cancelled"


def test_validation_scope_and_no_partial_quota_changes(client, owner, student):
    headers = owner["headers"]
    media_id = upload(client, headers, student["id"])
    body = payload(student["id"], [media_id])
    assert client.post("/api/v1/submissions", headers=headers, json=body).status_code == 422
    assert submit(client, headers, body | {"media_ids": []}).status_code == 422
    assert submit(client, headers, body | {"media_ids": [media_id] * 11}).status_code == 422
    assert submit(client, headers, body | {"media_ids": [media_id, media_id]}).status_code == 422
    assert submit(client, headers, body | {"completed_attempt": False}).status_code == 422
    assert submit(client, headers, body | {"media_ids": [str(uuid4())]}).status_code == 404
    sibling = client.post("/api/v1/students", headers=headers, json={"nickname": "另一个", "grade": 2}).json()["data"]
    assert submit(client, headers, body | {"student_id": sibling["id"]}).status_code == 403
    quota = client.get("/api/v1/quota", headers=headers).json()["data"]
    assert quota["reserved_pages"] == 0
    value = submit(client, headers, body).json()["data"]
    other = client.post("/api/v1/tenants", headers=headers, json={"name": "另一个家庭"}).json()["data"]
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}
    for path in (f"/submissions/{value['submission_id']}", f"/jobs/{value['job_id']}"):
        assert client.get("/api/v1" + path, headers=other_headers).status_code == 404
    assert client.post(f"/api/v1/jobs/{value['job_id']}/cancel", headers=other_headers).status_code == 404
    assert submit(client, other_headers, body).status_code == 404


def test_quota_exhaustion_serialized(client, owner, student, admin_engine):
    headers = owner["headers"]
    body = payload(student["id"], [upload(client, headers, student["id"])])
    client.get("/api/v1/quota", headers=headers)
    with admin_engine.begin() as db:
        db.execute(text("UPDATE quota_accounts SET page_limit=1 WHERE tenant_id=:tenant"), {"tenant": owner["tenant_id"]})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(client, headers, body), range(2)))
    assert sorted(result.status_code for result in results) == [202, 409]
    quota = client.get("/api/v1/quota", headers=headers).json()["data"]
    assert quota["reserved_pages"] == 1 and quota["remaining_pages"] == 0


def test_submission_list_stable_cursor_and_child_scope(client, owner, student):
    headers = owner["headers"]
    body = payload(student["id"], [upload(client, headers, student["id"])])
    ids = [submit(client, headers, body).json()["data"]["submission_id"] for _ in range(3)]
    first = client.get("/api/v1/submissions?limit=2", headers=headers).json()["data"]
    assert [row["id"] for row in first["items"]] == ids[::-1][:2]
    second = client.get("/api/v1/submissions", params={"limit": 2, "cursor": first["next_cursor"]}, headers=headers).json()["data"]
    assert [row["id"] for row in second["items"]] == ids[::-1][2:]
    assert client.get("/api/v1/submissions?limit=101", headers=headers).status_code == 422
    assert client.get("/api/v1/submissions?cursor=bad", headers=headers).status_code == 422


def test_child_and_limited_guardian_cannot_access_sibling_work(client, owner, student):
    from test_identity_security import registered

    headers = owner["headers"]
    sibling = client.post("/api/v1/students", headers=headers, json={"nickname": "妹妹", "grade": 2}).json()["data"]
    client.post("/api/v1/consents", headers=headers, json={"student_id": sibling["id"], "policy_version": "2026-10-25", "child_rules_version": "2026-10-25", "granted": True})
    own = submit(client, headers, payload(student["id"], [upload(client, headers, student["id"])] )).json()["data"]
    other = submit(client, headers, payload(sibling["id"], [upload(client, headers, sibling["id"])] )).json()["data"]
    code = client.post(f"/api/v1/students/{student['id']}/pairing-codes", headers=headers).json()["data"]["code"]
    child = client.post("/api/v1/child-sessions", json={"code": code, "device_name": "提交测试设备"}).json()["data"]
    email, user = registered(client)
    invitation = client.post("/api/v1/guardian-invitations", headers=headers, json={"email": email, "student_ids": [student["id"]]}).json()["data"]
    guardian = client.post("/api/v1/guardian-invitations/accept", headers={"Authorization": f"Bearer {user['access_token']}"}, json={"code": invitation["code"]}).json()["data"]
    for session in (child, guardian):
        scoped = {"Authorization": f"Bearer {session['access_token']}"}
        items = client.get("/api/v1/submissions", headers=scoped).json()["data"]["items"]
        assert [item["id"] for item in items] == [own["submission_id"]]
        filtered = client.get("/api/v1/submissions", params={"student_id": student["id"]}, headers=scoped)
        assert [item["id"] for item in filtered.json()["data"]["items"]] == [own["submission_id"]]
        assert client.get("/api/v1/submissions", params={"student_id": sibling["id"]}, headers=scoped).status_code == 404
        assert client.get(f"/api/v1/submissions/{own['submission_id']}", headers=scoped).status_code == 200
        assert client.get(f"/api/v1/submissions/{other['submission_id']}", headers=scoped).status_code == 404
        assert client.get(f"/api/v1/jobs/{other['job_id']}", headers=scoped).status_code == 404
        assert client.post(f"/api/v1/jobs/{other['job_id']}/cancel", headers=scoped).status_code == 404
    assert client.get("/api/v1/quota", headers={"Authorization": f"Bearer {child['access_token']}"}).status_code == 403


def test_submission_transaction_rolls_back_outbox_quota_and_audit(client, owner, student, admin_engine, monkeypatch):
    import wizzy.submissions as module

    body = payload(student["id"], [upload(client, owner["headers"], student["id"])])
    original = module.audit

    def fail_after_writes(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected failure after submission writes")

    monkeypatch.setattr(module, "audit", fail_after_writes)
    from fastapi.testclient import TestClient

    with TestClient(client.app, raise_server_exceptions=False) as failing:
        result = submit(failing, owner["headers"], body)
    assert result.status_code == 500
    with admin_engine.connect() as db:
        for table in ("submissions", "submission_pages", "jobs", "quota_reservations", "outbox_events"):
            assert db.execute(text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"), {"tenant": owner["tenant_id"]}).scalar_one() == 0
        assert db.execute(text("SELECT count(*) FROM audit_events WHERE tenant_id=:tenant AND action='submission.created'"), {"tenant": owner["tenant_id"]}).scalar_one() == 0
    monkeypatch.setattr(module, "audit", original)
    quota = client.get("/api/v1/quota", headers=owner["headers"]).json()["data"]
    assert quota["reserved_pages"] == 0
    assert submit(client, owner["headers"], body).status_code == 202


def test_consent_withdrawal_cancels_jobs_and_releases_quota_without_resurrection(client, owner, student, admin_engine):
    headers = owner["headers"]
    body, key = payload(student["id"], [upload(client, headers, student["id"])]), str(uuid4())
    value = submit(client, headers, body, key).json()["data"]
    consent = {"student_id": student["id"], "policy_version": "2026-10-25", "child_rules_version": "2026-10-25", "granted": False}
    for _ in range(2):
        assert client.post("/api/v1/consents", headers=headers, json=consent).status_code == 201
    assert client.get("/api/v1/quota", headers=headers).json()["data"]["reserved_pages"] == 0
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT status FROM jobs WHERE id=:id"), {"id": value["job_id"]}).scalar_one() == "cancelled"
        assert db.execute(text("SELECT status FROM quota_reservations WHERE submission_id=:id"), {"id": value["submission_id"]}).scalar_one() == "released"
    client.post("/api/v1/consents", headers=headers, json=consent | {"granted": True})
    assert client.get(f"/api/v1/submissions/{value['submission_id']}", headers=headers).status_code == 404
    assert client.get(f"/api/v1/jobs/{value['job_id']}", headers=headers).status_code == 404
    assert client.get("/api/v1/submissions", headers=headers).json()["data"]["items"] == []
    assert submit(client, headers, body, key).status_code == 404


def test_student_filter_pagination_validation_and_revoked_consent(client, owner, student):
    headers = owner["headers"]
    sibling = client.post("/api/v1/students", headers=headers, json={"nickname": "妹妹", "grade": 2}).json()["data"]
    consent = {"student_id": sibling["id"], "policy_version": "2026-10-25", "child_rules_version": "2026-10-25", "granted": True}
    assert client.post("/api/v1/consents", headers=headers, json=consent).status_code == 201
    expected = []
    for index in range(3):
        sid = student["id"] if index == 1 else sibling["id"]
        value = submit(client, headers, payload(sid, [upload(client, headers, sid)])).json()["data"]
        if sid == sibling["id"]:
            expected.append(value["submission_id"])
    params = {"student_id": sibling["id"], "limit": 1}
    first = client.get("/api/v1/submissions", headers=headers, params=params).json()["data"]
    second = client.get("/api/v1/submissions", headers=headers, params=params | {"cursor": first["next_cursor"]}).json()["data"]
    assert [first["items"][0]["id"], second["items"][0]["id"]] == expected[::-1]
    assert second["next_cursor"] is None
    assert client.get("/api/v1/submissions", headers=headers, params={"student_id": "invalid"}).status_code == 422
    assert client.get("/api/v1/submissions", headers=headers, params={"student_id": str(uuid4())}).status_code == 404
    assert client.post("/api/v1/consents", headers=headers, json=consent | {"granted": False}).status_code == 201
    assert client.get("/api/v1/submissions", headers=headers, params=params).status_code == 403


def test_history_filters_use_inclusive_shanghai_dates_and_keep_pagination(client, owner, student, admin_engine):
    headers = owner["headers"]
    timestamps = ["2026-10-24T23:59:59.999999+08:00", "2026-10-25T00:00:00+08:00",
                  "2026-10-25T23:59:59.999999+08:00", "2026-10-26T00:00:00+08:00"]
    records = [submit(client, headers, payload(student["id"], [upload(client, headers, student["id"])])).json()["data"]
               for _ in timestamps]
    assert client.post(f"/api/v1/jobs/{records[2]['job_id']}/cancel", headers=headers).status_code == 200
    with admin_engine.begin() as db:
        for record, timestamp in zip(records, timestamps, strict=True):
            db.execute(text("UPDATE submissions SET created_at=:at WHERE id=:id"),
                       {"at": timestamp, "id": record["submission_id"]})
    filters = {"student_id": student["id"], "date_from": "2026-10-25", "date_to": "2026-10-25", "subject": "math", "limit": 1}
    first = client.get("/api/v1/submissions", headers=headers, params=filters).json()["data"]
    second = client.get("/api/v1/submissions", headers=headers, params=filters | {"cursor": first["next_cursor"]}).json()["data"]
    assert [first["items"][0]["id"], second["items"][0]["id"]] == [r["submission_id"] for r in records[1:3][::-1]]
    assert second["next_cursor"] is None
    cancelled = client.get("/api/v1/submissions", headers=headers, params=filters | {"status": "cancelled"}).json()["data"]
    assert [item["id"] for item in cancelled["items"]] == [records[2]["submission_id"]]
    queued = client.get("/api/v1/submissions", headers=headers, params=filters | {"status": "queued"}).json()["data"]
    assert [item["id"] for item in queued["items"]] == [records[1]["submission_id"]]
    after = client.get("/api/v1/submissions", headers=headers, params={"date_from": "2026-10-26"}).json()["data"]
    assert [item["id"] for item in after["items"]] == [records[3]["submission_id"]]
    before = client.get("/api/v1/submissions", headers=headers, params={"date_to": "2026-10-24"}).json()["data"]
    assert [item["id"] for item in before["items"]] == [records[0]["submission_id"]]


def test_history_filter_validation_and_authorization_before_empty_results(client, owner, student):
    for filters in ({"date_from": "2026-02-30"}, {"date_to": "invalid"},
                    {"date_from": "2026-10-26", "date_to": "2026-10-25"},
                    {"status": "unknown"}, {"status": "running"}, {"subject": "science"}):
        assert client.get("/api/v1/submissions", headers=owner["headers"], params=filters).status_code == 422
    filters = {"student_id": str(uuid4()), "date_to": "2000-01-01", "status": "cancelled"}
    assert client.get("/api/v1/submissions", headers=owner["headers"], params=filters).status_code == 404


def test_automatic_subject_default_and_date_tabs_are_scoped(client, owner, student, admin_engine):
    headers = owner['headers']
    body = payload(student['id'], [upload(client, headers, student['id'])])
    body.pop('subject')
    record = submit(client, headers, body).json()['data']
    identifier = record['submission_id']
    assert client.get(f'/api/v1/submissions/{identifier}', headers=headers).json()['data']['subject'] == 'auto'
    with admin_engine.begin() as db:
        db.execute(text('UPDATE submissions SET created_at=:at WHERE id=:id'), {'at': '2026-11-13T16:01:00Z', 'id': identifier})
    days = client.get(f"/api/v1/students/{student['id']}/submission-days", headers=headers)
    assert days.status_code == 200
    assert days.json()['data']['items'] == [{'date': '2026-11-14', 'count': 1}]
    assert client.get(f'/api/v1/students/{uuid4()}/submission-days', headers=headers).status_code == 404
    listed = client.get('/api/v1/submissions', params={'student_id': student['id']}, headers=headers).json()['data']['items']
    assert listed[0]['page_count'] == 1
