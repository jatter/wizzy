"""Organization graph rules, serialized changes and history on real PostgreSQL."""
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
import test_school_directory as schools
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from wizzy.security import digest

platform = schools.platform
session = schools.session


def body(**overrides):
    return {'operation_id': str(uuid4()), 'code': 'ORG-' + uuid4().hex[:12].upper(),
            'kind': 'department', 'name': '教学部', 'academic_year': '2026-2027',
            'status': 'active', 'parent_id': None, 'reason': '核验学校组织', **overrides}


def change(node, **overrides):
    return {**{k: node[k] for k in ('name', 'status', 'parent_id')},
            'operation_id': str(uuid4()), 'expected_revision': node['revision'], 'reason': '调整学校组织', **overrides}


@pytest.fixture
def org(client, platform):
    headers = session(client, platform)
    school = schools.create(client, headers)
    return headers, school, f"{schools.BASE}/{school['id']}/structure"


def create(client, org, **overrides):
    headers, _, url = org
    response = client.post(url, headers=headers, json=body(**overrides))
    assert response.status_code == 201, response.text
    return response.json()['data']


def test_tree_revisions_and_history_snapshot(client, org, platform, admin_engine):
    headers, school, url = org
    campus = create(client, org, kind='campus', name=' 主校区 ', code=' campus_1 ')
    grade = create(client, org, kind='grade', name='一年级', parent_id=campus['id'])
    node = create(client, org, kind='class', name='一班', parent_id=grade['id'])
    assert campus['code'] == 'CAMPUS_1' and campus['name'] == '主校区'
    assert node['school_id'] == school['id']
    tree = client.get(url, headers=headers, params={'academic_year': '2026-2027'}).json()['data']
    assert tree == {'items': [campus, grade, node], 'academic_year': '2026-2027', 'school_status': 'active', 'school_revision': 1}
    update = change(node, name='一（1）班', status='disabled')
    second = client.post(f"{url}/{node['id']}/revisions", headers=headers, json=update).json()['data']
    assert second['revision'] == 2 and second['status'] == 'disabled'
    assert client.get(f"{url}/{node['id']}", headers=headers).json()['data'] == second
    history = client.get(f"{url}/{node['id']}/revisions?limit=1", headers=headers).json()['data']
    assert history['items'] == [second] and history['next_cursor']
    older = client.get(f"{url}/{node['id']}/revisions", headers=headers,
                       params={'limit': 1, 'cursor': history['next_cursor']}).json()['data']
    assert older['items'] == [node] and older['next_cursor'] is None
    assert client.get(url, headers=headers, params={'academic_year': '2025-2026'}).json()['data']['items'] == []
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE actor_id=:id AND action LIKE 'schools.structure.%'"), {'id': platform['id']}).scalar_one() == 4


@pytest.mark.parametrize('overrides', [
    {'name': ''}, {'name': ' '}, {'name': 'x' * 121}, {'name': 'x\ny'}, {'reason': ''},
    {'reason': 'x' * 501}, {'status': 'deleted'}, {'code': 'X'}, {'code': '教学'}, {'kind': 'school'},
    {'academic_year': '2026-2028'}, {'academic_year': '1999-2000'}, {'academic_year': '２０２６-２０２７'},
    {'parent_id': 'invalid'}, {'operation_id': 'invalid'}, {'student_id': str(uuid4())},
])
def test_validation(client, org, overrides):
    headers, _, url = org
    assert client.post(url, headers=headers, json=body(**overrides)).status_code == 422


@pytest.mark.parametrize('expected', [True, 0, -1, 1.0, '1', 999999999])
def test_strict_revision(client, org, expected):
    headers, _, url = org
    node = create(client, org)
    assert client.post(f"{url}/{node['id']}/revisions", headers=headers,
                       json=change(node, expected_revision=expected)).status_code == 422


