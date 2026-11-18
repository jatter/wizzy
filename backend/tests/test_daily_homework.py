"""Daily checklist acceptance against isolated PostgreSQL and real API authorization."""
from uuid import uuid4

from test_media import upload


def body(**kwargs):
    return {'client_request_id': str(uuid4()), **kwargs}


def url(student, day='2026-11-13'):
    return f"/api/v1/students/{student['id']}/homework-days/{day}"


def create_item(client, owner, student):
    value = body(subject='chinese', item_type='written', title='小册第15课', raw_text='《小》第15课', locator={'book_alias': '小册', 'lesson': '15'})
    response = client.post(url(student) + '/items', headers=owner['headers'], json=value)
    assert response.status_code == 201, response.text
    return response.json()['data']


def test_manual_checklist_progress_verification_and_semantic_change(client, owner, student):
    response = client.get(url(student), headers=owner['headers'])
    assert response.status_code == 200, response.text
    assert response.json()['data']['summary']['all_verified'] is False
    item = create_item(client, owner, student)
    response = client.post(f"/api/v1/homework-items/{item['id']}/progress", headers=owner['headers'], json=body(expected_revision=item['revision'], state='completed'))
    assert response.status_code == 200, response.text
    done = response.json()['data']
    assert done['work_state'] == 'completed' and done['verification_state'] == 'unverified'
    response = client.post(f"/api/v1/homework-items/{item['id']}/verify", headers=owner['headers'], json=body(expected_revision=done['revision'], completed=True))
    assert response.status_code == 200, response.text
    verified = response.json()['data']
    assert verified['verification_state'] == 'guardian_confirmed'
    changed = client.put(f"/api/v1/homework-items/{item['id']}", headers=owner['headers'], json=body(expected_revision=verified['revision'], title='小册第16课'))
    assert changed.status_code == 200, changed.text
    assert changed.json()['data']['verification_state'] == 'unverified'
    assert len(changed.json()['data']['events']) >= 4


def test_request_replay_conflicting_payload_and_cas(client, owner, student):
    payload = body(subject='math', title='大册P66', item_type='written')
    first = client.post(url(student) + '/items', headers=owner['headers'], json=payload)
    assert first.status_code == 201, first.text
    replay = client.post(url(student) + '/items', headers=owner['headers'], json=payload)
    assert replay.status_code == 201 and replay.json()['data']['id'] == first.json()['data']['id']
    conflict = client.post(url(student) + '/items', headers=owner['headers'], json=payload | {'title': '大册P67'})
    assert conflict.status_code == 409
    item = first.json()['data']
    assert client.put(f"/api/v1/homework-items/{item['id']}", headers=owner['headers'], json=body(expected_revision=0, title='旧版本')).status_code == 409


def test_scope_no_homework_and_preparation_counts(client, owner, student):
    create_item(client, owner, student)
    day = client.get(url(student), headers=owner['headers']).json()['data']
    scope = body(expected_revision=day['revision'], scope_state='complete', subjects={'chinese': 'has_items', 'math': 'confirmed_none', 'english': 'confirmed_none', 'other': 'confirmed_none'})
    result = client.put(url(student) + '/scope', headers=owner['headers'], json=scope)
    assert result.status_code == 200, result.text
    assert result.json()['data']['scope_state'] == 'complete'
    none_day = client.get(url(student, '2026-11-14'), headers=owner['headers']).json()['data']
    result = client.put(url(student, '2026-11-14') + '/scope', headers=owner['headers'], json=body(expected_revision=none_day['revision'], scope_state='complete', subjects={s: 'confirmed_none' for s in ('chinese', 'math', 'english', 'other')}))
    assert result.status_code == 200, result.text
    assert result.json()['data']['summary']['no_homework'] is True
    assert result.json()['data']['summary']['all_verified'] is False


def test_other_family_cannot_read_item(client, owner, student):
    item = create_item(client, owner, student)
    other = client.post('/api/v1/tenants', headers=owner['headers'], json={'name': '另一个家庭'}).json()['data']
    response = client.get(f"/api/v1/homework-items/{item['id']}", headers={'Authorization': 'Bearer ' + other['access_token']})
    assert response.status_code == 404


