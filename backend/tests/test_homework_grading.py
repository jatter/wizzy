"""Real database and HTTP boundary for configured multi-image homework grading."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from uuid import uuid4

import pytest
import test_platform_identity as identity_fixtures
from conftest import database as session_database
from pypdf import PdfReader
from sqlalchemy import create_engine, text
from test_media import upload
from test_submissions import payload, submit

from wizzy.homework_grading import process_next
from wizzy.model_configs import key_fields

platform = identity_fixtures.platform


@pytest.fixture
def database(tmp_path_factory):
    yield from session_database.__wrapped__(tmp_path_factory)


@pytest.fixture
def provider(app, admin_engine, platform):
    calls = []
    contents = []
    finish_reasons = ['stop', 'stop']
    replies = ["第1张的第1题错误，正确答案为 42。", "第1张第1题：错误；第2张第1题：正确。"]
    transport = {'mode': 'json'}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(body)
            reply = replies[min(len(calls) - 1, len(replies) - 1)]
            if reply is None:
                self.send_response(502)
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"private-provider-error"}}')
                return
            count = sum(item['type'] == 'image_url' for item in body['messages'][-1]['content'])
            reply = json.dumps({'pages': [{'position': i, 'subject': ['math', 'english', 'chinese'][(i-1) % 3],
                'note': '', 'questions': [{'number': '第1题', 'verdict': 'incorrect', 'student_answer': '41',
                'reasoning': reply, 'steps': ['40+2=42'], 'answer': '42'}]} for i in range(1, count+1)]}, ensure_ascii=False)
            contents.append(reply)
            finish = finish_reasons[min(len(calls) - 1, 1)]
            chat_id = f'chat-{len(calls)}'
            usage = {'prompt_tokens': 12, 'completion_tokens': 8, 'note': 'test-provider-secret'}
            if 'raw' in transport:
                raw = transport['raw']
                content_type = 'text/event-stream'
            elif transport['mode'] == 'sse':
                cut = max(1, len(reply) // 2)
                events = [
                    {'id': chat_id, 'choices': [{'delta': {'content': reply[:cut], 'reasoning_content': 'secret-thought'}, 'finish_reason': None}]},
                    {'id': chat_id, 'choices': [{'delta': {'content': reply[cut:]}, 'finish_reason': None}]},
                    {'id': chat_id, 'choices': [{'delta': {}, 'finish_reason': finish}], 'usage': usage},
                ]
                raw = b''.join(b'data: ' + json.dumps(event, ensure_ascii=False).encode() + b'\n\n' for event in events) + b'data: [DONE]\n\n'
                content_type = 'text/event-stream'
            else:
                raw = json.dumps({'id': chat_id, 'choices': [{'message': {'content': reply}, 'finish_reason': finish}],
                                  'usage': usage}).encode()
                content_type = 'application/json'
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    model_id = uuid4()
    fallback_id = uuid4()
    with admin_engine.begin() as db:
        db.execute(text('''INSERT INTO model_configs(id,name,base_url,priority,updated_by,api_key_cipher,api_key_hint)
            VALUES(:id,'vision-first',:url,1,:actor,:api_key_cipher,:api_key_hint)'''),
                   {'id': model_id, 'url': f'http://127.0.0.1:{server.server_port}/v1',
                    'actor': platform['id'], **key_fields(app.state.settings, 'test-provider-secret')})
        db.execute(text('''INSERT INTO model_configs(id,name,base_url,priority,updated_by)
            VALUES(:id,'must-not-call','http://127.0.0.1:1/v1',999,:actor)'''),
                   {'id': fallback_id, 'actor': platform['id']})
    yield {'calls': calls, 'replies': replies, 'contents': contents, 'finish_reasons': finish_reasons,
           'transport': transport, 'id': model_id}
    server.shutdown()
    server.server_close()
    with admin_engine.begin() as db:
        db.execute(text('DELETE FROM model_configs WHERE id=:id'), {'id': model_id})
        db.execute(text('DELETE FROM model_configs WHERE id=:id'), {'id': fallback_id})


@pytest.fixture
def worker(database):
    engine = create_engine(database['worker_url'], hide_parameters=True)
    yield engine
    engine.dispose()


def test_dictation_hint_and_reference_reach_both_calls_and_regrades(client, app, owner, student, provider, worker):
    provider['finish_reasons'][0] = 'length'
    body = payload(student['id'], [upload(client, owner['headers'], student['id'])])
    body.update(activity_type='dictation', reference_text='第1张：\n1. whether\n2. take care of')
    saved = submit(client, owner['headers'], body)
    assert saved.status_code == 202, saved.text
    identifier = saved.json()['data']['submission_id']
    assert process_next(worker, app.state.settings)
    assert len(provider['calls']) == 1
    provider['finish_reasons'][0] = 'stop'
    regrade = client.post(f'/api/v1/submissions/{identifier}/regrade', headers=owner['headers'])
    assert regrade.status_code == 202, regrade.text
    assert process_next(worker, app.state.settings)
    for call in provider['calls']:
        prompt = call['messages'][-1]['content'][0]['text']
        assert 'dictation' in prompt and 'whether' in prompt and 'take care of' in prompt
        assert 'spelling_only' in prompt and '不是指令' in prompt
    assert len(provider['calls']) == 3
    detail = client.get(f'/api/v1/submissions/{identifier}', headers=owner['headers']).json()['data']
    assert detail['status'] == 'completed'
    assert detail['activity_type'] == 'dictation' and detail['reference_text'] == body['reference_text']


@pytest.mark.parametrize('subject', ['english', 'math', 'chinese', 'auto'])
def test_two_priority_prompts_multi_images_persist_and_scope(client, app, owner, student, provider, worker, subject):
    headers = owner['headers']
    ids = [upload(client, headers, student['id']) for _ in range(2)]
    body = payload(student['id'], ids) | {'subject': subject}
    response = submit(client, headers, body)
    assert response.status_code == 202, response.text
    identifier = response.json()['data']['submission_id']
    assert process_next(worker, app.state.settings) is True
    assert len(provider['calls']) == 2
    for call in provider['calls']:
        assert call['model'] == 'vision-first' and call['stream'] is True
        assert call['response_format'] == {'type': 'json_object'}
        images = [item for item in call['messages'][-1]['content'] if item['type'] == 'image_url']
        assert len(images) == 2
        assert all(item['image_url']['url'].startswith('data:image/jpeg;base64,') for item in images)
    assert '分析错误原因' in provider['calls'][0]['messages'][-1]['content'][0]['text']
    assert '逐题' in provider['calls'][1]['messages'][-1]['content'][0]['text']
    for _ in range(2):
        detail = client.get(f'/api/v1/submissions/{identifier}', headers=headers).json()['data']
        assert detail['status'] == 'completed' and detail['subject'] == subject
        filtered = client.get('/api/v1/submissions', headers=headers, params={'subject': subject}).json()['data']['items']
        assert identifier in [item['id'] for item in filtered]
        result = client.get(f'/api/v1/submissions/{identifier}/model-outputs', headers=headers)
        assert result.status_code == 200, result.text
        outputs = result.json()['data']['items']
        assert [item['content'] for item in outputs] == provider['contents']
        assert [item['prompt_priority'] for item in outputs] == [10, 20]
        assert all(item['model_name'] == 'vision-first' and item['status'] == 'succeeded' for item in outputs)
        assert [page['position'] for page in outputs[0]['page_results']] == [1, 2]
        assert [page['subject'] for page in outputs[0]['page_results']] == ['math', 'english']
        assert outputs[0]['page_results'][0]['counts'] == {'correct': 0, 'incorrect': 1, 'unanswered': 0, 'unclear': 0}
        assert outputs[0]['page_results'][0]['questions'][0]['steps'] == ['40+2=42']
        assert 'test-provider-secret' not in result.text
    assert process_next(worker, app.state.settings) is False
    quota = client.get('/api/v1/quota', headers=headers).json()['data']
    assert quota['reserved_pages'] == 0 and quota['used_pages'] == 2
    other = client.post('/api/v1/tenants', headers=headers, json={'name': '隔离家庭'}).json()['data']
    assert client.get(f'/api/v1/submissions/{identifier}/model-outputs',
                      headers={'Authorization': 'Bearer ' + other['access_token']}).status_code == 404


def test_second_call_failure_keeps_first_result_and_releases_quota(client, app, owner, student, provider, worker):
    provider['replies'][1] = None
    receipt = submit(client, owner['headers'], payload(student['id'], [upload(client, owner['headers'], student['id'])])).json()['data']
    process_next(worker, app.state.settings)
    result = client.get(f"/api/v1/submissions/{receipt['submission_id']}/model-outputs", headers=owner['headers'])
    items = result.json()['data']['items']
    assert [item['status'] for item in items] == ['succeeded', 'failed']
    assert items[0]['content'] == provider['contents'][0]
    assert items[1]['error_message'] and 'private-provider-error' not in result.text
    detail = client.get(f"/api/v1/submissions/{receipt['submission_id']}", headers=owner['headers']).json()['data']
    assert detail['status'] == 'partial'
    assert process_next(worker, app.state.settings) is False


def test_disabled_prompt_skipped_and_configuration_is_snapshotted(client, app, owner, student, provider, worker, admin_engine):
    with admin_engine.begin() as db:
        db.execute(text('UPDATE grading_prompts SET enabled=false WHERE priority=10'))
    try:
        receipt = submit(client, owner['headers'], payload(student['id'], [upload(client, owner['headers'], student['id'])])).json()['data']
        process_next(worker, app.state.settings)
        items = client.get(f"/api/v1/submissions/{receipt['submission_id']}/model-outputs", headers=owner['headers']).json()['data']['items']
        assert [item['prompt_priority'] for item in items] == [20, 30]
    finally:
        with admin_engine.begin() as db:
            db.execute(text('UPDATE grading_prompts SET enabled=true WHERE priority=10'))


def test_crashed_claim_is_failed_without_repeating_paid_calls(client, app, owner, student, provider, worker, admin_engine):
    receipt = submit(client, owner['headers'], payload(student['id'], [upload(client, owner['headers'], student['id'])])).json()['data']
    with worker.begin() as db:
        claimed = db.execute(text('SELECT public.claim_homework_grading()')).scalar_one()
        assert claimed['submission_id'] == receipt['submission_id']
    with admin_engine.begin() as db:
        db.execute(text("UPDATE homework_grading_batches SET lease_until=now()-interval '1 second' WHERE submission_id=:id"), {'id': receipt['submission_id']})
    assert process_next(worker, app.state.settings) is False
    assert provider['calls'] == []
    state = client.get(f"/api/v1/jobs/{receipt['job_id']}", headers=owner['headers']).json()['data']
    assert state['status'] == 'failed'


def test_invalid_structured_answer_preserves_raw_response_without_fake_counts(client, app, owner, student, provider, worker, admin_engine, monkeypatch):
    raw = {'choices': [{'message': {'content': '第1题：正确'}}]}
    monkeypatch.setattr('wizzy.homework_grading.invoke', lambda *_: ('第1题：正确', raw))
    receipt = submit(client, owner['headers'], payload(student['id'], [upload(client, owner['headers'], student['id'])])).json()['data']
    process_next(worker, app.state.settings)
    items = client.get(f"/api/v1/submissions/{receipt['submission_id']}/model-outputs", headers=owner['headers']).json()['data']['items']
    assert len(items) == 1 and items[0]['status'] == 'failed'
    assert items[0]['content'] == '第1题：正确' and items[0]['page_results'] == []
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT response FROM homework_model_outputs WHERE submission_id=:id'), {'id': receipt['submission_id']}).scalar_one() == raw


def test_three_access_failures_regrade_onto_the_second_priority_model(client, app, owner, student, provider, worker, admin_engine):
    provider['replies'][:] = [None, None, None, 'kept', 'kept']
    with admin_engine.begin() as db:
        url = db.execute(text("SELECT base_url FROM model_configs WHERE name='vision-first'")).scalar_one()
        db.execute(text("UPDATE model_configs SET name='vision-second',base_url=:url WHERE name='must-not-call'"), {'url': url})
    headers = owner['headers']
    receipt = submit(client, headers, payload(student['id'], [upload(client, headers, student['id'])])).json()['data']
    identifier = receipt['submission_id']
    assert process_next(worker, app.state.settings) is True
    failed = client.get(f'/api/v1/submissions/{identifier}/model-outputs', headers=headers).json()['data']['items']
    assert failed[0]['status'] == 'failed' and '模型调用失败（HTTP 502）' in failed[0]['error_message']
    assert failed[0]['model_name'] == 'vision-first'
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT primary_call_failures FROM homework_grading_batches WHERE submission_id=:id'), {'id': identifier}).scalar_one() == 1
    first = client.post(f'/api/v1/submissions/{identifier}/regrade', headers=headers)
    assert first.status_code == 202, first.text
    assert client.post(f'/api/v1/submissions/{identifier}/regrade', headers=headers).status_code == 409
    assert process_next(worker, app.state.settings) is True
    assert client.post(f'/api/v1/submissions/{identifier}/regrade', headers=headers).status_code == 202
    assert process_next(worker, app.state.settings) is True
    assert [call['model'] for call in provider['calls']] == ['vision-first', 'vision-first', 'vision-first', 'vision-second', 'vision-second']
    items = client.get(f'/api/v1/submissions/{identifier}/model-outputs', headers=headers).json()['data']['items']
    assert [item['model_name'] for item in items] == ['vision-second', 'vision-second']
    assert [item['status'] for item in items] == ['succeeded', 'succeeded']
    detail = client.get(f'/api/v1/submissions/{identifier}', headers=headers).json()['data']
    assert detail['status'] == 'completed'
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT primary_call_failures FROM homework_grading_batches WHERE submission_id=:id'), {'id': identifier}).scalar_one() == 3
    quota = client.get('/api/v1/quota', headers=headers).json()['data']
    assert quota['reserved_pages'] == 0 and quota['used_pages'] == 1
    other = client.post('/api/v1/tenants', headers=headers, json={'name': '另一家庭'}).json()['data']
    assert client.post(f'/api/v1/submissions/{identifier}/regrade', headers={'Authorization': 'Bearer ' + other['access_token']}).status_code == 404


def test_streamed_completion_drops_reasoning_and_redacts_secret(client, app, owner, student, provider, worker, admin_engine):
    provider['transport']['mode'] = 'sse'
    receipt = submit(client, owner['headers'], payload(student['id'], [upload(client, owner['headers'], student['id'])])).json()['data']
    assert process_next(worker, app.state.settings) is True
    assert [call['stream'] for call in provider['calls']] == [True, True]
    items = client.get(f"/api/v1/submissions/{receipt['submission_id']}/model-outputs", headers=owner['headers']).json()['data']['items']
    assert [item['status'] for item in items] == ['succeeded', 'succeeded']
    assert [item['content'] for item in items] == provider['contents']
    assert 'secret-thought' not in json.dumps(items, ensure_ascii=False)
    with admin_engine.connect() as db:
        stored = db.execute(text('SELECT response FROM homework_model_outputs WHERE submission_id=:id ORDER BY sequence'),
                            {'id': receipt['submission_id']}).scalars().all()
    assert [item['choices'][0]['finish_reason'] for item in stored] == ['stop', 'stop']
    assert all(item['usage']['note'] == '****' for item in stored)
    assert 'test-provider-secret' not in json.dumps(stored)


def test_length_limited_response_is_saved_but_not_shown_as_complete(client, app, owner, student, provider, worker, admin_engine):
    provider['finish_reasons'][0] = 'length'
    receipt = submit(client, owner['headers'], payload(student['id'], [upload(client, owner['headers'], student['id'])])).json()['data']
    process_next(worker, app.state.settings)
    items = client.get(f"/api/v1/submissions/{receipt['submission_id']}/model-outputs", headers=owner['headers']).json()['data']['items']
    assert len(items) == 1 and items[0]['status'] == 'failed'
    assert items[0]['content'] == provider['contents'][0]
    assert '长度上限' in items[0]['error_message']
    with admin_engine.connect() as db:
        response = db.execute(text('SELECT response FROM homework_model_outputs WHERE submission_id=:id'), {'id': receipt['submission_id']}).scalar_one()
        assert response['choices'][0]['finish_reason'] == 'length'


@pytest.mark.parametrize('raw,diagnostic', [
    (b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\ndata: {broken test-provider-secret\n\n', 'JSONDecodeError'),
    (b'data: {"error":{"message":"test-provider-secret upstream failure"}}\n\n', 'ValueError'),
    (b'{"error":{"message":"test-provider-secret upstream failure"}}', 'KeyError'),
])
def test_malformed_provider_response_is_saved_privately_without_false_results(
        client, app, owner, student, provider, worker, admin_engine, raw, diagnostic):
    provider['transport']['raw'] = raw
    headers = owner['headers']
    receipt = submit(client, headers, payload(student['id'], [upload(client, headers, student['id'])])).json()['data']
    assert process_next(worker, app.state.settings)
    result = client.get(f"/api/v1/submissions/{receipt['submission_id']}/model-outputs", headers=headers)
    item, = result.json()['data']['items']
    assert item['status'] == 'failed' and item['page_results'] == [] and item['content'] == ''
    assert '重新批改' in item['error_message']
    assert len(provider['calls']) == 1
    with admin_engine.connect() as db:
        response = db.execute(text('SELECT response FROM homework_model_outputs WHERE submission_id=:id'),
                              {'id': receipt['submission_id']}).scalar_one()
    assert response['raw_response'] == raw.decode().replace('test-provider-secret', '****')
    assert response['parse_error'] == diagnostic
    assert 'test-provider-secret' not in json.dumps(response)
    assert 'raw_response' not in result.text and 'upstream failure' not in result.text


def test_dictation_results_preserve_words_without_counting_spelling_as_mastery(client, app, owner, student, provider, worker, monkeypatch):
    question = {'number': '左列第1项', 'item_type': 'word', 'grading_basis': 'spelling_only',
                'verdict': 'incorrect', 'student_answer': 'applle', 'reasoning': '多写了一个l，仅检查拼写',
                'steps': [], 'answer': 'apple'}
    questions = [question,
                 {**question, 'number': '右列第1项', 'verdict': 'correct', 'student_answer': 'weather', 'answer': 'weather'},
                 {**question, 'number': '右列第2项', 'grading_basis': 'none', 'verdict': 'unclear', 'student_answer': 'w?rd', 'answer': ''}]

    def fake_invoke(*_args):
        content = json.dumps({'pages': [{'position': 1, 'subject': 'english', 'note': '仅检查拼写', 'questions': questions}]})
        return content, {'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}]}

    monkeypatch.setattr('wizzy.homework_grading.invoke', fake_invoke)
    headers = owner['headers']
    saved = submit(client, headers, payload(student['id'], [upload(client, headers, student['id'])])).json()['data']
    assert process_next(worker, app.state.settings)
    outputs = client.get(f"/api/v1/submissions/{saved['submission_id']}/model-outputs", headers=headers).json()['data']['items']
    assert all(item['status'] == 'succeeded' for item in outputs)
    page = outputs[-1]['page_results'][0]
    assert page['counts'] == {'correct': 1, 'incorrect': 1, 'unanswered': 0, 'unclear': 1}
    assert page['questions'][0]['student_answer'] == 'applle'
    assert page['questions'][1]['grading_basis'] == 'spelling_only'
    questions[:] = [{**question, 'verdict': 'correct', 'student_answer': 'apple'}]
    submit(client, headers, payload(student['id'], [upload(client, headers, student['id'])]))
    assert process_next(worker, app.state.settings)
    bank = client.get(f"/api/v1/students/{student['id']}/mistake-bank", headers=headers).json()['data']['items']
    assert len(bank) == 1
    assert bank[0]['incorrect_count'] == 1 and bank[0]['correct_count'] == 0


def test_mistake_bank_counts_repeats_and_prints_separate_sheets(client, app, owner, student, provider, worker, admin_engine, monkeypatch):
    def body(answer, verdict):
        return {'pages': [{'position': 1, 'subject': 'math', 'note': '', 'questions': [
            {'number': '第6题', 'verdict': verdict, 'student_answer': answer, 'reasoning': '鸡兔同笼，头共三十五，脚共九十四。', 'steps': ['设鸡为x'], 'answer': '公鸡10只'},
            {'number': '第7题', 'verdict': 'unanswered', 'student_answer': '', 'reasoning': '', 'steps': [], 'answer': ''},
        ]}]}
    scripts = [body('公鸡13只', 'incorrect'), body('公鸡13只', 'incorrect'), body('公鸡10只', 'correct')]
    seen = []

    def fake_invoke(*_args):
        current = scripts[min(len(seen) // 2, len(scripts) - 1)]
        seen.append(1)
        content = json.dumps(current, ensure_ascii=False)
        return content, {'choices': [{'message': {'content': content}}]}

    monkeypatch.setattr('wizzy.homework_grading.invoke', fake_invoke)
    headers = owner['headers']
    for _ in range(3):
        submit(client, headers, payload(student['id'], [upload(client, headers, student['id'])]))
        assert process_next(worker, app.state.settings) is True
    bank = client.get(f"/api/v1/students/{student['id']}/mistake-bank", headers=headers)
    assert bank.status_code == 200, bank.text
    items = bank.json()['data']['items']
    assert len(items) == 1
    assert (items[0]['incorrect_count'], items[0]['correct_count'], items[0]['repeated_count']) == (2, 1, 2)
    assert '2 次' in items[0]['guardian_note']
    detail = client.get(f"/api/v1/students/{student['id']}/mistake-bank/{items[0]['id']}", headers=headers).json()['data']
    assert detail['summary'] == '前2次答案相同，第3次变了。'
    assert len(detail['timeline']) == 3
    with admin_engine.begin() as db:
        removed = db.execute(text('DELETE FROM mistake_attempts RETURNING id')).all()
        db.execute(text('DELETE FROM mistake_sync'))
        db.execute(text('DELETE FROM mistake_items'))
    assert removed
    again = client.get(f"/api/v1/students/{student['id']}/mistake-bank", headers=headers).json()['data']['items']
    assert len(again) == 1 and again[0]['incorrect_count'] == 2
    other = client.post('/api/v1/tenants', headers=headers, json={'name': '另一家庭'}).json()['data']
    assert client.get(f"/api/v1/students/{student['id']}/mistake-bank", headers={'Authorization': 'Bearer ' + other['access_token']}).status_code == 404
    code = client.post(f"/api/v1/students/{student['id']}/pairing-codes", headers=headers).json()['data']['code']
    child = {'Authorization': 'Bearer ' + client.post('/api/v1/child-sessions', json={'code': code, 'device_name': '学习平板'}).json()['data']['access_token']}
    child_items = client.get(f"/api/v1/students/{student['id']}/mistake-bank", headers=child).json()['data']['items']
    assert 'guardian_note' not in child_items[0]
    assert client.post(f"/api/v1/students/{student['id']}/mistake-bank/{again[0]['id']}/dismiss", headers=child).status_code == 403
    sheet = client.post(f"/api/v1/students/{student['id']}/mistake-sheets", headers=headers, json={'item_ids': [again[0]['id']], 'due_in_days': 3})
    assert sheet.status_code == 201, sheet.text
    assert sheet.json()['data']['state'] == '待再做'
    sheet_id = sheet.json()['data']['id']
    questions = client.get(f'/api/v1/mistake-sheets/{sheet_id}/pdf?kind=questions', headers=headers)
    answers = client.get(f'/api/v1/mistake-sheets/{sheet_id}/pdf?kind=answers', headers=headers)
    assert questions.status_code == 200 and questions.content.startswith(b'%PDF')
    assert answers.status_code == 200 and answers.content.startswith(b'%PDF') and answers.content != questions.content
    assert len(PdfReader(BytesIO(questions.content)).pages) >= 1
    assert len(PdfReader(BytesIO(answers.content)).pages) >= 1
    assert client.get(f'/api/v1/mistake-sheets/{sheet_id}/pdf?kind=answers', headers=child).status_code == 403
    assert client.post(f"/api/v1/students/{student['id']}/mistake-bank/{again[0]['id']}/dismiss", headers=headers).status_code == 200
    assert client.get(f"/api/v1/students/{student['id']}/mistake-bank", headers=headers).json()['data']['items'] == []
