"""School metadata uses real PostgreSQL, independent platform auth and immutable history."""
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
import test_platform_identity as identity_fixtures
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from wizzy.security import digest

platform = identity_fixtures.platform
session = identity_fixtures.session
BASE = '/api/v1/admin/schools'


def payload(**overrides):
    return {'operation_id': str(uuid4()), 'code': 'SCH-' + uuid4().hex[:12].upper(),
            'name': '小知实验学校', 'region': '浙江省杭州市', 'academic_year': '2026-2027',
            'status': 'active', 'reason': '核验学校档案', **overrides}


def create(client, headers, **overrides):
    result = client.post(BASE, headers=headers, json=payload(**overrides))
    assert result.status_code == 201, result.text
    return result.json()['data']


def change(item, **overrides):
    return {**{k: item[k] for k in ('name', 'region', 'academic_year', 'status')},
            'operation_id': str(uuid4()), 'expected_revision': item['revision'],
            'reason': '更新核验后的学校档案', **overrides}


def test_roundtrip_history_and_audit(client, platform, admin_engine):
    headers = session(client, platform)
    first = create(client, headers, code=' sch_demo ', name=' 同名学校 ')
    assert first['code'] == 'SCH_DEMO' and first['name'] == '同名学校'
    assert first['revision'] == 1 and first['actor_id'] == str(platform['id'])
    assert client.get(f"{BASE}/{first['id']}", headers=headers).json()['data'] == first
    disabled = client.post(f"{BASE}/{first['id']}/revisions", headers=headers,
                           json=change(first, status='disabled'))
    assert disabled.status_code == 201, disabled.text
    second = disabled.json()['data']
    assert second['revision'] == 2 and second['status'] == 'disabled'
    restored = client.post(f"{BASE}/{first['id']}/revisions", headers=headers,
                           json=change(second, status='active')).json()['data']
    assert restored['revision'] == 3
    history = client.get(f"{BASE}/{first['id']}/revisions?limit=1", headers=headers).json()['data']
    assert history['items'] == [restored] and history['next_cursor']
    older = client.get(f"{BASE}/{first['id']}/revisions", headers=headers,
                       params={'limit': 2, 'cursor': history['next_cursor']}).json()['data']
    assert older['items'] == [second, first] and older['next_cursor'] is None
    with admin_engine.connect() as db:
        actions = db.execute(text("SELECT action FROM platform_audit_events WHERE actor_id=:id AND action LIKE 'schools.%' ORDER BY created_at"), {'id': platform['id']}).scalars().all()
    assert actions == ['schools.create', 'schools.revise', 'schools.revise']
    assert 'operation_id' not in first and 'request_snapshot' not in first


@pytest.mark.parametrize('overrides', [
    {'name': ''}, {'name': ' '}, {'name': 'a' * 121}, {'region': ''}, {'region': 'x\ny'},
    {'reason': ''}, {'reason': 'x' * 501}, {'reason': '\t核验'}, {'status': 'deleted'},
    {'code': 'X'}, {'code': '学校'}, {'code': '\nSCH_1'}, {'code': 'S' * 41},
    {'academic_year': '2026-2028'}, {'academic_year': '1999-2000'},
    {'academic_year': '9999-10000'}, {'academic_year': '２０２６-２０２７'},
    {'student_id': str(uuid4())}, {'name': None}, {'operation_id': 'invalid'},
])
def test_invalid_metadata(client, platform, overrides):
    headers = session(client, platform)
    response = client.post(BASE, headers=headers, json=payload(**overrides))
    assert response.status_code == 422, response.text


@pytest.mark.parametrize('expected', [True, 0, -1, 1.0, '1', 999999999])
def test_revision_is_a_strict_bounded_integer(client, platform, expected):
    headers = session(client, platform)
    first = create(client, headers)
    response = client.post(f"{BASE}/{first['id']}/revisions", headers=headers,
                           json=change(first, expected_revision=expected))
    assert response.status_code == 422, response.text


