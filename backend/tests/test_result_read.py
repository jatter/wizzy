"""Read-only conservative views through real restricted PostgreSQL roles."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_execution import execution as execution  # noqa: F401
from test_execution import wait_for_blocker
from test_identity_security import registered
from test_results import complete, document, finish, snapshot
from test_results import result_context as result_context  # noqa: F401


@pytest.fixture
def readable_result(execution, result_context, admin_engine):
    lease, call, _ = result_context
    finish(execution, lease, call)
    result = document(call)
    # Storage order is not a public ordering guarantee.
    result['pages'].reverse()
    assert complete(execution, lease, result)
    with admin_engine.connect() as db:
        submission_id = str(db.execute(text('SELECT submission_id FROM jobs WHERE id=:job_id'), lease).scalar_one())
    return lease, call, result, submission_id


def path(submission_id):
    return f'/api/v1/submissions/{submission_id}/result'


def child_session(client, owner, student_id):
    response = client.post(f'/api/v1/students/{student_id}/pairing-codes', headers=owner['headers'])
    assert response.status_code == 201, response.text
    child = client.post('/api/v1/child-sessions', json={
        'code': response.json()['data']['code'], 'device_name': '结果查看测试设备',
    })
    assert child.status_code == 201, child.text
    return {'Authorization': f"Bearer {child.json()['data']['access_token']}"}


def invited_guardian(client, owner, student_ids):
    email, user = registered(client)
    invitation = client.post('/api/v1/guardian-invitations', headers=owner['headers'], json={
        'email': email, 'student_ids': student_ids,
    })
    assert invitation.status_code == 201, invitation.text
    accepted = client.post('/api/v1/guardian-invitations/accept',
        headers={'Authorization': f"Bearer {user['access_token']}"},
        json={'code': invitation.json()['data']['code']})
    assert accepted.status_code == 200, accepted.text
    return {'Authorization': f"Bearer {accepted.json()['data']['access_token']}"}, user['user_id']


def test_result_view_projects_only_public_conservative_fields(readable_result, client, owner, admin_engine):
    lease, call, stored, submission_id = readable_result
    before = snapshot(admin_engine, lease)
    response = client.get(path(submission_id), headers=owner['headers'])
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    view = response.json()['data']
    assert set(view) == {'submission_id', 'student_id', 'source_revision', 'status', 'created_at',
                         'schema_version', 'successful_pages', 'failed_pages',
                         'needs_guardian_review', 'review_notice', 'pages'}
    assert view['submission_id'] == submission_id
    assert view['source_revision'] == 1 and view['status'] == 'completed'
    assert view['schema_version'] == '1.0-conservative'
    assert (view['successful_pages'], view['failed_pages']) == (3, 0)
    assert view['needs_guardian_review'] is True and '不能作为' in view['review_notice']
    source = client.get(f'/api/v1/submissions/{submission_id}', headers=owner['headers']).json()['data']
    assert [(p['position'], p['media_id']) for p in view['pages']] == [
        (p['position'], p['media_id']) for p in source['pages']]
    for page in view['pages']:
        assert set(page) == {'position', 'media_id', 'status', 'questions'}
        assert page['questions'] == next(p for p in stored['pages'] if p['position'] == page['position'])['questions']
    for internal in ('model_run_id', 'prompt_hash', 'rule_version', 'eval_set_version',
                     'execution_token', 'provider_request_id', 'cost_microyuan', str(call['run_id'])):
        assert internal not in response.text
    assert client.get(path(submission_id), headers=owner['headers']).json()['data'] == view
    assert snapshot(admin_engine, lease) == before


@pytest.mark.parametrize('successes', [0, 2])
def test_partial_and_all_failed_results_still_have_page_errors(execution, result_context, client, owner, admin_engine, successes):
    lease, call, _ = result_context
    finish(execution, lease, call, outcome='succeeded' if successes else 'failed')
    assert complete(execution, lease, document(call, succeeded=successes))
    with admin_engine.connect() as db:
        submission_id = str(db.execute(text('SELECT submission_id FROM jobs WHERE id=:job_id'), lease).scalar_one())
    response = client.get(path(submission_id), headers=owner['headers'])
    assert response.status_code == 200, response.text
    view = response.json()['data']
    assert view['status'] == ('partial' if successes else 'failed')
    assert (view['successful_pages'], view['failed_pages']) == (successes, 3-successes)
    for page in view['pages'][successes:]:
        assert set(page) == {'position', 'media_id', 'status', 'error_code'}
        assert page['status'] == 'failed' and page['error_code'] == 'invalid_model_output'


def test_no_result_is_not_invented_or_charged(execution, result_context, client, owner, admin_engine):
    lease, _, _ = result_context
    with admin_engine.connect() as db:
        submission_id = str(db.execute(text('SELECT submission_id FROM jobs WHERE id=:job_id'), lease).scalar_one())
    before = snapshot(admin_engine, lease)
    response = client.get(path(submission_id), headers=owner['headers'])
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'result_not_ready'
    assert response.headers['cache-control'] == 'no-store'
    assert snapshot(admin_engine, lease) == before
    assert client.get(path(uuid4()), headers=owner['headers']).status_code == 404
    assert client.get(path(submission_id)).status_code == 401
    assert client.get(path('not-a-uuid'), headers=owner['headers']).status_code == 422


def test_child_and_assigned_guardian_read_but_not_other_students(readable_result, client, owner, student):
    *_, submission_id = readable_result
    child = child_session(client, owner, student['id'])
    guardian, _ = invited_guardian(client, owner, [student['id']])
    for headers in (child, guardian):
        assert client.get(path(submission_id), headers=headers).status_code == 200
    sibling = client.post('/api/v1/students', headers=owner['headers'], json={'nickname': '另一个', 'grade': 2}).json()['data']
    assert client.post('/api/v1/consents', headers=owner['headers'], json={
        'student_id': sibling['id'], 'policy_version': '2026-10-25',
        'child_rules_version': '2026-10-25', 'granted': True,
    }).status_code == 201
    sibling_child = child_session(client, owner, sibling['id'])
    sibling_guardian, _ = invited_guardian(client, owner, [sibling['id']])
    other_tenant = client.post('/api/v1/tenants', headers=owner['headers'], json={'name': '其他家庭'}).json()['data']
    for headers in (sibling_child, sibling_guardian, {'Authorization': f"Bearer {other_tenant['access_token']}"}):
        response = client.get(path(submission_id), headers=headers)
        assert response.status_code == 404, response.text
        assert '12 + 8' not in response.text


@pytest.mark.parametrize(('invalidate', 'status'), [
    ('consent', 403), ('epoch', 404), ('inactive', 404), ('tenant', 401),
    ('result_revision', 409), ('receipt_revision', 409), ('job_epoch', 409), ('cancelled', 409),
])
def test_result_view_rechecks_live_state(readable_result, client, owner, student, admin_engine, invalidate, status):
    lease, _, _, submission_id = readable_result
    with admin_engine.begin() as db:
        if invalidate == 'consent':
            db.execute(text('UPDATE students SET consent_active=false WHERE id=:id'), {'id': student['id']})
        elif invalidate == 'epoch':
            db.execute(text('UPDATE students SET deletion_epoch=deletion_epoch+1 WHERE id=:id'), {'id': student['id']})
        elif invalidate == 'inactive':
            db.execute(text("UPDATE students SET status='disabled' WHERE id=:id"), {'id': student['id']})
        elif invalidate == 'tenant':
            db.execute(text("UPDATE tenants SET status='suspended' WHERE id=:id"), {'id': owner['tenant_id']})
        elif invalidate == 'result_revision':
            db.execute(text('UPDATE grading_results SET source_revision=source_revision+1 WHERE event_id=:event_id'), lease)
        elif invalidate == 'receipt_revision':
            db.execute(text('UPDATE execution_receipts SET source_revision=source_revision+1 WHERE event_id=:event_id'), lease)
        elif invalidate == 'job_epoch':
            db.execute(text('UPDATE jobs SET deletion_epoch=deletion_epoch+1 WHERE id=:job_id'), lease)
        else:
            db.execute(text("UPDATE submissions SET status='cancelled' WHERE id=:id"), {'id': submission_id})
    response = client.get(path(submission_id), headers=owner['headers'])
    assert response.status_code == status, response.text
    assert '12 + 8' not in response.text


def test_regrant_does_not_resurrect_result(readable_result, client, owner, student):
    *_, submission_id = readable_result
    body = {'student_id': student['id'], 'policy_version': '2026-10-25',
            'child_rules_version': '2026-10-25', 'granted': False}
    assert client.post('/api/v1/consents', headers=owner['headers'], json=body).status_code == 201
    assert client.post('/api/v1/consents', headers=owner['headers'], json=body | {'granted': True}).status_code == 201
    assert client.get(path(submission_id), headers=owner['headers']).status_code == 404


def test_revoked_guardian_loses_result_access(readable_result, client, owner, student, admin_engine):
    *_, submission_id = readable_result
    headers, user_id = invited_guardian(client, owner, [student['id']])
    assert client.get(path(submission_id), headers=headers).status_code == 200
    with admin_engine.begin() as db:
        db.execute(text('DELETE FROM student_access WHERE student_id=:student AND user_id=:user'),
                   {'student': student['id'], 'user': user_id})
    assert client.get(path(submission_id), headers=headers).status_code == 404


def test_waiting_read_does_not_bypass_consent_withdrawal(readable_result, client, owner, student, admin_engine):
    *_, submission_id = readable_result
    with ThreadPoolExecutor(max_workers=1) as pool:
        with admin_engine.begin() as db:
            blocker = db.execute(text('SELECT pg_backend_pid()')).scalar_one()
            db.execute(text('UPDATE students SET consent_active=false,deletion_epoch=deletion_epoch+1 WHERE id=:id'), {'id': student['id']})
            future = pool.submit(client.get, path(submission_id), headers=owner['headers'])
            wait_for_blocker(admin_engine, blocker)
            assert not future.done()
        response = future.result(timeout=5)
    assert response.status_code == 403, response.text
    assert '12 + 8' not in response.text


def test_result_reader_does_not_grant_private_table_access(readable_result, database):
    *_, submission_id = readable_result
    for key in ('url', 'dispatcher_url', 'worker_url'):
        engine = create_engine(database[key], hide_parameters=True)
        try:
            for query in ('SELECT payload FROM public.grading_results',
                          'DELETE FROM public.grading_results',
                          'UPDATE public.grading_results SET successful_pages=0'):
                with engine.begin() as db, pytest.raises(DBAPIError):
                    db.execute(text(query))
            with engine.begin() as db:
                if key == 'url':
                    # API role needs both trusted tenant and principal context.
                    assert db.execute(text('SELECT public.read_grading_result(:id)'), {'id': submission_id}).scalar_one() is None
                else:
                    with pytest.raises(DBAPIError):
                        db.execute(text('SELECT public.read_grading_result(:id)'), {'id': submission_id})
        finally:
            engine.dispose()


def test_database_reader_enforces_principal_not_only_tenant(readable_result, client, owner, student, database, admin_engine):
    from wizzy.db import set_context

    *_, submission_id = readable_result
    _, user_id = invited_guardian(client, owner, [student['id']])
    engine = create_engine(database['url'], hide_parameters=True)
    try:
        with engine.begin() as db:
            set_context(db, owner['tenant_id'])
            assert db.execute(text('SELECT public.read_grading_result(:id)'), {'id': submission_id}).scalar_one() is None
            set_context(db, owner['tenant_id'], user_id=user_id)
            assert db.execute(text('SELECT public.read_grading_result(:id)'), {'id': submission_id}).scalar_one()['submission_id'] == submission_id
        with admin_engine.begin() as db:
            db.execute(text('DELETE FROM student_access WHERE student_id=:student AND user_id=:user'),
                       {'student': student['id'], 'user': user_id})
        with engine.begin() as db:
            set_context(db, owner['tenant_id'], user_id=user_id)
            assert db.execute(text('SELECT public.read_grading_result(:id)'), {'id': submission_id}).scalar_one() is None
            set_context(db, owner['tenant_id'], student_id=student['id'])
            assert db.execute(text('SELECT public.read_grading_result(:id)'), {'id': submission_id}).scalar_one()['submission_id'] == submission_id
            set_context(db, uuid4(), student_id=student['id'])
            assert db.execute(text('SELECT public.read_grading_result(:id)'), {'id': submission_id}).scalar_one() is None
    finally:
        engine.dispose()
