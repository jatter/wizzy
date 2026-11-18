"""Exercise real PDF intake and quarantine boundaries using restricted PostgreSQL roles."""
import asyncio
import hashlib
import json
import stat
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import text
from test_textbook_rights import BASE, action, create, session
from test_textbook_rights import platform as platform
from test_textbook_rights import reviewer as reviewer

from wizzy.security import digest

PDF = b'%PDF-1.7\nquarantine fixture; not a structurally validated PDF\n%%EOF\n'


@pytest.fixture(autouse=True)
def private_storage(app, tmp_path):
    app.state.settings.storage_root = tmp_path


@pytest.fixture
def approved(client, platform, reviewer):
    headers = session(client, platform)
    item = create(client, headers)
    item = action(client, headers, item, 'submit')
    return headers, action(client, reviewer['headers'], item, 'approve')


def body(item, content=PDF, **changes):
    return {'operation_id': str(uuid4()), 'expected_revision': item['revision'], 'filename': '数学.PDF',
            'size_bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest(), 'reason': '接收授权教材', **changes}


def intent(client, headers, item, **changes):
    payload = body(item, **changes)
    result = client.post(f"{BASE}/{item['id']}/documents", headers=headers, json=payload)
    assert result.status_code == 201, result.text
    return result.json()['data'], payload


def upload(client, headers, item, document, content=PDF, **extra):
    return client.put(f"{BASE}/{item['id']}/documents/{document['id']}/content",
                      headers={**headers, 'Content-Type': 'application/pdf', **extra}, content=content)


def test_receive_retry_listing_and_private_storage(client, approved, app, admin_engine):
    headers, item = approved
    document, payload = intent(client, headers, item)
    assert document['status'] == 'awaiting_upload' and document['received_at'] is None
    assert document['scan_status'] == 'not_scanned' and document['content_available'] is False
    path = f"{BASE}/{item['id']}/documents"
    assert client.post(path, headers=headers, json=payload).json()['data'] == document
    received = upload(client, headers, item, document)
    assert received.status_code == 201, received.text
    value = received.json()['data']
    assert value['status'] == 'quarantined' and value['received_at']
    assert value['scan_status'] == 'not_scanned' and value['content_available'] is False
    assert 'request_snapshot' not in value
    assert upload(client, headers, item, document).status_code == 200
    assert client.get(f"{path}/{document['id']}", headers=headers).json()['data'] == value
    assert client.get(f"{path}/{document['id']}/content", headers=headers).status_code == 405
    root = app.state.settings.storage_root / 'textbook-quarantine'
    files = list(root.iterdir())
    assert len(files) == 1 and files[0].name == UUID(document['id']).hex + '.pdf'
    assert files[0].read_bytes() == PDF and stat.S_IMODE(files[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    second, _ = intent(client, headers, item)
    page = client.get(path, headers=headers, params={'limit': 1}).json()['data']
    assert [x['id'] for x in page['items']] == [second['id']] and page['next_cursor']
    older = client.get(path, headers=headers, params={'limit': 1, 'cursor': page['next_cursor']}).json()['data']
    assert older['items'] == [value] and older['next_cursor'] is None
    assert client.get(path, headers=headers, params={'cursor': 'invalid'}).status_code == 422
    with admin_engine.connect() as db:
        events = db.execute(text("SELECT action FROM platform_audit_events WHERE actor_id=:id AND action LIKE 'textbooks.documents.%' ORDER BY created_at"), {'id': document['created_by']}).scalars().all()
        assert events == ['textbooks.documents.intent', 'textbooks.documents.received', 'textbooks.documents.intent']


def test_listing_preserves_fractional_timestamp_and_cursor(client, approved, admin_engine):
    headers, item = approved
    older, _ = intent(client, headers, item)
    newer, _ = intent(client, headers, item)
    with admin_engine.begin() as db:
        for document, timestamp in [(older, '2026-01-01T00:00:00.120000+00:00'),
                                    (newer, '2026-01-01T00:00:00.123450+00:00')]:
            db.execute(text('UPDATE textbook_documents SET created_at=CAST(:timestamp AS timestamptz) WHERE id=:id'),
                       {'timestamp': timestamp, 'id': document['id']})
    path = f"{BASE}/{item['id']}/documents"
    details = [client.get(f"{path}/{document['id']}", headers=headers).json()['data']
               for document in (newer, older)]
    assert [document['created_at'] for document in details] == [
        '2026-01-01T00:00:00.12345+00:00', '2026-01-01T00:00:00.12+00:00']
    first = client.get(path, headers=headers, params={'limit': 1}).json()['data']
    assert first['items'] == [details[0]] and first['next_cursor']
    second = client.get(path, headers=headers, params={'limit': 1, 'cursor': first['next_cursor']}).json()['data']
    assert second['items'] == [details[1]] and second['next_cursor'] is None


@pytest.mark.parametrize('change', [
    {'filename': '../book.pdf'}, {'filename': 'folder\\book.pdf'}, {'filename': 'book.txt'},
    {'filename': ' book.pdf'}, {'filename': 'x\n.pdf'}, {'size_bytes': 0}, {'size_bytes': True},
    {'size_bytes': 52428801}, {'size_bytes': 3.0}, {'sha256': 'A'*64}, {'sha256': 'bad'},
    {'expected_revision': True}, {'expected_revision': 0}, {'reason': ''}, {'reason': '\t理由'},
    {'extra': True}, {'operation_id': 'bad'},
])
def test_strict_metadata_validation(client, approved, change):
    headers, item = approved
    result = client.post(f"{BASE}/{item['id']}/documents", headers=headers, json=body(item, **change))
    assert result.status_code == 422, result.text


@pytest.mark.parametrize('content,change,extra,code', [
    (PDF, {'sha256': '0'*64}, {}, 'checksum_mismatch'),
    (b'not a PDF', {}, {}, 'invalid_pdf_signature'),
    (PDF[:-1], {'size_bytes': len(PDF)}, {}, 'size_mismatch'),
    (PDF, {}, {'Content-Type': 'text/plain'}, 'content_type_mismatch'),
    (PDF, {}, {'Content-Length': 'abc'}, 'size_mismatch'),
])
def test_reject_bytes_without_receipt_or_temporary_file(client, approved, app, content, change, extra, code):
    headers, item = approved
    document, _ = intent(client, headers, item, content=content, **change)
    result = upload(client, headers, item, document, content, **extra)
    assert result.status_code == 422 and result.json()['error']['code'] == code, result.text
    value = client.get(f"{BASE}/{item['id']}/documents/{document['id']}", headers=headers).json()['data']
    assert value['status'] == 'awaiting_upload'
    root = app.state.settings.storage_root / 'textbook-quarantine'
    assert not root.exists() or not list(root.iterdir())


def test_expiry_withdrawal_and_revision_conflict(client, approved, admin_engine):
    headers, item = approved
    document, _ = intent(client, headers, item)
    with admin_engine.begin() as db:
        db.execute(text("UPDATE textbook_documents SET created_at=clock_timestamp()-interval '2 hours',expires_at=clock_timestamp()-interval '1 hour' WHERE id=:id"), {'id': document['id']})
    assert upload(client, headers, item, document).json()['error']['code'] == 'upload_expired'
    live, _ = intent(client, headers, item)
    withdrawn = action(client, headers, item, 'withdraw')
    assert upload(client, headers, item, live).json()['error']['code'] == 'document_rights_unavailable'
    assert client.post(f"{BASE}/{item['id']}/documents", headers=headers, json=body(withdrawn)).status_code == 409


def test_draft_and_stale_revision_are_rejected(client, approved):
    headers, item = approved
    draft = create(client, headers)
    assert client.post(f"{BASE}/{draft['id']}/documents", headers=headers, json=body(draft)).json()['error']['code'] == 'document_rights_unavailable'
    assert client.post(f"{BASE}/{item['id']}/documents", headers=headers, json=body(item, expected_revision=1)).json()['error']['code'] == 'textbook_revision_conflict'


def test_live_permission_owner_and_idempotency(client, approved, reviewer, platform, admin_engine):
    headers, item = approved
    document, payload = intent(client, headers, item)
    path = f"{BASE}/{item['id']}/documents"
    assert client.post(path, headers=headers, json={**payload, 'filename': 'other.pdf'}).json()['error']['code'] == 'operation_conflict'
    assert upload(client, reviewer['headers'], item, document).status_code == 403
    with admin_engine.begin() as db:
        db.execute(text("UPDATE platform_accounts SET role='operator' WHERE id=:id"), {'id': platform['id']})
    assert client.get(path, headers=headers).status_code == 200
    assert client.post(path, headers=headers, json=payload).status_code == 403
    assert upload(client, headers, item, document).status_code == 403
    with admin_engine.begin() as db:
        assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE action='access.denied' AND actor_id IN (:a,:b)"), {'a': platform['id'], 'b': reviewer['id']}).scalar_one() == 3
        db.execute(text("UPDATE platform_accounts SET status='disabled' WHERE id=:id"), {'id': platform['id']})
    assert client.get(path, headers=headers).status_code == 401
    assert upload(client, headers, item, document).status_code == 401


def test_authorization_rechecked_after_stream(client, approved, app, admin_engine, monkeypatch):
    headers, item = approved
    document, _ = intent(client, headers, item)
    original = Request.stream
    async def stream(request):
        async for chunk in original(request):
            yield chunk
        # The endpoint has authenticated and consumed bytes before this change.
        if request.method == 'PUT' and request.url.path.endswith('/content'):
            await asyncio.to_thread(action, client, headers, item, 'withdraw')
    monkeypatch.setattr(Request, 'stream', stream)
    result = upload(client, headers, item, document)
    assert result.status_code == 409 and result.json()['error']['code'] == 'document_rights_unavailable'
    assert not list((app.state.settings.storage_root / 'textbook-quarantine').iterdir())


def test_concurrent_intents_and_uploads(client, approved, app):
    headers, item = approved
    payload = body(item)
    path = f"{BASE}/{item['id']}/documents"
    def post(_):
        with TestClient(app, client=(f'parallel-{uuid4()}', 50000)) as other:
            return other.post(path, headers=headers, json=payload)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(post, range(2)))
    assert sorted(r.status_code for r in results) == [200, 201]
    document = results[0].json()['data']
    def put(_):
        with TestClient(app, client=(f'parallel-{uuid4()}', 50000)) as other:
            return upload(other, headers, item, document)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(put, range(2)))
    assert sorted(r.status_code for r in results) == [200, 201]
    assert len(list((app.state.settings.storage_root / 'textbook-quarantine').iterdir())) == 1


def test_failed_receipt_commit_recovers_existing_isolated_file(client, approved, app, admin_engine):
    headers, item = approved
    document, _ = intent(client, headers, item)
    with admin_engine.begin() as db:
        db.execute(text("""CREATE FUNCTION public.fail_document_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.action='textbooks.documents.received' THEN RAISE EXCEPTION 'injected'; END IF; RETURN NEW; END $$;
            CREATE TRIGGER fail_document_receipt BEFORE INSERT ON platform_audit_events FOR EACH ROW EXECUTE FUNCTION public.fail_document_receipt()"""))
    try:
        with TestClient(app, raise_server_exceptions=False) as failing:
            assert upload(failing, headers, item, document).status_code == 500
    finally:
        with admin_engine.begin() as db:
            db.execute(text('DROP TRIGGER fail_document_receipt ON platform_audit_events; DROP FUNCTION public.fail_document_receipt()'))
    assert len(list((app.state.settings.storage_root / 'textbook-quarantine').iterdir())) == 1
    assert upload(client, headers, item, document).status_code == 201


@pytest.mark.parametrize('change', [{'size_bytes': True}, {'size_bytes': 1e100}, {'reason': None},
                                    {'filename': '../x.pdf'}, {'expected_revision': 1e100}, {'extra': 1}])
def test_direct_capability_validation(client, approved, app, change):
    headers, item = approved
    with app.state.engine.begin() as db:
        result = db.execute(text("""SELECT public.textbook_document_request(:hash,'intent','sql-test',
            CAST(:body AS jsonb),:edition,NULL,50,NULL,NULL)"""), {
                'hash': digest(headers['Authorization'].split()[1]), 'body': json.dumps(body(item, **change)),
                'edition': item['id']}).scalar_one()
    assert result['status'] == 422