def test_idempotency_unique_code_and_revision_conflict(client, platform, admin_engine):
    headers = session(client, platform)
    body = payload()
    response = client.post(BASE, headers=headers, json=body)
    assert response.status_code == 201
    first = response.json()['data']
    replay = client.post(BASE, headers=headers, json=body)
    assert replay.status_code == 200 and replay.json()['data'] == first
    assert client.post(BASE, headers=headers, json={**body, 'name': '另一学校'}).json()['error']['code'] == 'operation_conflict'
    duplicate = client.post(BASE, headers=headers, json=payload(code=body['code'].lower()))
    assert duplicate.status_code == 409 and duplicate.json()['error']['code'] == 'school_code_conflict'
    other = create(client, headers, name=first['name'])
    revision = change(first, status='disabled')
    second = client.post(f"{BASE}/{first['id']}/revisions", headers=headers, json=revision)
    assert second.status_code == 201
    retry = client.post(f"{BASE}/{first['id']}/revisions", headers=headers, json=revision)
    assert retry.status_code == 200 and retry.json()['data'] == second.json()['data']
    assert client.post(BASE, headers=headers, json=body).json()['data'] == first
    conflict = client.post(f"{BASE}/{first['id']}/revisions", headers=headers, json=change(first))
    assert conflict.status_code == 409 and conflict.json()['error']['code'] == 'school_revision_conflict'
    assert client.post(f"{BASE}/{other['id']}/revisions", headers=headers, json=revision).json()['error']['code'] == 'operation_conflict'
    assert client.post(f"{BASE}/{other['id']}/revisions", headers=headers,
                       json=change(other, operation_id=body['operation_id'])).json()['error']['code'] == 'operation_conflict'
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM school_directory_revisions WHERE school_id=:id'), {'id': first['id']}).scalar_one() == 2
        assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE actor_id=:id AND action LIKE 'schools.%'"), {'id': platform['id']}).scalar_one() == 3


def test_search_filters_pagination_and_missing(client, platform):
    headers = session(client, platform)
    marker = uuid4().hex[:10]
    first = create(client, headers, name=marker + ' 一校', region='区域_百分%', status='active')
    second = create(client, headers, name=marker + ' 二校', status='disabled')
    page = client.get(BASE, headers=headers, params={'q': marker, 'limit': 1}).json()['data']
    assert page['items'] == [second] and page['next_cursor']
    older = client.get(BASE, headers=headers, params={'q': marker, 'limit': 1, 'cursor': page['next_cursor']}).json()['data']
    assert older['items'] == [first] and older['next_cursor'] is None
    assert client.get(BASE, headers=headers, params={'q': marker, 'status': 'active'}).json()['data']['items'] == [first]
    assert first in client.get(BASE, headers=headers, params={'q': '_百分%'}).json()['data']['items']
    assert first in client.get(BASE, headers=headers, params={'q': first['code'].lower()}).json()['data']['items']
    for suffix in ('', '/revisions'):
        assert client.get(f'{BASE}/{uuid4()}{suffix}', headers=headers).status_code == 404
    for params in ({'limit': 101}, {'cursor': 'invalid'}, {'q': 'a\nb'}, {'status': 'bad'}):
        assert client.get(BASE, headers=headers, params=params).status_code == 422


def test_auth_permission_revocation_and_audit(client, platform, owner, admin_engine):
    for headers in ({}, owner['headers']):
        assert client.get(BASE, headers=headers).status_code == 401
        assert client.post(BASE, headers=headers, json=payload()).status_code == 401
    headers = session(client, platform)
    first = create(client, headers)
    with admin_engine.begin() as db:
        db.execute(text("UPDATE platform_accounts SET role='operator' WHERE id=:id"), {'id': platform['id']})
    assert client.get(BASE, headers=headers).status_code == 200
    assert client.get(f"{BASE}/{first['id']}/revisions", headers=headers).status_code == 200
    assert client.post(BASE, headers=headers, json=payload()).status_code == 403
    assert client.post(f"{BASE}/{first['id']}/revisions", headers=headers, json=change(first)).status_code == 403
    with admin_engine.begin() as db:
        assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE actor_id=:id AND action='access.denied'"), {'id': platform['id']}).scalar_one() == 2
        db.execute(text("UPDATE platform_accounts SET role='security_admin' WHERE id=:id"), {'id': platform['id']})
        db.execute(text("DELETE FROM platform_role_permissions WHERE role='security_admin' AND permission='platform.schools.read'"))
    try:
        assert client.get(BASE, headers=headers).status_code == 403
    finally:
        with admin_engine.begin() as db:
            db.execute(text("INSERT INTO platform_role_permissions VALUES('security_admin','platform.schools.read')"))
    with admin_engine.begin() as db:
        db.execute(text('UPDATE platform_sessions SET revoked_at=now() WHERE account_id=:id'), {'id': platform['id']})
    assert client.get(BASE, headers=headers).status_code == 401


