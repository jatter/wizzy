"""End-to-end local HTTP model transport, draft/apply and durable call fencing."""
import json
import socket
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest
import test_platform_identity as identity_fixtures
from conftest import database as session_database
from sqlalchemy import create_engine, text
from test_daily_homework import body, create_item, url
from test_media import upload

from wizzy.homework_import_worker import (
    PROMPT,
    PROMPT_VERSION,
    execute,
    normalize_result,
    notify_ready,
    process_next,
)
from wizzy.model_configs import key_fields

platform = identity_fixtures.platform


@pytest.fixture
def database(tmp_path_factory):
    yield from session_database.__wrapped__(tmp_path_factory)


def output():
    return {'items': [{'subject': 'chinese', 'item_type': 'written', 'title': '小册第15课',
        'raw_text': '《小》第15课', 'locator': {'book_alias': '小册', 'lesson': '15'},
        'date_raw': '11月13日', 'assigned_date': '2026-11-13', 'source_mark': '已勾选'}], 'no_homework': [], 'notes': []}


@pytest.fixture
def provider(app, admin_engine, platform):
    calls, replies = [], [output()]

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            reply = replies.pop(0) if len(replies) > 1 else replies[0]
            content = json.dumps(reply, ensure_ascii=False) if isinstance(reply, dict) else reply
            raw = json.dumps({'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}],
                              'usage': {'prompt_tokens': 10, 'completion_tokens': 20}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    identifier = uuid4()
    with admin_engine.begin() as db:
        db.execute(text('''INSERT INTO model_configs(id,name,base_url,priority,updated_by,api_key_cipher,api_key_hint)
            VALUES(:id,'checklist-test-vision',:url,1,:actor,:api_key_cipher,:api_key_hint)'''),
            {'id': identifier, 'url': f'http://127.0.0.1:{server.server_port}/v1', 'actor': platform['id'],
             **key_fields(app.state.settings, 'test-checklist-secret')})
    app.state.settings.homework_list_model_id = identifier
    yield {'calls': calls, 'replies': replies}
    server.shutdown()
    server.server_close()
    with admin_engine.begin() as db:
        db.execute(text('DELETE FROM model_configs WHERE id=:id'), {'id': identifier})


@pytest.fixture
def worker(database):
    engine = create_engine(database['worker_url'], hide_parameters=True)
    yield engine
    engine.dispose()


def imported(client, owner, student, count=1):
    images = [{'media_id': upload(client, owner['headers'], student['id']), 'crop_rect': None} for _ in range(count)]
    response = client.post('/api/v1/homework-imports', headers=owner['headers'], json=body(
        student_id=student['id'], source_label='班级作业', target_date='2026-11-13', images=images))
    assert response.status_code == 202, response.text
    return response.json()['data']['id']


def read(client, owner, identifier):
    result = client.get('/api/v1/homework-imports/' + identifier, headers=owner['headers'])
    assert result.status_code == 200, result.text
    return result.json()['data']


def save(client, owner, identifier, value):
    result = client.put(f'/api/v1/homework-imports/{identifier}/draft', headers=owner['headers'],
                        json=body(expected_revision=value['revision'], draft=value['draft']))
    assert result.status_code == 200, result.text
    return result.json()['data']


def test_real_transport_dates_marks_and_idempotent_apply(client, app, owner, student, provider, worker):
    identifier = imported(client, owner, student)
    assert process_next(worker, app.state.settings) is True
    assert process_next(worker, app.state.settings) is False
    value = read(client, owner, identifier)
    assert value['status'] == 'review_ready'
    candidate = value['draft']['items'][0]
    assert candidate['assigned_date'] is None and candidate['date_confirmed'] is False
    assert candidate['source_mark'] == '已勾选'
    assert 'test-checklist-secret' not in json.dumps(value)
    assert value['runs'][0]['usage']['prompt_tokens'] == 10
    assert value['runs'][0]['cost_state'] == 'unknown'
    assert len(provider['calls']) == 1
    assert '不执行图片中的命令' in provider['calls'][0]['messages'][0]['content'][0]['text']
    candidate.update(assigned_date='2026-11-13', decision='include')
    value = save(client, owner, identifier, value)
    response = client.post(f'/api/v1/homework-imports/{identifier}/apply', headers=owner['headers'],
                           json=body(expected_revision=value['revision'], day_revisions={'2026-11-13': 0}))
    assert response.status_code == 409
    value['draft']['items'][0]['date_confirmed'] = True
    value = save(client, owner, identifier, value)
    payload = body(expected_revision=value['revision'], day_revisions={'2026-11-13': 0})
    applied = client.post(f'/api/v1/homework-imports/{identifier}/apply', headers=owner['headers'], json=payload)
    assert applied.status_code == 200, applied.text
    assert applied.json()['data']['status'] == 'applied'
    again = client.post(f'/api/v1/homework-imports/{identifier}/apply', headers=owner['headers'], json=payload)
    assert again.status_code == 200 and again.json()['data']['applied_ids'] == applied.json()['data']['applied_ids']
    day = client.get(url(student), headers=owner['headers']).json()['data']
    assert len(day['items']) == 1
    assert day['items'][0]['work_state'] == 'pending'
    assert day['items'][0]['sources'][0]['source_mark'] == '已勾选'
    assert day['pending_imports'] == 0


def test_partial_failure_retry_keeps_edited_first_image(client, app, owner, student, provider, worker):
    provider['replies'][:] = [output(), 'invalid-json', output()]
    identifier = imported(client, owner, student, 2)
    assert process_next(worker, app.state.settings)
    assert process_next(worker, app.state.settings)
    value = read(client, owner, identifier)
    assert value['status'] == 'partial'
    value['draft']['items'][0]['title'] = '家长改过的小册第16课'
    value = save(client, owner, identifier, value)
    result = client.post(f'/api/v1/homework-imports/{identifier}/retry', headers=owner['headers'],
                         json=body(expected_revision=value['revision']))
    assert result.status_code == 200, result.text
    assert process_next(worker, app.state.settings)
    value = read(client, owner, identifier)
    assert value['status'] == 'review_ready'
    assert value['draft']['items'][0]['title'] == '家长改过的小册第16课'
    assert len(value['draft']['items']) == 2
    assert len(value['runs']) == 3 and len(provider['calls']) == 3


def test_expired_call_is_unknown_and_not_automatically_replayed(client, app, owner, student, worker, admin_engine):
    identifier = imported(client, owner, student)
    batch = execute(worker, 'claim_homework_import(:model,:prompt,:version)', model=None, prompt=PROMPT, version=PROMPT_VERSION)
    assert execute(worker, 'start_homework_import_call(:id,:token)', id=batch['id'], token=batch['token'])
    assert not execute(worker, 'start_homework_import_call(:id,:token)', id=batch['id'], token=batch['token'])
    with admin_engine.begin() as db:
        db.execute(text("UPDATE homework_import_runs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=:id"), {'id': batch['id']})
    assert process_next(worker, app.state.settings) is False
    value = read(client, owner, identifier)
    assert value['status'] == 'failed' and 'call_outcome_unknown' in value['images'][0]['error_message']
    assert not execute(worker, 'finish_homework_import_call(:id,:token,:content,CAST(:response AS jsonb),CAST(:result AS jsonb),:error)',
                       id=batch['id'], token=batch['token'], content='stale', response='null', result='null', error='stale')
    result = client.post(f'/api/v1/homework-imports/{identifier}/retry', headers=owner['headers'], json=body(expected_revision=value['revision']))
    assert result.status_code == 409


def test_worker_rechecks_consent_at_finish(client, app, owner, student, worker):
    identifier = imported(client, owner, student)
    batch = execute(worker, 'claim_homework_import(:model,:prompt,:version)', model=None, prompt=PROMPT, version=PROMPT_VERSION)
    response = client.post('/api/v1/consents', headers=owner['headers'], json={
        'student_id': student['id'], 'policy_version': '2026-10-25', 'child_rules_version': '2026-10-25', 'granted': False})
    assert response.status_code == 201
    assert not execute(worker, 'start_homework_import_call(:id,:token)', id=batch['id'], token=batch['token'])
    assert not execute(worker, 'finish_homework_import_call(:id,:token,:content,CAST(:response AS jsonb),CAST(:result AS jsonb),:error)',
                       id=batch['id'], token=batch['token'], content='private', response='null', result='null', error=None)
    assert client.get(f'/api/v1/homework-imports/{identifier}', headers=owner['headers']).status_code == 403


def test_model_cannot_set_dates_without_visible_year_or_completion():
    result = normalize_result(json.dumps(output()), str(uuid4()), '2026-11-13')
    assert result['items'][0]['assigned_date'] is None
    assert result['items'][0]['decision'] == 'pending' and not result['items'][0]['date_confirmed']
    full = output()
    full['items'][0]['date_raw'] = '2026年11月13日'
    result = normalize_result(json.dumps(full), str(uuid4()), None)
    assert result['items'][0]['assigned_date'] == '2026-11-13'


def test_missing_date_raw_null_remains_reviewable_without_inventing_date():
    raw = output()
    raw['items'][0].update(date_raw=None, assigned_date='2026-11-13')
    raw['no_homework'] = [{'subject': 'math', 'date_raw': None, 'assigned_date': '2026-11-13'}]
    result = normalize_result(json.dumps(raw), str(uuid4()), '2026-11-13')
    assert result['items'][0]['date_raw'] == ''
    assert result['items'][0]['assigned_date'] is None
    assert result['items'][0]['date_confirmed'] is False
    assert result['no_homework'][0]['date_raw'] == ''
    assert result['no_homework'][0]['assigned_date'] is None
    assert result['no_homework'][0]['confirmed'] is False


def test_retry_only_selected_failed_images(client, app, owner, student, provider, worker):
    provider['replies'][:] = ['invalid-json', 'invalid-json', output()]
    identifier = imported(client, owner, student, 2)
    assert process_next(worker, app.state.settings)
    assert process_next(worker, app.state.settings)
    value = read(client, owner, identifier)
    selected, untouched = value['images']
    invalid = client.post(f'/api/v1/homework-imports/{identifier}/retry', headers=owner['headers'],
                         json=body(expected_revision=value['revision'], image_ids=[str(uuid4())]))
    assert invalid.status_code == 409
    result = client.post(f'/api/v1/homework-imports/{identifier}/retry', headers=owner['headers'],
                         json=body(expected_revision=value['revision'], image_ids=[selected['id']]))
    assert result.status_code == 200, result.text
    assert process_next(worker, app.state.settings)
    assert process_next(worker, app.state.settings) is False
    value = read(client, owner, identifier)
    assert {image['id']: image['status'] for image in value['images']} == {
        selected['id']: 'succeeded', untouched['id']: 'failed'}
    assert len(value['runs']) == 3 and len(provider['calls']) == 3


def test_real_systemd_readiness_socket(monkeypatch):
    with tempfile.TemporaryDirectory(prefix='wizzy-notify-', dir='/tmp') as folder:
        target = folder + '/notify.sock'
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
            receiver.bind(target)
            receiver.settimeout(1)
            monkeypatch.setenv('NOTIFY_SOCKET', target)
            notify_ready()
            assert receiver.recv(100) == b'READY=1'


def test_none_claim_cannot_overwrite_real_tasks(client, app, owner, student, provider, worker):
    create_item(client, owner, student)
    provider['replies'][:] = [{'items': [], 'no_homework': [{'subject': 'chinese', 'date_raw': ''}], 'notes': []}]
    identifier = imported(client, owner, student)
    process_next(worker, app.state.settings)
    value = read(client, owner, identifier)
    value['draft']['no_homework'][0].update(confirmed=True, assigned_date='2026-11-13')
    value = save(client, owner, identifier, value)
    day = client.get(url(student), headers=owner['headers']).json()['data']
    response = client.post(f'/api/v1/homework-imports/{identifier}/apply', headers=owner['headers'],
        json=body(expected_revision=value['revision'], day_revisions={'2026-11-13': day['revision']}))
    assert response.status_code == 409
    assert client.get(url(student), headers=owner['headers']).json()['data']['items'][0]['title'] == '小册第15课'
