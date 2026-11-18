"""Timetable behavior on isolated real PostgreSQL, not a mock calendar."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from test_result_read import child_session, invited_guardian


def term_path(tid):
    return f'/api/v1/timetables/{tid}'


def create_term(client, headers, sid, **changes):
    return client.post(f'/api/v1/students/{sid}/terms', headers=headers, json={
        'name': '2026 秋季', 'start_date': '2026-09-02', 'end_date': '2026-09-20',
        'week_starts_on': 1, **changes})


def slot(**changes):
    return {'id': str(uuid4()), 'weekday': 1, 'period': 1, 'subject': '数学',
            'weeks': 'all', 'start_time': '08:00', 'end_time': '08:40', 'room': '301', **changes}


def save(client, headers, tid, slots, revision=0, **changes):
    return client.put(term_path(tid), headers=headers, json={
        'expected_revision': revision, 'slots': slots, **changes})


def lessons(client, headers, tid, **changes):
    return client.get(term_path(tid)+'/instances', headers=headers, params={
        'start_date': '2026-08-31', 'end_date': '2026-09-27', **changes})


def override(client, headers, tid, original, revision=1, **changes):
    return client.post('/api/v1/lesson-overrides', headers=headers, json={
        'term_id': tid, 'expected_revision': revision, 'slot_id': original['id'],
        'source_date': '2026-09-07', 'action': 'cancel', 'reason': '学校临时安排', **changes})


@pytest.fixture
def term(client, owner, student):
    response = create_term(client, owner['headers'], student['id'])
    assert response.status_code == 201, response.text
    return response.json()['data']


def test_term_and_empty_template(client, owner, student):
    response = create_term(client, owner['headers'], student['id'])
    assert response.status_code == 201, response.text
    term = response.json()['data']
    assert term['revision'] == term['template_revision'] == 0
    assert term['timezone'] == 'Asia/Shanghai'
    listing = client.get(f"/api/v1/students/{student['id']}/terms", headers=owner['headers'])
    assert listing.status_code == 200, listing.text
    assert listing.json()['data']['items'] == [term]
    value = client.get(term_path(term['id']), headers=owner['headers']).json()['data']
    assert value['term'] == term and value['slots'] == [] and value['overrides'] == []
    assert lessons(client, owner['headers'], term['id']).json()['data']['items'] == []


def test_week_parity_and_term_bounds(term, client, owner):
    h, tid = owner['headers'], term['id']
    slots = [slot(weeks='odd'), slot(weekday=7, period=2, weeks='even')]
    response = save(client, h, tid, slots)
    assert response.status_code == 200, response.text
    result = lessons(client, h, tid).json()['data']
    assert [(x['date'], x['week_number'], x['subject']) for x in result['items']] == [
        ('2026-09-13', 2, '数学'), ('2026-09-14', 3, '数学')]
    assert result['timezone'] == 'Asia/Shanghai' and result['revision'] == 1
    assert response.json()['data']['slots'] == slots


def test_sunday_week_start_and_cross_week(client, owner, student):
    term = create_term(client, owner['headers'], student['id'], week_starts_on=7).json()['data']
    response = save(client, owner['headers'], term['id'], [slot(weekday=7, weeks='even'), slot(weekday=1, period=2, weeks='even')])
    assert response.status_code == 200, response.text
    items = lessons(client, owner['headers'], term['id']).json()['data']['items']
    assert [(x['date'], x['week_number']) for x in items] == [('2026-09-06', 2), ('2026-09-07', 2), ('2026-09-20', 4)]


def test_conflicts_require_explicit_confirmation_and_do_not_write(term, client, owner):
    h, tid = owner['headers'], term['id']
    slots = [slot(), slot(period=2, start_time='08:30', end_time='09:00')]
    response = save(client, h, tid, slots)
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'schedule_conflict'
    warnings = response.json()['error']['field_errors']
    assert warnings and warnings[0]['date'] == '2026-09-07'
    assert set(warnings[0]['slot_ids']) == {x['id'] for x in slots}
    assert client.get(term_path(tid), headers=h).json()['data']['term']['revision'] == 0
    response = save(client, h, tid, slots, confirm_conflicts=True)
    assert response.status_code == 200, response.text
    assert response.json()['data']['conflicts'] == warnings


def test_adjacent_times_and_disjoint_weeks_are_not_conflicts(term, client, owner):
    slots = [slot(weeks='odd'), slot(weeks='even'), slot(period=2, start_time='08:40', end_time='09:00')]
    response = save(client, owner['headers'], term['id'], slots)
    assert response.status_code == 200, response.text
    assert response.json()['data']['conflicts'] == []


def test_period_conflict_without_times(term, client, owner):
    response = save(client, owner['headers'], term['id'], [slot(start_time=None, end_time=None), slot()])
    assert response.status_code == 409, response.text


def test_move_cancel_restore_are_single_instance_with_history(term, client, owner):
    h, tid, original = owner['headers'], term['id'], slot()
    assert save(client, h, tid, [original]).status_code == 200
    response = override(client, h, tid, original, action='move', target_date='2026-09-08',
                        replacement={'period': 3, 'subject': '语文', 'start_time': None, 'end_time': None, 'room': None})
    assert response.status_code == 201, response.text
    items = lessons(client, h, tid).json()['data']['items']
    assert [(x['date'], x['subject']) for x in items] == [('2026-09-08', '语文'), ('2026-09-14', '数学')]
    assert items[0]['source_date'] == '2026-09-07' and items[0]['overridden']
    # A target outside this query still removes the original instance.
    assert lessons(client, h, tid, start_date='2026-09-07', end_date='2026-09-07').json()['data']['items'] == []
    assert len(lessons(client, h, tid, start_date='2026-09-08', end_date='2026-09-08').json()['data']['items']) == 1
    response = override(client, h, tid, original, revision=2)
    assert response.status_code == 201, response.text
    assert [x['date'] for x in lessons(client, h, tid).json()['data']['items']] == ['2026-09-14']
    assert override(client, h, tid, original, revision=3, action='restore').status_code == 201
    assert [x['date'] for x in lessons(client, h, tid).json()['data']['items']] == ['2026-09-07', '2026-09-14']
    value = client.get(term_path(tid), headers=h).json()['data']
    assert value['slots'] == [original] and len(value['overrides']) == 3
    assert [x['action'] for x in value['overrides']] == ['move', 'cancel', 'restore']
    assert value['term']['revision'] == 4 and value['term']['template_revision'] == 1


def test_moved_lesson_can_cross_week_and_collisions_are_confirmed(term, client, owner):
    h, tid, original = owner['headers'], term['id'], slot()
    save(client, h, tid, [original])
    response = override(client, h, tid, original, action='move', target_date='2026-09-14',
                        replacement={k: original[k] for k in ('period', 'subject', 'start_time', 'end_time', 'room')})
    assert response.status_code == 409, response.text
    response = override(client, h, tid, original, action='move', target_date='2026-09-14', confirm_conflicts=True,
                        replacement={k: original[k] for k in ('period', 'subject', 'start_time', 'end_time', 'room')})
    assert response.status_code == 201, response.text
    items = lessons(client, h, tid).json()['data']['items']
    assert len(items) == 2 and items[0]['date'] == items[1]['date'] == '2026-09-14'
    assert len({x['id'] for x in items}) == 2


def test_template_cannot_silently_remove_active_overrides(term, client, owner):
    h, tid, original = owner['headers'], term['id'], slot()
    save(client, h, tid, [original])
    override(client, h, tid, original)
    response = save(client, h, tid, [], revision=2)
    assert response.status_code == 409 and response.json()['error']['code'] == 'active_override_conflict'
    assert save(client, h, tid, [original, slot(weekday=2)], revision=2).status_code == 200
    assert override(client, h, tid, original, revision=3, action='restore').status_code == 201
    assert save(client, h, tid, [], revision=4).status_code == 200
    assert lessons(client, h, tid).json()['data']['items'] == []


@pytest.mark.parametrize('changes', [
    {'end_date': '2026-09-01'}, {'end_date': '2028-01-01'}, {'week_starts_on': 0},
    {'week_starts_on': True}, {'name': '  '}, {'name': 'x\x00y'}, {'timezone': 'UTC'},
])
def test_invalid_terms(client, owner, student, changes):
    response = create_term(client, owner['headers'], student['id'], **changes)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize('changes', [
    {'weekday': 8}, {'period': 0}, {'weeks': 'third'}, {'subject': '  '},
    {'room': 'x\x00'}, {'start_time': '25:00'}, {'end_time': '08:00'},
    {'end_time': None}, {'start_time': '08:00:00'},
])
def test_invalid_slots(term, client, owner, changes):
    response = save(client, owner['headers'], term['id'], [slot(**changes)])
    assert response.status_code == 422, response.text


def test_duplicate_slots_and_query_limits(term, client, owner):
    h, tid, original = owner['headers'], term['id'], slot()
    assert save(client, h, tid, [original, original]).status_code == 422
    assert save(client, h, tid, [slot() for _ in range(101)]).status_code == 422
    assert save(client, h, tid, [], revision=True).status_code == 422
    assert save(client, h, tid, [], confirm_conflicts='yes').status_code == 422
    for params in ({'start_date': '2026-09-10', 'end_date': '2026-09-01'},
                   {'start_date': '2026-01-01', 'end_date': '2026-09-01'}):
        assert lessons(client, h, tid, **params).status_code == 422


@pytest.mark.parametrize('changes', [
    {'source_date': '2026-09-08'}, {'source_date': '2026-08-31'}, {'slot_id': str(uuid4())},
    {'action': 'move', 'target_date': '2026-10-01', 'replacement': {'period': 2, 'subject': '数学'}},
])
def test_invalid_override_source_or_target(term, client, owner, changes):
    original, h, tid = slot(), owner['headers'], term['id']
    save(client, h, tid, [original])
    response = override(client, h, tid, original, **changes)
    assert response.status_code == 422, response.text
    assert client.get(term_path(tid), headers=h).json()['data']['term']['revision'] == 1


@pytest.mark.parametrize('changes', [
    {'action': 'move'}, {'action': 'cancel', 'target_date': '2026-09-09'},
    {'action': 'cancel', 'replacement': {'period': 2, 'subject': '语文'}}, {'reason': '  '},
])
def test_invalid_override_payload(term, client, owner, changes):
    response = override(client, owner['headers'], term['id'], slot(), **changes)
    assert response.status_code == 422, response.text


def test_stale_revision_and_concurrent_saves(term, client, owner):
    h, tid = owner['headers'], term['id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: save(client, h, tid, [slot()]), range(2)))
    assert sorted(x.status_code for x in responses) == [200, 409]
    value = client.get(term_path(tid), headers=h).json()['data']
    assert value['term']['revision'] == 1
    response = override(client, h, tid, value['slots'][0], revision=0)
    assert response.status_code == 409 and response.json()['error']['code'] == 'revision_conflict'


def test_child_read_only_and_other_students_hidden(term, client, owner, student):
    child = child_session(client, owner, student['id'])
    tid = term['id']
    assert client.get(term_path(tid), headers=child).status_code == 200
    assert lessons(client, child, tid).status_code == 200
    assert client.get(f"/api/v1/students/{student['id']}/terms", headers=child).status_code == 200
    assert save(client, child, tid, []).status_code == 403
    assert create_term(client, child, student['id']).status_code == 403
    assert override(client, child, tid, slot()).status_code == 403
    sibling = client.post('/api/v1/students', headers=owner['headers'], json={'nickname': '妹妹', 'grade': 2}).json()['data']
    assert client.get(f"/api/v1/students/{sibling['id']}/terms", headers=child).status_code == 404
    assert client.get(term_path(uuid4()), headers=owner['headers']).status_code == 404
    assert client.get(term_path(tid)).status_code == 401


def test_withdrawal_hides_old_terms_even_after_reconsent(term, client, owner, student):
    h, tid = owner['headers'], term['id']
    response = client.post('/api/v1/consents', headers=h, json={
        'student_id': student['id'], 'policy_version': '2026-10-25',
        'child_rules_version': '2026-10-25', 'granted': False})
    assert response.status_code == 201, response.text
    assert client.get(term_path(tid), headers=h).status_code == 404
    assert client.get(f"/api/v1/students/{student['id']}/terms", headers=h).status_code == 403
    response = client.post('/api/v1/consents', headers=h, json={
        'student_id': student['id'], 'policy_version': '2026-10-25',
        'child_rules_version': '2026-10-25', 'granted': True})
    assert response.status_code == 201, response.text
    assert client.get(term_path(tid), headers=h).status_code == 404
    assert client.get(f"/api/v1/students/{student['id']}/terms", headers=h).json()['data']['items'] == []


def test_audit_failure_rolls_back_template_and_override(term, client, owner, monkeypatch):
    import wizzy.timetables as module

    h, tid, original = owner['headers'], term['id'], slot()
    original_audit = module.audit

    def fail(*args, **kwargs):
        raise RuntimeError('injected timetable audit failure')

    monkeypatch.setattr(module, 'audit', fail)
    with pytest.raises(RuntimeError, match='injected timetable audit failure'):
        save(client, h, tid, [original])
    monkeypatch.setattr(module, 'audit', original_audit)
    assert client.get(term_path(tid), headers=h).json()['data']['term']['revision'] == 0
    save(client, h, tid, [original])
    monkeypatch.setattr(module, 'audit', fail)
    with pytest.raises(RuntimeError, match='injected timetable audit failure'):
        override(client, h, tid, original)
    monkeypatch.setattr(module, 'audit', original_audit)
    value = client.get(term_path(tid), headers=h).json()['data']
    assert value['term']['revision'] == 1 and value['overrides'] == []


def test_terms_pagination(client, owner, student):
    h, sid = owner['headers'], student['id']
    ids = [create_term(client, h, sid, name=f'学期{i}').json()['data']['id'] for i in range(3)]
    cursor, found = None, []
    for _ in ids:
        response = client.get(f'/api/v1/students/{sid}/terms', headers=h,
                              params={'limit': 1, **({'cursor': cursor} if cursor else {})})
        assert response.status_code == 200, response.text
        page = response.json()['data']
        found += [x['id'] for x in page['items']]
        cursor = page['next_cursor']
    assert found == ids and cursor is None
    assert client.get(f'/api/v1/students/{sid}/terms', headers=h, params={'cursor': 'invalid'}).status_code == 422


def test_direct_sql_rls_and_immutable_history(term, client, owner, student, database, admin_engine):
    from wizzy.db import set_context

    h, tid, original = owner['headers'], term['id'], slot()
    save(client, h, tid, [original])
    override(client, h, tid, original)
    uid = client.get('/api/v1/auth/me', headers=h).json()['data']['user_id']
    engine = create_engine(database['url'], hide_parameters=True)
    try:
        with engine.begin() as db:
            for table in ('terms', 'timetable_versions', 'timetable_slots', 'lesson_overrides'):
                assert db.execute(text(f'SELECT count(*) FROM {table}')).scalar_one() == 0
            set_context(db, owner['tenant_id'], student_id=student['id'])
            assert db.execute(text('SELECT count(*) FROM terms WHERE id=:id'), {'id': tid}).scalar_one() == 1
            assert db.execute(text('UPDATE terms SET revision=revision+1 WHERE id=:id'), {'id': tid}).rowcount == 0
        with engine.begin() as db:
            set_context(db, owner['tenant_id'], user_id=uid)
            assert db.execute(text('SELECT count(*) FROM lesson_overrides WHERE term_id=:id'), {'id': tid}).scalar_one() == 1
        with admin_engine.begin() as db:
            db.execute(text("UPDATE memberships SET status='revoked' WHERE tenant_id=:tid AND user_id=:uid"), {'tid': owner['tenant_id'], 'uid': uid})
        with engine.begin() as db:
            set_context(db, owner['tenant_id'], user_id=uid)
            for table in ('terms', 'timetable_versions', 'timetable_slots', 'lesson_overrides'):
                assert db.execute(text(f'SELECT count(*) FROM {table}')).scalar_one() == 0
    finally:
        engine.dispose()



def test_guardian_assignment_and_family_isolation(term, client, owner, student, admin_engine):
    from test_identity_security import registered

    tid, sid, h, original = term['id'], student['id'], owner['headers'], slot()
    assert save(client, h, tid, [original]).status_code == 200
    sibling = client.post('/api/v1/students', headers=h, json={'nickname': '妹妹', 'grade': 2}).json()['data']
    assigned, user = invited_guardian(client, owner, [sid])
    other_student, _ = invited_guardian(client, owner, [sibling['id']])
    _, stranger = registered(client)
    family = client.post('/api/v1/tenants', headers={'Authorization': f"Bearer {stranger['access_token']}"},
                         json={'name': '另一个家庭'}).json()['data']
    outsider = {'Authorization': f"Bearer {family['access_token']}"}
    assert client.get(term_path(tid), headers=assigned).status_code == 200
    assert override(client, assigned, tid, original).status_code == 201
    with admin_engine.begin() as db:
        db.execute(text('DELETE FROM student_access WHERE student_id=:student AND user_id=:user'),
                   {'student': sid, 'user': user})
    for denied in (assigned, other_student, outsider):
        assert client.get(term_path(tid), headers=denied).status_code == 404
        assert lessons(client, denied, tid).status_code == 404
        assert client.get(f'/api/v1/students/{sid}/terms', headers=denied).status_code == 404
        assert create_term(client, denied, sid).status_code == 404
        assert save(client, denied, tid, [], revision=2).status_code == 404
        assert override(client, denied, tid, original, revision=2).status_code == 404
    assert client.get(term_path(tid), headers=h).json()['data']['term']['revision'] == 2


def test_template_and_override_share_one_concurrency_revision(term, client, owner):
    h, tid, original = owner['headers'], term['id'], slot()
    assert save(client, h, tid, [original]).status_code == 200
    with ThreadPoolExecutor(max_workers=2) as pool:
        template = pool.submit(save, client, h, tid, [original, slot(weekday=2)], revision=1)
        change = pool.submit(override, client, h, tid, original, revision=1)
        replies = [template.result(), change.result()]
    assert sorted(r.status_code for r in replies) == [200, 409] or sorted(r.status_code for r in replies) == [201, 409]
    assert next(r for r in replies if r.status_code == 409).json()['error']['code'] == 'revision_conflict'
    value = client.get(term_path(tid), headers=h).json()['data']
    assert value['term']['revision'] == 2
    assert (len(value['slots']), len(value['overrides'])) in ((2, 0), (1, 1))


def test_restore_checks_new_conflicts_without_losing_history(term, client, owner):
    h, tid, original = owner['headers'], term['id'], slot()
    assert save(client, h, tid, [original]).status_code == 200
    assert override(client, h, tid, original).status_code == 201
    # A newly added even-week course is conflict-free while the original is cancelled.
    extra = slot(weeks='even')
    assert save(client, h, tid, [original, extra], revision=2).status_code == 200
    before = client.get(term_path(tid), headers=h).json()['data']
    response = override(client, h, tid, original, revision=3, action='restore')
    assert response.status_code == 409 and response.json()['error']['code'] == 'schedule_conflict'
    assert client.get(term_path(tid), headers=h).json()['data'] == before
    response = override(client, h, tid, original, revision=3, action='restore', confirm_conflicts=True)
    assert response.status_code == 201, response.text
    assert response.json()['data']['term']['revision'] == 4
    assert len(response.json()['data']['overrides']) == 2


def test_term_creation_audit_failure_rolls_back(client, owner, student, monkeypatch):
    import wizzy.timetables as module

    def fail(*args, **kwargs):
        raise RuntimeError('injected term audit failure')

    monkeypatch.setattr(module, 'audit', fail)
    with pytest.raises(RuntimeError, match='injected term audit failure'):
        create_term(client, owner['headers'], student['id'])
    response = client.get(f"/api/v1/students/{student['id']}/terms", headers=owner['headers'])
    assert response.json()['data']['items'] == []
