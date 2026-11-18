"""Durable jobs through real PostgreSQL capabilities and isolated PDF extraction."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_pdf_processing import CleanForTest, pdf_bytes
from test_textbook_documents import approved as approved
from test_textbook_documents import intent, upload
from test_textbook_documents import private_storage as private_storage
from test_textbook_rights import BASE, action
from test_textbook_rights import platform as platform
from test_textbook_rights import reviewer as reviewer

from wizzy.pdf_scan import ScanUnavailable, UnsafeFile
from wizzy.textbook_worker import TextbookWorker


@pytest.fixture
def received(client, approved):
    headers, item = approved
    content = pdf_bytes(('First source page', '', 'Third source page'))
    document, _ = intent(client, headers, item, content=content)
    assert upload(client, headers, item, document, content=content).status_code == 201
    path = f"{BASE}/{item['id']}/documents/{document['id']}/processing-jobs"
    return headers, item, document, path


def request_body(item, **changes):
    return {'operation_id': str(uuid4()), 'expected_revision': item['revision'],
            'reason': '提取授权教材逐页草稿', **changes}


def submit(client, received, **changes):
    headers, item, _, path = received
    payload = request_body(item, **changes)
    response = client.post(path, headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()['data'], payload


@pytest.fixture
def worker(database, app):
    engine = create_engine(database['worker_url'], hide_parameters=True)
    settings = app.state.settings.model_copy(update={'clamav_host': '127.0.0.1'})
    value = TextbookWorker(engine, settings)
    value.scanner = CleanForTest()  # Explicit test injection; never configurable in production.
    yield value
    engine.dispose()


def call(worker, op, job=None, token=None, result=None):
    return worker._call(op, job=UUID(job) if job else None, token=UUID(token) if token else None, result=result)


def expire(admin_engine, job):
    with admin_engine.begin() as db:
        db.execute(text("UPDATE textbook_processing_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=:id"), {'id': job})


def terminal():
    return {'status': 'unavailable', 'code': 'scanner_unavailable', 'scan': None,
            'draft': None, 'content_available': False}


def test_round_trip_real_pdf_idempotency_history_and_pages(client, received, worker, admin_engine):
    headers, item, document, path = received
    job, payload = submit(client, received)
    assert job['state'] == 'queued' and job['attempts'] == 0
    assert client.post(path, headers=headers, json=payload).json()['data'] == job
    assert client.post(path, headers=headers, json={**payload, 'reason': '不同请求'}).json()['error']['code'] == 'operation_conflict'
    assert client.post(path, headers=headers, json=request_body(item)).json()['error']['code'] == 'processing_active'
    assert worker.consume_one() == 'finished'
    assert worker.consume_one() == 'idle'
    detail = client.get(f"{path}/{job['id']}", headers=headers).json()['data']
    assert detail['state'] == 'draft_ready' and detail['page_count'] == 3
    assert detail['pages_without_text'] == [2] and detail['character_count'] == 34
    assert detail['scan']['database_revision'] == 1
    for field in ('pages', 'result', 'lease_token', 'session_id', 'request_snapshot', 'operation_id'):
        assert field not in detail
    assert 'First source page' not in json.dumps(detail)
    for number, expected in [(1, 'First source page'), (2, ''), (3, 'Third source page')]:
        page = client.get(f"{path}/{job['id']}/pages/{number}", headers=headers).json()['data']
        assert page['page'] == {'number': number, 'text': expected}
        assert page['requires_review'] and page['ocr_performed'] is False and page['content_available'] is False
    assert client.get(f"{path}/{job['id']}/pages/4", headers=headers).status_code == 404
    assert client.get(f"{path}/{job['id']}/pages/0", headers=headers).status_code == 422
    newer, _ = submit(client, received)
    listing = client.get(path, headers=headers, params={'limit': 1}).json()['data']
    assert [row['id'] for row in listing['items']] == [newer['id']]
    older = client.get(path, headers=headers, params={'limit': 1, 'cursor': listing['next_cursor']}).json()['data']
    assert [row['id'] for row in older['items']] == [job['id']] and older['next_cursor'] is None
    assert client.get(path, headers=headers, params={'cursor': 'bad'}).status_code == 422
    receipt = client.get(f"{BASE}/{item['id']}/documents/{document['id']}", headers=headers).json()['data']
    assert receipt['scan_status'] == 'not_scanned'  # Intake receipt is immutable; evidence belongs to the job.
    with admin_engine.connect() as db:
        events = db.execute(text("SELECT action FROM platform_audit_events WHERE actor_id=:id AND action LIKE 'textbooks.processing.%' ORDER BY created_at"), {'id': job['created_by']}).scalars().all()
    assert events == ['textbooks.processing.request', 'textbooks.processing.finished', 'textbooks.processing.request']
    assert worker.consume_one() == 'finished'


@pytest.mark.parametrize('change', [
    {'expected_revision': True}, {'expected_revision': 0}, {'expected_revision': 2.0},
    {'reason': ''}, {'reason': ' x'}, {'reason': 'x\n'}, {'reason': 'a\x7fb'},
    {'reason': 'x'*501}, {'operation_id': 'bad'}, {'extra': True},
])
def test_request_validation(client, received, change):
    headers, item, _, path = received
    assert client.post(path, headers=headers, json=request_body(item, **change)).status_code == 422


def test_intake_required_and_revision_conflict(client, approved, received):
    headers, item = approved
    document, _ = intent(client, headers, item)
    path = f"{BASE}/{item['id']}/documents/{document['id']}/processing-jobs"
    assert client.post(path, headers=headers, json=request_body(item)).json()['error']['code'] == 'document_not_received'
    headers, item, _, path = received
    assert client.post(path, headers=headers, json=request_body(item, expected_revision=item['revision']-1)).json()['error']['code'] == 'textbook_revision_conflict'


@pytest.mark.parametrize('when', ['before_claim', 'before_finish'])
@pytest.mark.parametrize('revocation', ['rights', 'session', 'permission'])
def test_revocation_blocks_and_does_not_save_text(client, received, worker, admin_engine, when, revocation):
    headers, item, _, path = received
    job, _ = submit(client, received)
    claimed = call(worker, 'claim') if when == 'before_finish' else None
    if revocation == 'rights':
        action(client, headers, item, 'withdraw')
    else:
        with admin_engine.begin() as db:
            if revocation == 'session':
                db.execute(text('UPDATE platform_sessions SET revoked_at=clock_timestamp() WHERE account_id=:id'), {'id': job['created_by']})
            else:
                db.execute(text("UPDATE platform_accounts SET role='operator' WHERE id=:id"), {'id': job['created_by']})
    result = call(worker, 'finish', job['id'], claimed['lease_token'], terminal()) if claimed else call(worker, 'claim')
    assert result['status'] == 'blocked'
    with admin_engine.connect() as db:
        row = db.execute(text('SELECT state,result FROM textbook_processing_jobs WHERE id=:id'), {'id': job['id']}).one()
    assert row.state == 'blocked' and row.result == {'code': 'authorization_changed'}
    if revocation == 'rights':
        assert client.get(f"{path}/{job['id']}/pages/1", headers=headers).status_code == 409


def test_revoked_preview_never_serves_completed_draft(client, received, worker):
    headers, item, _, path = received
    job, _ = submit(client, received)
    assert worker.consume_one() == 'finished'
    action(client, headers, item, 'withdraw')
    assert client.get(f"{path}/{job['id']}", headers=headers).status_code == 200
    assert client.get(f"{path}/{job['id']}/pages/1", headers=headers).json()['error']['code'] == 'draft_rights_unavailable'


def test_expired_lease_fences_old_worker_and_exhausts_attempts(client, received, worker, admin_engine):
    job, _ = submit(client, received)
    first = call(worker, 'claim')
    assert first['status'] == 'claimed'
    assert call(worker, 'claim')['status'] == 'idle'
    assert call(worker, 'finish', job['id'], str(uuid4()), terminal())['status'] == 'stale'
    expire(admin_engine, job['id'])
    assert call(worker, 'finish', job['id'], first['lease_token'], terminal())['status'] == 'stale'
    second = call(worker, 'claim')
    assert second['lease_token'] != first['lease_token']
    assert call(worker, 'finish', job['id'], first['lease_token'], terminal())['status'] == 'stale'
    expire(admin_engine, job['id'])
    assert call(worker, 'claim')['status'] == 'claimed'
    expire(admin_engine, job['id'])
    assert call(worker, 'claim')['status'] == 'blocked'
    with admin_engine.connect() as db:
        row = db.execute(text('SELECT attempts,result FROM textbook_processing_jobs WHERE id=:id'), {'id': job['id']}).one()
    assert row.attempts == 3 and row.result == {'code': 'attempts_exhausted'}


def test_concurrent_requests_and_claims_have_one_winner(app, client, received, worker):
    headers, item, _, path = received
    def create_one(_):
        with TestClient(app) as http:
            return http.post(path, headers=headers, json=request_body(item)).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(create_one, range(2))) == [201, 409]
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: call(worker, 'claim'), range(2)))
    assert sorted(row['status'] for row in claims) == ['claimed', 'idle']
    claimed = next(row for row in claims if row['status'] == 'claimed')
    assert call(worker, 'finish', claimed['id'], claimed['lease_token'], terminal())['status'] == 'finished'


@pytest.mark.parametrize('exception,status,code', [(ScanUnavailable, 'unavailable', 'scanner_unavailable'),
                                                 (UnsafeFile, 'rejected', 'scanner_rejected')])
def test_scanner_failure_is_persisted_without_private_details(client, received, worker, exception, status, code):
    headers, _, _, path = received
    job, _ = submit(client, received)
    class Failing:
        def scan(self, source):
            raise exception('private source content and connection details')
    worker.scanner = Failing()
    assert worker.consume_one() == 'finished'
    response = client.get(f"{path}/{job['id']}", headers=headers)
    value = response.json()['data']
    assert value['state'] == status and value['code'] == code and value['scan'] is None
    assert 'private source' not in response.text
    assert client.get(f"{path}/{job['id']}/pages/1", headers=headers).status_code == 404


def test_empty_pdf_is_no_text(client, approved, worker):
    headers, item = approved
    content = pdf_bytes(('', ''))
    document, _ = intent(client, headers, item, content=content)
    assert upload(client, headers, item, document, content=content).status_code == 201
    path = f"{BASE}/{item['id']}/documents/{document['id']}/processing-jobs"
    job, _ = submit(client, (headers, item, document, path))
    assert worker.consume_one() == 'finished'
    value = client.get(f"{path}/{job['id']}", headers=headers).json()['data']
    assert value['state'] == 'no_text' and value['pages_without_text'] == [1, 2]


def test_capability_acl_and_api_identity(client, received, database, worker, admin_engine):
    _, _, _, path = received
    assert client.get(path).status_code == 401
    assert client.get(path, headers={'Authorization': 'Bearer invalid'}).status_code == 401
    with pytest.raises(DBAPIError), worker.engine.begin() as db:
        db.execute(text('SELECT * FROM textbook_processing_jobs'))
    with admin_engine.connect() as db:
        role = database['env']['WIZZY_APP_ROLE']
        assert not db.execute(text("SELECT has_function_privilege(:role,'public.textbook_processing_worker(text,uuid,uuid,jsonb)','EXECUTE')"), {'role': role}).scalar_one()


def test_worker_missing_configuration_and_cli_errors_are_safe(app, monkeypatch, capsys):
    from wizzy import textbook_worker
    with pytest.raises(ValueError, match='ClamAV'):
        TextbookWorker(None, app.state.settings.model_copy(update={'clamav_host': None}))
    monkeypatch.setattr(textbook_worker, 'Settings', lambda: (_ for _ in ()).throw(RuntimeError('secret database password')))
    with pytest.raises(SystemExit, match='Textbook worker unavailable') as error:
        textbook_worker.main()
    assert 'secret' not in str(error.value) and capsys.readouterr().out == ''


def valid_draft():
    return {'status': 'draft_ready', 'code': None, 'scan': {'engine': 'test-only', 'database_revision': 1,
        'database_date': 'test-only', 'checked_at': '2026-10-26T00:00:00+00:00'},
        'draft': {'pages': [{'number': 1, 'text': '正文'}], 'page_count': 1, 'pages_without_text': [],
        'character_count': 2, 'method': 'embedded_text', 'requires_review': True, 'ocr_performed': False,
        'content_available': False}, 'content_available': False}


@pytest.mark.parametrize('path,value', [
    (['extra'], True), (['status'], None), (['content_available'], True), (['draft'], None),
    (['scan'], None), (['scan', 'database_revision'], True), (['scan', 'checked_at'], 'invalid time'),
    (['scan', 'checked_at'], 'infinity'), (['scan', 'extra'], 'private'),
    (['draft', 'character_count'], 1), (['draft', 'character_count'], True),
    (['draft', 'page_count'], 2), (['draft', 'page_count'], True), (['draft', 'pages_without_text'], [1]),
    (['draft', 'requires_review'], False), (['draft', 'ocr_performed'], True),
    (['draft', 'content_available'], True), (['draft', 'pages'], [{'number': 2, 'text': '正文'}]),
    (['draft', 'pages'], [{'number': 1, 'text': None}]), (['status'], 'no_text'),
])
def test_invalid_result_is_atomic(client, received, worker, admin_engine, path, value):
    job, _ = submit(client, received)
    claimed = call(worker, 'claim')
    payload = copy.deepcopy(valid_draft())
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert call(worker, 'finish', job['id'], claimed['lease_token'], payload)['status'] == 'invalid'
    with admin_engine.connect() as db:
        row = db.execute(text('SELECT state,result,lease_token FROM textbook_processing_jobs WHERE id=:id'), {'id': job['id']}).one()
        count = db.execute(text("SELECT count(*) FROM platform_audit_events WHERE request_id=:request"), {'request': 'processing-'+job['id']}).scalar_one()
    assert row.state == 'running' and row.result is None and str(row.lease_token) == claimed['lease_token'] and count == 0
    assert call(worker, 'finish', job['id'], claimed['lease_token'], valid_draft())['status'] == 'finished'
    assert call(worker, 'finish', job['id'], claimed['lease_token'], valid_draft())['status'] == 'stale'