def test_concurrent_replay_and_cas(client, app, platform):
    headers = session(client, platform)
    body = payload()
    def post(path, value):
        with TestClient(app, client=(f'test-{uuid4()}', 50000)) as parallel:
            return parallel.post(path, headers=headers, json=value)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: post(BASE, body), range(2)))
    assert sorted(r.status_code for r in results) == [200, 201]
    assert results[0].json()['data'] == results[1].json()['data']
    first = results[0].json()['data']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: post(f"{BASE}/{first['id']}/revisions", change(first)), range(2)))
    assert sorted(r.status_code for r in results) == [201, 409]


def test_runtime_cannot_bypass_capability(app, admin_engine):
    with app.state.engine.connect() as db:
        for table in ('school_directory', 'school_directory_revisions'):
            assert not db.execute(text("SELECT has_table_privilege(current_user,:table,'SELECT,INSERT,UPDATE,DELETE')"), {'table': table}).scalar_one()
        for helper in ('public.school_directory_view(uuid,integer)', 'public.school_metadata_valid(jsonb)'):
            assert not db.execute(text('SELECT has_function_privilege(current_user,:fn,\'EXECUTE\')'), {'fn': helper}).scalar_one()
    with admin_engine.connect() as db:
        rows = db.execute(text("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname IN ('school_directory','school_directory_revisions')")).all()
        assert len(rows) == 2 and all(a and b for a, b in rows)
        assert db.execute(text("SELECT proconfig FROM pg_proc WHERE proname='school_directory_request'")).scalar_one() == ['search_path=pg_catalog, pg_temp']


@pytest.mark.parametrize('mutation', [
    {'operation_id': 'bad'}, {'expected_revision': 1e100}, {'expected_revision': True},
    {'expected_revision': None}, {'academic_year': '2026-2028'}, {'name': []}, {'reason': '\t原因'},
    {'extra': 'forbidden'}, {'status': None},
])
def test_sql_capability_validates_without_http(client, app, platform, mutation):
    headers = session(client, platform)
    first = create(client, headers)
    token = headers['Authorization'].split()[1]
    with app.state.engine.begin() as db:
        result = db.execute(text('''SELECT public.school_directory_request(:hash,'revise','test-sql',
            CAST(:payload AS jsonb),:id,50,NULL,NULL,'',NULL)'''),
            {'hash': digest(token), 'payload': json.dumps(change(first, **mutation)), 'id': first['id']}).scalar_one()
    assert result['status'] == 422


def test_audit_failure_rolls_back_and_history_is_immutable(client, app, platform, admin_engine):
    headers = session(client, platform)
    first = create(client, headers)
    with admin_engine.begin() as db:
        db.execute(text('''CREATE FUNCTION public.fail_school_audit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.action='schools.revise' THEN RAISE EXCEPTION 'injected failure'; END IF; RETURN NEW; END $$;
            CREATE TRIGGER fail_school_audit BEFORE INSERT ON platform_audit_events
                FOR EACH ROW EXECUTE FUNCTION public.fail_school_audit()'''))
    try:
        with TestClient(app, raise_server_exceptions=False) as failing:
            result = failing.post(f"{BASE}/{first['id']}/revisions", headers=headers, json=change(first))
        assert result.status_code == 500 and 'injected' not in result.text
    finally:
        with admin_engine.begin() as db:
            db.execute(text('DROP TRIGGER fail_school_audit ON platform_audit_events; DROP FUNCTION public.fail_school_audit()'))
    assert client.get(f"{BASE}/{first['id']}", headers=headers).json()['data'] == first
    with pytest.raises(DBAPIError), admin_engine.begin() as db:
        db.execute(text('UPDATE school_directory_revisions SET reason=\'篡改\' WHERE school_id=:id'), {'id': first['id']})