def test_idempotency_cas_code_and_disabled_school(client, org):
    headers, school, url = org
    original = body()
    first = client.post(url, headers=headers, json=original).json()['data']
    assert client.post(url, headers=headers, json=original).json()['data'] == first
    assert client.post(url, headers=headers, json={**original, 'name': '其他名称'}).json()['error']['code'] == 'operation_conflict'
    assert client.post(url, headers=headers, json=body(code=first['code'])).json()['error']['code'] == 'structure_code_conflict'
    revise = change(first, name='教研部')
    route = f"{url}/{first['id']}/revisions"
    second = client.post(route, headers=headers, json=revise).json()['data']
    assert client.post(route, headers=headers, json=change(first)).json()['error']['code'] == 'structure_revision_conflict'
    assert client.post(f"{schools.BASE}/{school['id']}/revisions", headers=headers,
                       json=schools.change(school, status='disabled')).status_code == 201
    replay = client.post(route, headers=headers, json=revise)
    assert replay.status_code == 200 and replay.json()['data'] == second
    assert client.post(url, headers=headers, json=body()).json()['error']['code'] == 'school_disabled'
    assert client.post(route, headers=headers, json=change(second)).json()['error']['code'] == 'school_disabled'
    tree = client.get(url, headers=headers, params={'academic_year': '2026-2027'}).json()['data']
    assert tree['school_status'] == 'disabled' and tree['items'] == [second]


@pytest.mark.parametrize(('kind', 'parent_kind', 'allowed'), [
    ('campus', None, True), ('campus', 'department', False), ('department', None, True),
    ('department', 'campus', True), ('department', 'grade', False), ('department', 'class', False),
    ('grade', None, True), ('grade', 'department', True), ('grade', 'grade', False),
    ('class', None, False), ('class', 'campus', False), ('class', 'grade', True),
])
def test_parent_type_rules(client, org, kind, parent_kind, allowed):
    headers, _, url = org
    if parent_kind == 'class':
        grade = create(client, org, kind='grade')
        parent = create(client, org, kind='class', parent_id=grade['id'])
    else:
        parent = create(client, org, kind=parent_kind) if parent_kind else None
    response = client.post(url, headers=headers, json=body(kind=kind, parent_id=parent['id'] if parent else None))
    assert response.status_code == (201 if allowed else 422), response.text
    if not allowed:
        assert response.json()['error']['code'] == 'invalid_structure_parent'


def test_cross_school_year_missing_and_cycle(client, org):
    headers, _, url = org
    other = schools.create(client, headers)
    elsewhere = create(client, (headers, other, f"{schools.BASE}/{other['id']}/structure"))
    last_year = create(client, org, academic_year='2025-2026')
    for parent in (elsewhere['id'], last_year['id'], str(uuid4())):
        response = client.post(url, headers=headers, json=body(parent_id=parent))
        assert response.status_code == 422 and response.json()['error']['code'] == 'invalid_structure_parent'
    a = create(client, org)
    b = create(client, org, parent_id=a['id'])
    for parent in (a['id'], b['id']):
        response = client.post(f"{url}/{a['id']}/revisions", headers=headers, json=change(a, parent_id=parent))
        assert response.status_code == 422 and response.json()['error']['code'] == 'structure_cycle'
    assert client.get(f"{url}/{elsewhere['id']}", headers=headers).status_code == 404
    assert client.get(f"{url}/{elsewhere['id']}/revisions", headers=headers).status_code == 404


def test_depth_and_subtree_move(client, org):
    headers, _, url = org
    chain = [create(client, org)]
    for _ in range(7):
        chain.append(create(client, org, parent_id=chain[-1]['id']))
    response = client.post(url, headers=headers, json=body(parent_id=chain[-1]['id']))
    assert response.status_code == 422 and response.json()['error']['code'] == 'structure_depth'
    root = create(client, org)
    create(client, org, parent_id=root['id'])
    response = client.post(f"{url}/{root['id']}/revisions", headers=headers, json=change(root, parent_id=chain[6]['id']))
    assert response.status_code == 422 and response.json()['error']['code'] == 'structure_depth'
    assert client.post(f"{url}/{root['id']}/revisions", headers=headers,
                       json=change(root, parent_id=chain[5]['id'])).status_code == 201


