"""Annual school enrollment never grants household access or merges names."""
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
import test_school_structure as structure
from sqlalchemy import text

from wizzy.security import digest

platform = structure.platform
org = structure.org


def body(class_id=None, **overrides):
    return {'operation_id': str(uuid4()), 'code': 'ST-' + uuid4().hex[:12].upper(),
            'academic_year': '2026-2027', 'display_name': '林小知', 'class_id': class_id,
            'status': 'enrolled', 'reason': '核验入学信息', **overrides}


def change(item, **overrides):
    return {**{k: item[k] for k in ('display_name', 'class_id', 'status')},
            'operation_id': str(uuid4()), 'expected_revision': item['revision'], 'reason': '核验学籍变更', **overrides}


@pytest.fixture
def roster(client, org):
    grade = structure.create(client, org, kind='grade')
    klass = structure.create(client, org, kind='class', parent_id=grade['id'])
    headers, school, _ = org
    return headers, school, f"{structure.schools.BASE}/{school['id']}/roster", klass, grade


def create(client, roster, **overrides):
    headers, _, url, klass, _ = roster
    response = client.post(url, headers=headers, json=body(**{'class_id': klass['id'], **overrides}))
    assert response.status_code == 201, response.text
    return response.json()['data']


def test_registration_transfer_history_and_no_household_grant(client, roster, org, platform, admin_engine):
    headers, school, url, klass, grade = roster
    item = create(client, roster, code=' student_01 ', display_name=' 林小知 ')
    assert item['code'] == 'STUDENT_01' and item['display_name'] == '林小知'
    assert item['school_id'] == school['id'] and item['actor_id'] == str(platform['id'])
    other = structure.create(client, org, kind='class', parent_id=grade['id'])
    response = client.post(url+'/'+item['id']+'/revisions', headers=headers, json=change(item, class_id=other['id']))
    assert response.status_code == 201, response.text
    second = response.json()['data']
    assert second['revision'] == 2 and second['class_id'] == other['id']
    assert client.get(url+'/'+item['id'], headers=headers).json()['data'] == second
    page = client.get(url+'/'+item['id']+'/revisions?limit=1', headers=headers).json()['data']
    assert page['items'] == [second] and page['next_cursor']
    older = client.get(url+'/'+item['id']+'/revisions', headers=headers, params={'cursor': page['next_cursor']}).json()['data']
    assert older['items'] == [item] and older['next_cursor'] is None
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM student_access WHERE student_id=:id'), {'id': item['student_id']}).scalar_one() == 0
        assert db.execute(text('SELECT count(*) FROM students WHERE id=:id'), {'id': item['student_id']}).scalar_one() == 0
        assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE actor_id=:id AND action LIKE 'schools.roster.%'"),
                          {'id': platform['id']}).scalar_one() == 2


def test_identity_is_explicit_school_code_and_year(client, roster):
    headers, _, url, _, _ = roster
    first = create(client, roster, code='ST_01')
    same_name = create(client, roster)
    assert same_name['student_id'] != first['student_id']
    duplicate = client.post(url, headers=headers, json=body(first['class_id'], code='ST_01'))
    assert duplicate.status_code == 409 and duplicate.json()['error']['code'] == 'enrollment_exists'
    next_year = create(client, roster, code='ST_01', academic_year='2027-2028', status='paused', class_id=None)
    assert next_year['student_id'] == first['student_id'] and next_year['id'] != first['id']
    school = structure.schools.create(client, headers)
    response = client.post(f"{structure.schools.BASE}/{school['id']}/roster", headers=headers,
                           json=body(code='ST_01', status='paused'))
    assert response.status_code == 201, response.text
    assert response.json()['data']['student_id'] != first['student_id']


@pytest.mark.parametrize('status', ['paused', 'left', 'graduated'])
def test_non_enrolled_allows_empty_class_and_preserves_snapshots(client, roster, status):
    item = create(client, roster)
    headers, _, url, _, _ = roster
    payload = change(item, class_id=None, status=status)
    response = client.post(url+'/'+item['id']+'/revisions', headers=headers, json=payload)
    assert response.status_code == 201, response.text
    assert response.json()['data']['status'] == status
    assert client.post(url+'/'+item['id']+'/revisions', headers=headers, json=change(response.json()['data'], status='enrolled')).status_code == 422