def test_import_requires_real_image_and_persists_draft(client, owner, student):
    media_id = upload(client, owner['headers'], student['id'])
    payload = body(student_id=student['id'], source_kind='manual_image', source_label='六年级10班', target_date='2026-11-13', images=[{'media_id': media_id, 'crop_rect': None}])
    result = client.post('/api/v1/homework-imports', headers=owner['headers'], json=payload)
    assert result.status_code == 202, result.text
    identifier = result.json()['data']['id']
    detail = client.get(f'/api/v1/homework-imports/{identifier}', headers=owner['headers'])
    assert detail.status_code == 200, detail.text
    value = detail.json()['data']
    assert value['status'] == 'queued' and value['draft']['items'] == []
    assert value['images'][0]['media_id'] == media_id
    assert client.post('/api/v1/homework-imports', headers=owner['headers'], json=payload).json()['data']['id'] == identifier


def test_deleted_item_request_can_be_replayed(client, owner, student):
    item = create_item(client, owner, student)
    payload = body(expected_revision=item['revision'], deleted=True, reason='重复录入')
    first = client.put(f"/api/v1/homework-items/{item['id']}", headers=owner['headers'], json=payload)
    assert first.status_code == 200, first.text
    replay = client.put(f"/api/v1/homework-items/{item['id']}", headers=owner['headers'], json=payload)
    assert replay.status_code == 200 and replay.json()['data'] == first.json()['data']
    assert client.get(f"/api/v1/homework-items/{item['id']}", headers=owner['headers']).status_code == 404


def test_child_progress_is_allowed_but_content_and_verification_are_guardian_only(client, owner, student):
    item = create_item(client, owner, student)
    code = client.post(f"/api/v1/students/{student['id']}/pairing-codes", headers=owner['headers']).json()['data']['code']
    token = client.post('/api/v1/child-sessions', json={'code': code, 'device_name': '平板'}).json()['data']['access_token']
    child = {'Authorization': 'Bearer ' + token}
    response = client.post(f"/api/v1/homework-items/{item['id']}/progress", headers=child, json=body(expected_revision=item['revision'], state='completed'))
    assert response.status_code == 200, response.text
    assert response.json()['data']['completion_actor'] == 'child'
    assert client.post(f"/api/v1/homework-items/{item['id']}/verify", headers=child,
                       json=body(expected_revision=2, completed=True)).status_code == 403
    assert client.post(url(student) + '/items', headers=child, json=body(subject='math', title='伪造作业')).status_code == 403
    assert client.get('/api/v1/homework-imports', params={'student_id': student['id']}, headers=child).status_code == 403


def test_verified_summary_requires_scope_and_no_pending_imports(client, owner, student):
    item = create_item(client, owner, student)
    response = client.post(f"/api/v1/homework-items/{item['id']}/verify", headers=owner['headers'], json=body(expected_revision=1, completed=True))
    assert response.status_code == 200
    day = client.get(url(student), headers=owner['headers']).json()['data']
    assert day['summary']['verified'] == 1 and day['summary']['all_verified'] is False
    scope_body = body(expected_revision=day['revision'], scope_state='complete', subjects={
        'chinese': 'has_items', 'math': 'confirmed_none', 'english': 'confirmed_none', 'other': 'confirmed_none'})
    response = client.put(url(student) + '/scope', headers=owner['headers'], json=scope_body)
    assert response.status_code == 200 and response.json()['data']['summary']['all_verified'] is True
    media_id = upload(client, owner['headers'], student['id'])
    client.post('/api/v1/homework-imports', headers=owner['headers'], json=body(student_id=student['id'], target_date='2026-11-13', images=[{'media_id': media_id}]))
    day = client.get(url(student), headers=owner['headers']).json()['data']
    assert day['summary']['all_verified'] is False and day['scope_state'] == 'incomplete'


def test_preparation_exemptions_and_history_backlog(client, owner, student):
    item = create_item(client, owner, student)
    result = client.post(url(student) + '/items', headers=owner['headers'], json=body(subject='other', item_type='preparation', title='准备两本语文本'))
    assert result.status_code == 201, result.text
    day = client.get(url(student), headers=owner['headers']).json()['data']
    assert day['summary']['required'] == 1 and day['summary']['preparation_total'] == 1
    result = client.put(f"/api/v1/homework-items/{item['id']}", headers=owner['headers'], json=body(expected_revision=1, exempted=True, reason='老师取消'))
    assert result.status_code == 200, result.text
    day = client.get(url(student), headers=owner['headers']).json()['data']
    assert day['summary']['required'] == 0
    backlog = client.get(f"/api/v1/students/{student['id']}/homework-backlog", headers=owner['headers']).json()['data']['items']
    assert all(x['id'] != item['id'] for x in backlog)
