"""Bounded plan summaries use current authorization and the current deletion epoch."""
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from test_execution import execution as execution  # noqa: F401
from test_learning_reports import report_result as report_result  # noqa: F401
from test_result_read import child_session, invited_guardian
from test_result_read import readable_result as readable_result  # noqa: F401
from test_results import result_context as result_context  # noqa: F401
from test_review_plans import generate
from test_review_plans import plan_tasks as plan_tasks  # noqa: F401

from wizzy.db import set_context


def history(client, headers, target, **params):
    return client.get('/api/v1/review-plans', headers=headers,
                      params={'student_id': target, **params})


def test_empty_history_then_persistent_lightweight_summaries(plan_tasks, client, owner, student, admin_engine):
    h = owner['headers']
    assert history(client, h, student['id']).json()['data'] == {'items': [], 'next_cursor': None}
    plans = [generate(client, h, student['id']).json()['data'],
             generate(client, h, student['id'], type='monthly', period_start='2026-10-01').json()['data']]
    with admin_engine.connect() as db:
        before = db.execute(text('SELECT count(*) FROM audit_events')).scalar_one()
    for _ in range(2):
        response = history(client, h, student['id'])
        assert response.status_code == 200, response.text
        data = response.json()['data']
        assert data['next_cursor'] is None
        for summary, plan in zip(data['items'], reversed(plans), strict=True):
            assert set(summary) == {'id', 'student_id', 'type', 'period_start', 'period_end', 'timezone',
                                    'report_id', 'created_at', 'question_count', 'estimated_minutes', 'duration_basis'}
            assert summary == {key: plan[key] for key in summary}
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM audit_events')).scalar_one() == before
        assert db.execute(text('SELECT count(*) FROM review_plans WHERE student_id=:id'),
                          {'id': student['id']}).scalar_one() == 2


def test_descending_cursor_is_stable_for_identical_timestamps_and_filters(client, owner, student, admin_engine):
    h = owner['headers']
    plans = [generate(client, h, student['id'], type=kind,
                      period_start='2026-10-01' if kind == 'monthly' else '2026-10-26').json()['data']
             for kind in ['weekly', 'monthly', 'weekly', 'monthly', 'weekly']]
    with admin_engine.begin() as db:
        db.execute(text("UPDATE review_plans SET created_at='2026-10-26T03:00:00Z' WHERE student_id=:id"),
                   {'id': student['id']})
    for kind in [None, 'weekly', 'monthly']:
        expected = sorted([p['id'] for p in plans if kind is None or p['type'] == kind], reverse=True)
        seen, cursor = [], None
        while True:
            params = {'limit': 2}
            if kind:
                params['type'] = kind
            if cursor:
                params['cursor'] = cursor
            response = history(client, h, student['id'], **params)
            assert response.status_code == 200, response.text
            data = response.json()['data']
            seen.extend(p['id'] for p in data['items'])
            cursor = data['next_cursor']
            if cursor is None:
                break
            assert len(seen) < len(expected)
        assert seen == expected


def test_history_obeys_student_and_tenant_scope(client, owner, student):
    h = owner['headers']
    generate(client, h, student['id'])
    sibling = client.post('/api/v1/students', headers=h, json={'nickname': '同家庭学生', 'grade': 3}).json()['data']
    assigned, _ = invited_guardian(client, owner, [student['id']])
    unassigned, _ = invited_guardian(client, owner, [sibling['id']])
    child = child_session(client, owner, student['id'])
    for headers, status in [(h, 200), (assigned, 200), (unassigned, 404), (child, 200), ({}, 401)]:
        assert history(client, headers, student['id']).status_code == status
    assert history(client, h, sibling['id']).status_code == 404
    consent = client.post('/api/v1/consents', headers=h, json={
        'student_id': sibling['id'], 'policy_version': '2026-10-25',
        'child_rules_version': '2026-10-25', 'granted': True})
    assert consent.status_code == 201, consent.text
    assert history(client, h, sibling['id']).json()['data']['items'] == []
    assert history(client, child, sibling['id']).status_code == 404
    assert history(client, h, uuid4()).status_code == 404
    outsider = client.post('/api/v1/auth/register', json={
        'email': f'outsider-{uuid4().hex}@example.test', 'password': 'Test-only-strong-passphrase',
        'privacy_version': '2026-10-25', 'accepted_privacy': True}).json()['data']
    outsider_h = {'Authorization': 'Bearer '+outsider['access_token']}
    tenant = client.post('/api/v1/tenants', headers=outsider_h, json={'name': '另一家庭'}).json()['data']
    assert history(client, {'Authorization': 'Bearer '+tenant['access_token']}, student['id']).status_code == 404