def test_class_must_be_same_school_year_kind_and_effectively_active(client, roster, org):
    headers, _, url, klass, grade = roster
    for class_id, year in [(None, '2026-2027'), (grade['id'], '2026-2027'),
                           (klass['id'], '2027-2028'), (str(uuid4()), '2026-2027')]:
        response = client.post(url, headers=headers, json=body(class_id, academic_year=year))
        assert response.status_code == 422, response.text
    other_school = structure.schools.create(client, headers)
    other_org = (headers, other_school, f"{structure.schools.BASE}/{other_school['id']}/structure")
    other_grade = structure.create(client, other_org, kind='grade')
    other_class = structure.create(client, other_org, kind='class', parent_id=other_grade['id'])
    assert client.post(url, headers=headers, json=body(other_class['id'])).status_code == 422
    response = client.post(org[2]+'/'+grade['id']+'/revisions', headers=headers, json=structure.change(grade, status='disabled'))
    assert response.status_code == 201
    assert client.post(url, headers=headers, json=body(klass['id'])).status_code == 422
    create(client, roster, status='paused')


def test_cas_replay_and_disabled_school(client, roster):
    headers, school, url, klass, _ = roster
    payload = body(klass['id'])
    response = client.post(url, headers=headers, json=payload)
    assert response.status_code == 201, response.text
    item = response.json()['data']
    update = change(item, display_name='核验后姓名')
    assert client.post(url+'/'+item['id']+'/revisions', headers=headers, json=update).status_code == 201
    conflict = client.post(url+'/'+item['id']+'/revisions', headers=headers, json=change(item))
    assert conflict.status_code == 409 and conflict.json()['error']['code'] == 'enrollment_revision_conflict'
    disabled = client.post(f"{structure.schools.BASE}/{school['id']}/revisions", headers=headers,
                           json=structure.schools.change(school, status='disabled'))
    assert disabled.status_code == 201
    replay = client.post(url, headers=headers, json=payload)
    assert replay.status_code == 200 and replay.json()['data'] == item
    assert client.post(url, headers=headers, json={**payload, 'display_name': '其他姓名'}).status_code == 409
    assert client.post(url, headers=headers, json=body(klass['id'])).status_code == 409
    assert client.get(url, headers=headers, params={'academic_year': '2026-2027'}).status_code == 200


def test_list_filters_literal_search_and_stable_registration_cursor(client, roster):
    headers, _, url, _, _ = roster
    first = create(client, roster, code='ST_01', display_name='林_小知')
    second = create(client, roster, code='ST_02', status='paused')
    params = {'academic_year': '2026-2027', 'limit': 1}
    page = client.get(url, headers=headers, params=params).json()['data']
    assert page['items'] == [second] and page['next_cursor']
    revised = client.post(url+'/'+first['id']+'/revisions', headers=headers, json=change(first, display_name='林_同学')).json()['data']
    next_page = client.get(url, headers=headers, params={**params, 'cursor': page['next_cursor']}).json()['data']
    assert next_page['items'] == [revised] and next_page['next_cursor'] is None
    for query, expected in [('ST_01', [revised]), ('_', [second, revised]), ('%', []), ('林_', [revised])]:
        assert client.get(url, headers=headers, params={**params, 'limit': 50, 'q': query}).json()['data']['items'] == expected
    assert client.get(url, headers=headers, params={**params, 'status': 'paused'}).json()['data']['items'] == [second]
    assert client.get(url, headers=headers, params={**params, 'cursor': 'bad'}).status_code == 422


@pytest.mark.parametrize('overrides', [ {'display_name': ''}, {'display_name': 'x'*81}, {'display_name': 'a\nb'},
    {'academic_year': '2026-2028'}, {'academic_year': '１９９９-２０００'}, {'status': 'active'},
    {'class_id': False}, {'code': 'bad code'}, {'parent_phone': '123'}])
def test_invalid_payloads(client, roster, overrides):
    headers, _, url, klass, _ = roster
    assert client.post(url, headers=headers, json=body(**{'class_id': klass['id'], **overrides})).status_code == 422


