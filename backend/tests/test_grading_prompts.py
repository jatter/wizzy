"""Grading prompts: seeded homework instructions, priority and enablement."""
from uuid import uuid4

import pytest
import test_platform_identity as identity_fixtures
from sqlalchemy import text

platform = identity_fixtures.platform
session = identity_fixtures.session

BASE = '/api/v1/admin/grading-prompts'
SEED_NAMES = ('错题原因与正确答案', '逐题对错判定', '错因归类', '订正提示')


def body(**overrides):
    return {'name': '临时提示词-' + uuid4().hex[:8], 'content': '请只指出错误题，不要补充图片里没有的题目。',
            'priority': 50, 'enabled': True, **overrides}


def test_requires_platform_session(client, owner):
    for headers in ({}, owner['headers']):
        assert client.get(BASE, headers=headers).status_code == 401
        assert client.post(BASE, headers=headers, json=body()).status_code == 401


def test_seeds_homework_prompts_with_the_requested_wording(client, platform):
    headers = session(client, platform)
    listed = client.get(BASE, headers=headers)
    assert listed.status_code == 200, listed.text
    items = listed.json()['data']['items']
    seeded = [item for item in items if item['name'] in SEED_NAMES]
    assert [item['name'] for item in seeded] == list(SEED_NAMES)
    assert [item['priority'] for item in seeded] == [10, 20, 30, 40]
    assert all(item['enabled'] for item in seeded)
    answer = seeded[0]
    assert '分析错误原因' in answer['content']
    assert all('听写' in item['content'] and '参考' in item['content'] for item in seeded)
    assert items[0]['id'] == answer['id']


def test_create_update_orders_by_priority_and_toggles_enabled(client, platform, admin_engine):
    headers = session(client, platform)
    created = client.post(BASE, headers=headers, json=body(priority=1, content='先看第1张。\n再看第2张。'))
    assert created.status_code == 201, created.text
    item = created.json()['data']
    assert item['enabled'] is True and item['content'] == '先看第1张。\n再看第2张。'
    try:
        listed = client.get(BASE, headers=headers).json()['data']['items']
        assert listed[0]['id'] == item['id']
        updated = client.post(f'{BASE}/{item["id"]}', headers=headers,
                              json={'name': item['name'], 'content': item['content'], 'priority': 90, 'enabled': False})
        assert updated.status_code == 200, updated.text
        assert updated.json()['data']['priority'] == 90 and updated.json()['data']['enabled'] is False
        again = client.get(BASE, headers=headers).json()['data']['items']
        assert again[-1]['id'] == item['id'] and again[0]['name'] == '错题原因与正确答案'
        duplicate = client.post(BASE, headers=headers, json=body(name=item['name']))
        assert duplicate.status_code == 409
        assert client.post(f'{BASE}/{item["id"]}/delete', headers=headers).status_code == 200
        item = None
        assert client.post(f'{BASE}/{created.json()["data"]["id"]}/delete', headers=headers).status_code == 404
    finally:
        if item is not None:
            client.post(f'{BASE}/{item["id"]}/delete', headers=headers)
    with admin_engine.connect() as db:
        actions = db.execute(text("SELECT action FROM platform_audit_events WHERE actor_id=:id AND action LIKE 'grading_prompts.%'"),
                             {'id': platform['id']}).scalars().all()
    assert sorted(actions) == ['grading_prompts.create', 'grading_prompts.delete', 'grading_prompts.update']


@pytest.mark.parametrize('overrides', [
    {'name': ''}, {'name': '第一行\n第二行'}, {'content': ''}, {'content': '有控制符\x00'},
    {'priority': 0}, {'priority': 10000}, {'priority': '1'}, {'priority': True},
    {'enabled': 'true'}, {'enabled': 1}, {'extra': 1},
])
def test_rejects_invalid_prompt(client, platform, overrides):
    headers = session(client, platform)
    response = client.post(BASE, headers=headers, json=body(**overrides))
    assert response.status_code == 422, response.text


def test_operator_reads_but_cannot_change(client, platform, admin_engine):
    headers = session(client, platform)
    item = client.post(BASE, headers=headers, json=body()).json()['data']
    try:
        with admin_engine.begin() as db:
            db.execute(text("UPDATE platform_accounts SET role='operator' WHERE id=:id"), {'id': platform['id']})
        assert client.get(BASE, headers=headers).status_code == 200
        assert client.post(BASE, headers=headers, json=body()).status_code == 403
        assert client.post(f'{BASE}/{item["id"]}', headers=headers, json=body(enabled=False)).status_code == 403
        assert client.post(f'{BASE}/{item["id"]}/delete', headers=headers).status_code == 403
        with admin_engine.connect() as db:
            assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE actor_id=:id AND action='access.denied'"),
                              {'id': platform['id']}).scalar_one() == 3
    finally:
        with admin_engine.begin() as db:
            db.execute(text("UPDATE platform_accounts SET role='security_admin' WHERE id=:id"), {'id': platform['id']})
        client.post(f'{BASE}/{item["id"]}/delete', headers=headers)