@pytest.mark.parametrize('params', [{'limit': 0}, {'limit': 101}, {'type': 'daily'}, {'cursor': 'broken'},
                                   {'student_id': 'invalid'}])
def test_history_validates_query(client, owner, student, params):
    assert history(client, owner['headers'], student['id'], **params).status_code == 422


@pytest.mark.parametrize('change', ['tenant', 'member', 'user', 'student', 'consent', 'epoch', 'assignment'])
def test_capability_rechecks_current_access_and_deletion_epoch(client, owner, student, database, admin_engine, change):
    generate(client, owner['headers'], student['id'])
    headers, actor = invited_guardian(client, owner, [student['id']])
    changes = {
        'tenant': ("UPDATE tenants SET status='suspended' WHERE id=:id", owner['tenant_id']),
        'member': ("UPDATE memberships SET status='revoked' WHERE user_id=:id", actor),
        'user': ("UPDATE users SET status='disabled' WHERE id=:id", actor),
        'student': ("UPDATE students SET status='deleted' WHERE id=:id", student['id']),
        'consent': ('UPDATE students SET consent_active=false WHERE id=:id', student['id']),
        'epoch': ('UPDATE students SET deletion_epoch=deletion_epoch+1 WHERE id=:id', student['id']),
        'assignment': ('DELETE FROM student_access WHERE student_id=:id AND user_id=:actor', student['id']),
    }
    query, identifier = changes[change]
    with admin_engine.begin() as db:
        db.execute(text(query), {'id': identifier, 'actor': actor})
    engine = create_engine(database['url'], hide_parameters=True)
    try:
        with engine.begin() as db:
            set_context(db, owner['tenant_id'], user_id=actor)
            result = db.execute(text('SELECT public.read_review_plans(:id,NULL,20,NULL,NULL)'),
                                {'id': student['id']}).scalar_one()
            assert result == ({'items': []} if change == 'epoch' else {'error': 'not_found'})
        response = history(client, headers, student['id'])
        if change == 'epoch':
            assert response.status_code == 200 and response.json()['data']['items'] == []
        else:
            assert response.status_code in (401, 403, 404)
    finally:
        engine.dispose()


@pytest.mark.parametrize('maximum,kind,after_date,after_id', [
    (None, None, None, None), (0, None, None, None), (101, None, None, None),
    (20, 'daily', None, None), (20, None, '2026-10-26T00:00:00Z', None), (20, None, None, uuid4()),
])
def test_restricted_capability_rejects_invalid_arguments(client, owner, student, database, admin_engine,
                                                       maximum, kind, after_date, after_id):
    _, actor = invited_guardian(client, owner, [student['id']])
    engine = create_engine(database['url'], hide_parameters=True)
    try:
        with engine.begin() as db:
            set_context(db, owner['tenant_id'], user_id=actor)
            result = db.execute(text('SELECT public.read_review_plans(:id,:kind,:n,:date,:after_id)'),
                                {'id': student['id'], 'kind': kind, 'n': maximum,
                                 'date': after_date, 'after_id': after_id}).scalar_one()
            assert result == {'error': 'invalid_plan'}
    finally:
        engine.dispose()