def test_concurrent_moves_cannot_form_cycle(client, org, app, platform, admin_engine):
    headers, _, url = org
    a, b = create(client, org), create(client, org)
    # Different administrators bypass account-row serialization; only the school lock protects the graph.
    with admin_engine.begin() as db:
        other = uuid4()
        db.execute(text('''INSERT INTO platform_accounts(id,email,password_hash,encrypted_totp,credential_version,role)
            SELECT :id,:email,password_hash,encrypted_totp,credential_version,role FROM platform_accounts WHERE id=:source'''),
            {'id': other, 'email': f'{other}@example.test', 'source': platform['id']})
        token = uuid4().hex
        db.execute(text('''INSERT INTO platform_sessions(id,account_id,token_hash,credential_version,expires_at)
            SELECT :id,id,:hash,credential_version,clock_timestamp()+interval '10 minutes' FROM platform_accounts WHERE id=:account'''),
            {'id': uuid4(), 'hash': digest(token), 'account': other})
    def move(args):
        node, parent, auth = args
        with TestClient(app, client=(f'graph-{uuid4()}', 50000)) as peer:
            return peer.post(f"{url}/{node['id']}/revisions", headers=auth, json=change(node, parent_id=parent['id'])).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(move, [(a, b, headers), (b, a, {'Authorization': f'Bearer {token}'})])) == [201, 422]


def test_permissions_revocation_and_auth(client, org, platform, admin_engine, owner):
    headers, _, url = org
    node = create(client, org)
    for auth in ({}, owner['headers']):
        assert client.get(url+'?academic_year=2026-2027', headers=auth).status_code == 401
    with admin_engine.begin() as db:
        db.execute(text("UPDATE platform_accounts SET role='operator' WHERE id=:id"), {'id': platform['id']})
    assert client.get(url+'?academic_year=2026-2027', headers=headers).status_code == 200
    denied = client.post(f"{url}/{node['id']}/revisions", headers=headers, json=change(node))
    assert denied.status_code == 403
    with admin_engine.begin() as db:
        assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE actor_id=:id AND action='access.denied'"), {'id': platform['id']}).scalar_one() == 1
        db.execute(text('UPDATE platform_sessions SET revoked_at=clock_timestamp() WHERE account_id=:id'), {'id': platform['id']})
    assert client.get(url+'?academic_year=2026-2027', headers=headers).status_code == 401


def test_audit_failure_rolls_back_node(client, org, admin_engine):
    headers, _, url = org
    with admin_engine.begin() as db:
        db.execute(text("CREATE FUNCTION fail_structure_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.action LIKE 'schools.structure.%' THEN RAISE EXCEPTION 'audit unavailable'; END IF; RETURN NEW; END $$"))
        db.execute(text('CREATE TRIGGER fail_structure_audit BEFORE INSERT ON platform_audit_events FOR EACH ROW EXECUTE FUNCTION fail_structure_audit()'))
    try:
        with pytest.raises(DBAPIError):
            client.post(url, headers=headers, json=body())
        assert client.get(url+'?academic_year=2026-2027', headers=headers).json()['data']['items'] == []
    finally:
        with admin_engine.begin() as db:
            db.execute(text('DROP TRIGGER fail_structure_audit ON platform_audit_events'))
            db.execute(text('DROP FUNCTION fail_structure_audit()'))


def test_capability_rejects_malformed_payloads(client, org, app):
    headers, school, _ = org
    token = headers['Authorization'].split()[1]
    for overrides in ({'parent_id': None, 'name': None}, {'status': None}, {'reason': None},
                      {'parent_id': str(uuid4()).upper()}, {'kind': None}, {'academic_year': '2026-2028'},
                      {'extra': True}, {'operation_id': 'invalid'}):
        with app.state.engine.begin() as db:
            result = db.execute(text('''SELECT public.school_structure_request(:hash,'create','test',CAST(:payload AS jsonb),
                :school,NULL,NULL,50,NULL,NULL)'''), {'hash': digest(token), 'school': school['id'],
                'payload': json.dumps(body(**overrides))}).scalar_one()
        assert result['status'] == 422, result