def test_independent_permission_and_live_revoke(client, roster, owner, platform, admin_engine):
    headers, _, url, klass, _ = roster
    for untrusted in ({}, owner['headers']):
        assert client.get(url, headers=untrusted, params={'academic_year': '2026-2027'}).status_code == 401
    with admin_engine.begin() as db:
        db.execute(text("UPDATE platform_accounts SET role='operator' WHERE id=:id"), {'id': platform['id']})
    assert client.get(url, headers=headers, params={'academic_year': '2026-2027'}).status_code == 200
    assert client.post(url, headers=headers, json=body(klass['id'])).status_code == 403
    with admin_engine.begin() as db:
        db.execute(text("DELETE FROM platform_role_permissions WHERE role='operator' AND permission='platform.students.read'"))
    try:
        assert client.get(url, headers=headers, params={'academic_year': '2026-2027'}).status_code == 403
    finally:
        with admin_engine.begin() as db:
            db.execute(text("INSERT INTO platform_role_permissions VALUES ('operator','platform.students.read')"))
    with admin_engine.begin() as db:
        db.execute(text('UPDATE platform_sessions SET revoked_at=clock_timestamp() WHERE account_id=:id'), {'id': platform['id']})
    assert client.get(url, headers=headers, params={'academic_year': '2026-2027'}).status_code == 401


def test_parallel_revision_only_one_wins(client, roster):
    headers, _, url, _, _ = roster
    item = create(client, roster)
    def write(index):
        return client.post(url+'/'+item['id']+'/revisions', headers=headers, json=change(item, display_name=f'姓名{index}')).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, (1, 2))) == [201, 409]


@pytest.mark.parametrize('patch', [{'display_name': None}, {'status': None}, {'class_id': 1}, {'extra': True},
                                   {'operation_id': None}, {'reason': '\n'}, {'academic_year': '2026-2028'}])
def test_sql_capability_validates_payload_without_http(client, roster, app, patch):
    headers, school, _, klass, _ = roster
    payload = body(**{'class_id': klass['id'], **patch})
    with app.state.engine.begin() as db:
        result = db.execute(text('''SELECT public.school_roster_request(:hash,'create','sql-validation',CAST(:payload AS jsonb),
            :school,NULL,NULL,NULL,NULL,50,NULL,NULL)'''), {'hash': digest(headers['Authorization'].split()[1]),
            'payload': json.dumps(payload), 'school': school['id']}).scalar_one()
        assert result['status'] == 422


def test_audit_failure_rolls_back_identity_and_enrollment(client, roster, admin_engine):
    from sqlalchemy.exc import DBAPIError
    headers, _, url, klass, _ = roster
    with admin_engine.begin() as db:
        db.execute(text("CREATE FUNCTION fail_roster_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.action LIKE 'schools.roster.%' THEN RAISE EXCEPTION 'audit unavailable'; END IF; RETURN NEW; END $$"))
        db.execute(text('CREATE TRIGGER fail_roster_audit BEFORE INSERT ON platform_audit_events FOR EACH ROW EXECUTE FUNCTION fail_roster_audit()'))
    try:
        with pytest.raises(DBAPIError):
            client.post(url, headers=headers, json=body(klass['id']))
        with admin_engine.connect() as db:
            assert db.execute(text('SELECT count(*) FROM school_roster_students WHERE school_id=:id'), {'id': roster[1]['id']}).scalar_one() == 0
        assert client.get(url, headers=headers, params={'academic_year': '2026-2027'}).json()['data']['items'] == []
    finally:
        with admin_engine.begin() as db:
            db.execute(text('DROP TRIGGER fail_roster_audit ON platform_audit_events'))
            db.execute(text('DROP FUNCTION fail_roster_audit()'))


def test_detail_and_history_are_scoped_to_school(client, roster):
    item = create(client, roster)
    other = structure.schools.create(client, roster[0])
    for suffix in ('', '/revisions'):
        response = client.get(f"{structure.schools.BASE}/{other['id']}/roster/{item['id']}{suffix}", headers=roster[0])
        assert response.status_code == 404
