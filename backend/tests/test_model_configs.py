"""Simple model configs: encrypted keys, priority ordering and a real outbound connection test."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest
import test_platform_identity as identity_fixtures
from sqlalchemy import text

platform = identity_fixtures.platform
session = identity_fixtures.session

BASE = '/api/v1/admin/model-configs'
GOOD_KEY = 'sk-test-' + 'a1b2c3d4e5f6'


class FakeProvider(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.seen.append({'path': self.path, 'auth': self.headers.get('Authorization'), 'body': body})
        if self.path == '/redirect/chat/completions':
            self.send_response(307)
            self.send_header('Location', 'http://example.invalid/steal')
            self.end_headers()
            return
        if self.headers.get('Authorization') != f'Bearer {GOOD_KEY}':
            payload, status = {'error': {'message': f'Incorrect API key {self.headers.get("Authorization")}'}}, 401
        else:
            payload, status = {'choices': [{'message': {'content': 'p'}}]}, 200
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


@pytest.fixture
def provider():
    server = ThreadingHTTPServer(('127.0.0.1', 0), FakeProvider)
    server.seen = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def body(**overrides):
    return {'name': 'qwen-vl-' + uuid4().hex[:8], 'base_url': 'https://api.example.test/v1',
            'api_key': GOOD_KEY, 'priority': 10, **overrides}


def test_requires_platform_session(client, owner):
    for headers in ({}, owner['headers']):
        assert client.get(BASE, headers=headers).status_code == 401
        assert client.post(BASE, headers=headers, json=body()).status_code == 401


def test_crud_orders_by_priority_and_never_returns_key(client, platform, admin_engine):
    headers = session(client, platform)
    low = client.post(BASE, headers=headers, json=body(priority=20))
    assert low.status_code == 201, low.text
    high = client.post(BASE, headers=headers, json=body(priority=1, api_key=None)).json()['data']
    low = low.json()['data']
    assert low['api_key_configured'] is True and low['api_key_hint'] == '****e5f6'
    assert high['api_key_configured'] is False and high['api_key_hint'] is None
    listed = client.get(BASE, headers=headers)
    assert [item['id'] for item in listed.json()['data']['items']][:2] == [high['id'], low['id']]
    for response in (listed,):
        assert GOOD_KEY not in response.text and 'cipher' not in response.text
    with admin_engine.connect() as db:
        stored = db.execute(text('SELECT api_key_cipher FROM model_configs WHERE id=:id'), {'id': low['id']}).scalar_one()
    assert stored and GOOD_KEY not in stored

    kept = client.post(f'{BASE}/{low["id"]}', headers=headers,
                       json={'name': low['name'], 'base_url': low['base_url'], 'priority': 5, 'api_key': ''})
    assert kept.status_code == 200, kept.text
    assert kept.json()['data']['priority'] == 5 and kept.json()['data']['api_key_hint'] == '****e5f6'
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT api_key_cipher FROM model_configs WHERE id=:id'), {'id': low['id']}).scalar_one() == stored

    assert client.post(BASE, headers=headers, json=body(name=low['name'], base_url=low['base_url'])).status_code == 409
    assert client.post(f'{BASE}/{low["id"]}/delete', headers=headers).status_code == 200
    assert client.post(f'{BASE}/{low["id"]}/delete', headers=headers).status_code == 404
    with admin_engine.connect() as db:
        actions = db.execute(text("SELECT action FROM platform_audit_events WHERE actor_id=:id AND action LIKE 'model_configs.%'"),
                             {'id': platform['id']}).scalars().all()
    assert sorted(actions) == ['model_configs.create', 'model_configs.create', 'model_configs.delete', 'model_configs.update']


@pytest.mark.parametrize('overrides', [
    {'name': ''}, {'name': 'a\nb'}, {'base_url': 'ftp://example.test/v1'}, {'base_url': 'example.test'},
    {'priority': 0}, {'priority': 10000}, {'priority': '1'}, {'priority': True}, {'extra': 1},
])
def test_rejects_invalid_config(client, platform, overrides):
    headers = session(client, platform)
    response = client.post(BASE, headers=headers, json=body(**overrides))
    assert response.status_code == 422, response.text
    assert GOOD_KEY not in response.text


def test_connection_test_uses_stored_key_and_records_result(client, platform, provider):
    headers = session(client, platform)
    base_url = f'http://127.0.0.1:{provider.server_port}/v1'
    good = client.post(BASE, headers=headers, json=body(base_url=base_url)).json()['data']
    result = client.post(f'{BASE}/{good["id"]}/test', headers=headers)
    assert result.status_code == 200, result.text
    value = result.json()['data']
    assert value['ok'] is True and value['message'].startswith('连接成功：HTTP 200')
    assert value['config']['last_test_ok'] is True and value['config']['last_test_at']
    seen = provider.seen[-1]
    assert seen['path'] == '/v1/chat/completions' and seen['auth'] == f'Bearer {GOOD_KEY}'
    assert seen['body']['model'] == good['name'] and seen['body']['max_tokens'] == 1

    bad = client.post(BASE, headers=headers, json=body(base_url=base_url + '/chat/completions', api_key='sk-wrong-key-0000')).json()['data']
    failed = client.post(f'{BASE}/{bad["id"]}/test', headers=headers)
    assert failed.status_code == 200
    assert failed.json()['data']['ok'] is False and failed.json()['data']['message'].startswith('HTTP 401')
    assert 'sk-wrong-key-0000' not in failed.text
    assert provider.seen[-1]['path'] == '/v1/chat/completions'
    listed = client.get(BASE, headers=headers).json()['data']['items']
    assert next(item for item in listed if item['id'] == bad['id'])['last_test_ok'] is False


def test_connection_test_refuses_redirects_and_reports_unreachable(client, platform, provider):
    headers = session(client, platform)
    redirect = client.post(BASE, headers=headers, json=body(base_url=f'http://127.0.0.1:{provider.server_port}/redirect')).json()['data']
    value = client.post(f'{BASE}/{redirect["id"]}/test', headers=headers).json()['data']
    assert value['ok'] is False and value['message'].startswith('HTTP 307')
    closed = client.post(BASE, headers=headers, json=body(base_url='http://127.0.0.1:9/v1')).json()['data']
    value = client.post(f'{BASE}/{closed["id"]}/test', headers=headers).json()['data']
    assert value['ok'] is False and value['message'].startswith('无法连接')


def test_operator_reads_but_cannot_write_or_test(client, platform, admin_engine):
    headers = session(client, platform)
    item = client.post(BASE, headers=headers, json=body()).json()['data']
    with admin_engine.begin() as db:
        db.execute(text("UPDATE platform_accounts SET role='operator' WHERE id=:id"), {'id': platform['id']})
    assert client.get(BASE, headers=headers).status_code == 200
    assert client.post(BASE, headers=headers, json=body()).status_code == 403
    assert client.post(f'{BASE}/{item["id"]}', headers=headers, json=body()).status_code == 403
    assert client.post(f'{BASE}/{item["id"]}/delete', headers=headers).status_code == 403
    assert client.post(f'{BASE}/{item["id"]}/test', headers=headers).status_code == 403
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE actor_id=:id AND action='access.denied'"),
                          {'id': platform['id']}).scalar_one() == 4
