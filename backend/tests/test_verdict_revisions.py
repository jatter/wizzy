"""Guardian conclusions are versioned user input, never model gold labels."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_execution import execution as execution  # noqa: F401
from test_execution import wait_for_blocker
from test_question_revisions import correction, questions
from test_result_read import child_session, invited_guardian
from test_result_read import readable_result as readable_result  # noqa: F401
from test_results import result_context as result_context  # noqa: F401
from test_results import snapshot

from wizzy.db import set_context


def override(client, headers, question_id, **extra):
    return client.post(f'/api/v1/questions/{question_id}/verdict-overrides', headers=headers, json={
        'expected_revision': 1, 'expected_verdict_revision': 0,
        'verdict': 'correct', 'reason': '监护人核对原图及解题过程', **extra})


def view(client, owner, question_id):
    response = client.get(f'/api/v1/questions/{question_id}', headers=owner['headers'])
    assert response.status_code == 200, response.text
    return response.json()['data']


def events(db, question_id):
    return db.execute(text('SELECT payload FROM question_verdict_events WHERE question_id=:id ORDER BY new_revision'),
                      {'id': question_id}).scalars().all()


def test_confirm_preserves_machine_result_and_versions_history(readable_result, client, owner, admin_engine):
    sid = readable_result[3]
    original = client.get(f'/api/v1/submissions/{sid}/result', headers=owner['headers']).json()
    q = questions(client, owner, sid)[0]
    response = override(client, owner['headers'], q['question_id'])
    assert response.status_code == 201, response.text
    assert response.headers['cache-control'] == 'no-store'
    result = response.json()['data']
    assert (result['revision'], result['verdict_revision'], result['effective_revision']) == (1, 1, 2)
    assert result['verdict'] == 'correct' and result['verdict_provenance'] == 'guardian'
    assert not result['needs_guardian_review'] and result['machine_question'] == q['machine_question']
    assert result['verdict_history'][0]['question_revision'] == 1
    assert result['verdict_history'][0]['provenance'] == 'guardian'
    assert 'actor_id' not in result['verdict_history'][0]
    second = override(client, owner['headers'], q['question_id'], expected_verdict_revision=1, verdict='incorrect')
    assert second.status_code == 201, second.text
    latest = second.json()['data']
    assert latest['verdict'] == 'incorrect' and latest['effective_revision'] == 3
    assert latest['verdict_history'][0] == result['verdict_history'][0]
    assert len(latest['verdict_history']) == 2
    assert client.get(f'/api/v1/submissions/{sid}/result', headers=owner['headers']).json()['data'] == original['data']
    assert view(client, owner, q['question_id']) == latest
    with admin_engine.connect() as db:
        stored = events(db, q['question_id'])
        assert [(e['old_revision'], e['new_revision']) for e in stored] == [(1, 2), (2, 3)]
        assert all(e['question_id'] == q['question_id'] and e['type'] == 'VerdictChanged' for e in stored)
        assert 'reason' not in str(stored) and 'stem' not in str(stored)
        assert db.execute(text("SELECT count(*) FROM audit_events WHERE resource_id=:id AND action='question.verdict_overridden'"),
                          {'id': q['question_id']}).scalar_one() == 2


def test_ocr_edit_invalidates_human_conclusion_and_emits_version(readable_result, client, owner, admin_engine):
    q = questions(client, owner, readable_result[3])[0]
    assert override(client, owner['headers'], q['question_id']).status_code == 201
    edited = correction(client, owner['headers'], q['question_id'])
    assert edited.status_code == 201, edited.text
    current = edited.json()['data']
    assert current['verdict'] == 'pending_review' and current['needs_guardian_review']
    assert current['verdict_provenance'] == 'ocr_pending'
    assert current['effective_verdict_revision'] is None
    assert (current['revision'], current['verdict_revision'], current['effective_revision']) == (2, 1, 3)
    assert len(current['verdict_history']) == 1
    assert override(client, owner['headers'], q['question_id'], expected_verdict_revision=1).status_code == 409
    result = override(client, owner['headers'], q['question_id'], expected_revision=2, expected_verdict_revision=1)
    assert result.status_code == 201, result.text
    assert result.json()['data']['verdict_history'][-1]['question_revision'] == 2
    with admin_engine.connect() as db:
        assert [(e['old_revision'], e['new_revision']) for e in events(db, q['question_id'])] == [(1, 2), (2, 3), (3, 4)]


@pytest.mark.parametrize('verdict,needs_review', [('correct', False), ('incorrect', False), ('unanswered', False),
    ('pending_review', True), ('unsupported', True)])
def test_guardian_verdict_states(readable_result, client, owner, verdict, needs_review):
    q = questions(client, owner, readable_result[3])[0]
    result = override(client, owner['headers'], q['question_id'], verdict=verdict)
    assert result.status_code == 201, result.text
    assert result.json()['data']['needs_guardian_review'] is needs_review


def test_concurrent_override_cas_and_stale_ocr_token(readable_result, client, owner, admin_engine):
    q = questions(client, owner, readable_result[3])[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda v: override(client, owner['headers'], q['question_id'], verdict=v), ['correct', 'incorrect']))
    assert sorted(r.status_code for r in responses) == [201, 409]
    assert len(view(client, owner, q['question_id'])['verdict_history']) == 1
    assert override(client, owner['headers'], q['question_id'], expected_revision=2, expected_verdict_revision=1).status_code == 409
    with admin_engine.connect() as db:
        assert len(events(db, q['question_id'])) == 1


def test_only_live_assigned_guardian_can_confirm(readable_result, client, owner, student, admin_engine):
    q = questions(client, owner, readable_result[3])[0]
    child = child_session(client, owner, student['id'])
    assert override(client, child, q['question_id']).status_code == 403
    assigned, user_id = invited_guardian(client, owner, [student['id']])
    assert override(client, assigned, q['question_id']).status_code == 201
    assert client.get(f"/api/v1/questions/{q['question_id']}", headers=child).json()['data']['verdict'] == 'correct'
    with admin_engine.begin() as db:
        db.execute(text('DELETE FROM student_access WHERE student_id=:student AND user_id=:user'), {'student': student['id'], 'user': user_id})
    assert override(client, assigned, q['question_id'], expected_verdict_revision=1).status_code == 404
    assert override(client, {}, q['question_id']).status_code == 401
    assert override(client, owner['headers'], uuid4()).status_code == 404


@pytest.mark.parametrize('change,status', [('consent', 403), ('epoch', 404), ('inactive', 404)])
def test_verdict_consent_epoch_fences(readable_result, client, owner, student, admin_engine, change, status):
    q = questions(client, owner, readable_result[3])[0]
    assignment = {'consent': 'consent_active=false', 'epoch': 'deletion_epoch=deletion_epoch+1', 'inactive': "status='disabled'"}[change]
    with admin_engine.begin() as db:
        db.execute(text(f'UPDATE students SET {assignment} WHERE id=:id'), {'id': student['id']})
    assert override(client, owner['headers'], q['question_id']).status_code == status
    with admin_engine.connect() as db:
        assert not events(db, q['question_id'])


@pytest.mark.parametrize('extra', [dict(expected_revision=True), dict(expected_revision=0), dict(expected_revision=2**31),
    dict(expected_verdict_revision=True), dict(expected_verdict_revision=-1), dict(expected_verdict_revision=2**31),
    dict(verdict='wrong'), dict(reason=''), dict(reason=' \n '), dict(reason='x'*1001), dict(reason='\x00'),
    dict(actor_id=str(uuid4())), dict(provenance='gold')])
def test_invalid_override_has_no_effect(readable_result, client, owner, extra):
    q = questions(client, owner, readable_result[3])[0]
    assert override(client, owner['headers'], q['question_id'], **extra).status_code == 422


def test_waiting_override_rechecks_consent(readable_result, client, owner, student, admin_engine):
    q = questions(client, owner, readable_result[3])[0]
    with ThreadPoolExecutor(max_workers=1) as pool:
        with admin_engine.begin() as db:
            blocker = db.execute(text('SELECT pg_backend_pid()')).scalar_one()
            db.execute(text('UPDATE students SET consent_active=false WHERE id=:id'), {'id': student['id']})
            future = pool.submit(override, client, owner['headers'], q['question_id'])
            wait_for_blocker(admin_engine, blocker)
            assert not future.done()
        assert future.result(timeout=5).status_code == 403
    with admin_engine.connect() as db:
        assert not events(db, q['question_id'])


@pytest.mark.parametrize('fail_at', ['event', 'audit'])
@pytest.mark.parametrize('operation', ['verdict', 'ocr'])
def test_failure_rolls_back_verdict_pointer_history_event_and_audit(readable_result, client, owner, admin_engine, fail_at, operation):
    q = questions(client, owner, readable_result[3])[0]
    if operation == 'ocr':
        q = override(client, owner['headers'], q['question_id']).json()['data']
    write = override if operation == 'verdict' else correction
    with admin_engine.connect() as db:
        before_events = events(db, q['question_id'])
        before_audits = db.execute(text('SELECT count(*) FROM audit_events WHERE resource_id=:id'), {'id': q['question_id']}).scalar_one()
    table = 'question_verdict_events' if fail_at == 'event' else 'audit_events'
    with admin_engine.begin() as db:
        db.execute(text("""CREATE FUNCTION public.reject_verdict_write() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'injected verdict failure'; END $$"""))
        db.execute(text(f'CREATE TRIGGER reject_verdict_write BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION public.reject_verdict_write()'))
    try:
        with pytest.raises(DBAPIError, match='injected verdict failure'):
            write(client, owner['headers'], q['question_id'])
        assert view(client, owner, q['question_id']) == q
        with admin_engine.connect() as db:
            assert events(db, q['question_id']) == before_events
            assert db.execute(text('SELECT count(*) FROM verdict_revisions WHERE question_id=:id'), {'id': q['question_id']}).scalar_one() == len(q['verdict_history'])
            assert db.execute(text('SELECT count(*) FROM question_revisions WHERE question_id=:id'), {'id': q['question_id']}).scalar_one() == len(q['history'])
            assert db.execute(text('SELECT count(*) FROM audit_events WHERE resource_id=:id'), {'id': q['question_id']}).scalar_one() == before_audits
    finally:
        with admin_engine.begin() as db:
            db.execute(text(f'DROP TRIGGER reject_verdict_write ON {table}; DROP FUNCTION public.reject_verdict_write()'))
    assert write(client, owner['headers'], q['question_id']).status_code == 201


def test_verdict_capability_is_guardian_only_and_tables_are_private(readable_result, client, owner, student, database):
    q = questions(client, owner, readable_result[3])[0]
    statement = text("SELECT public.override_question_verdict(:id,1,0,'correct','checked')")
    for key in ('url', 'worker_url', 'dispatcher_url'):
        engine = create_engine(database[key], hide_parameters=True)
        try:
            for table in ('verdict_revisions', 'question_verdict_events'):
                for sql in (f'SELECT * FROM {table}', f'DELETE FROM {table}', f"UPDATE {table} SET question_id='{uuid4()}'"):
                    with engine.begin() as db, pytest.raises(DBAPIError):
                        db.execute(text(sql))
            if key == 'url':
                with engine.begin() as db:
                    set_context(db, owner['tenant_id'])
                    assert db.execute(statement, {'id': q['question_id']}).scalar_one() == {'error': 'not_found'}
                    set_context(db, owner['tenant_id'], student_id=student['id'])
                    assert db.execute(statement, {'id': q['question_id']}).scalar_one() == {'error': 'not_found'}
            else:
                with engine.begin() as db, pytest.raises(DBAPIError):
                    db.execute(statement, {'id': q['question_id']})
        finally:
            engine.dispose()


def test_concurrent_ocr_and_confirmation_cannot_apply_to_wrong_text(readable_result, client, owner, admin_engine):
    q = questions(client, owner, readable_result[3])[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(override, client, owner['headers'], q['question_id'])
        b = pool.submit(correction, client, owner['headers'], q['question_id'])
        confirmed, edited = a.result(timeout=5), b.result(timeout=5)
    assert edited.status_code == 201
    assert confirmed.status_code in (201, 409)
    current = view(client, owner, q['question_id'])
    assert current['revision'] == 2 and current['verdict'] == 'pending_review'
    assert current['effective_verdict_revision'] is None
    assert len(current['verdict_history']) == int(confirmed.status_code == 201)
    with admin_engine.connect() as db:
        assert len(events(db, q['question_id'])) == 1 + int(confirmed.status_code == 201)


def test_unassigned_sibling_and_other_tenant_cannot_override(readable_result, client, owner, admin_engine, database):
    from test_identity_security import registered

    q = questions(client, owner, readable_result[3])[0]
    sibling = client.post('/api/v1/students', headers=owner['headers'], json={'nickname': '其他学生', 'grade': 3}).json()['data']
    other, other_id = invited_guardian(client, owner, [sibling['id']])
    assert override(client, other, q['question_id']).status_code == 404
    _, user = registered(client)
    response = client.post('/api/v1/tenants', headers={'Authorization': f"Bearer {user['access_token']}"}, json={'name': '独立家庭'})
    foreign = response.json()['data']
    assert override(client, {'Authorization': f"Bearer {foreign['access_token']}"}, q['question_id']).status_code == 404
    engine = create_engine(database['url'], hide_parameters=True)
    try:
        with engine.begin() as db:
            for tenant, actor in [(owner['tenant_id'], other_id), (foreign['tenant']['id'], user['user_id'])]:
                set_context(db, tenant, user_id=actor)
                result = db.execute(text("SELECT public.override_question_verdict(:id,1,0,'correct','checked')"),
                                    {'id': q['question_id']}).scalar_one()
                assert result == {'error': 'not_found'}
    finally:
        engine.dispose()
    with admin_engine.connect() as db:
        assert not events(db, q['question_id'])


@pytest.mark.parametrize('reason', [None, '', ' ', '\t\n', 'x'*1001, ' '*1000+'x'])
def test_database_override_rejects_invalid_reason(readable_result, client, owner, database, reason):
    q = questions(client, owner, readable_result[3])[0]
    actor_id = client.get('/api/v1/auth/me', headers=owner['headers']).json()['data']['user_id']
    engine = create_engine(database['url'], hide_parameters=True)
    try:
        with engine.begin() as db:
            set_context(db, owner['tenant_id'], user_id=actor_id)
            result = db.execute(text("SELECT public.override_question_verdict(:id,1,0,'correct',:reason)"),
                                {'id': q['question_id'], 'reason': reason}).scalar_one()
            assert result == {'error': 'invalid_override'}
    finally:
        engine.dispose()


@pytest.mark.parametrize('reason', ['x'*1000, '\t核对原图\n'])
def test_override_accepts_valid_reason_without_trimming(readable_result, client, owner, reason):
    q = questions(client, owner, readable_result[3])[0]
    response = override(client, owner['headers'], q['question_id'], reason=reason)
    assert response.status_code == 201, response.text
    assert response.json()['data']['verdict_history'][0]['reason'] == reason


def test_manual_changes_do_not_create_calls_jobs_or_quota_charges(readable_result, client, owner, admin_engine):
    lease, _, _, sid = readable_result
    q = questions(client, owner, sid)[0]
    before = snapshot(admin_engine, lease)
    statement = text('''SELECT s.source_revision,
        (SELECT count(*) FROM jobs j WHERE j.submission_id=s.id),
        (SELECT count(*) FROM model_runs m JOIN jobs j ON j.id=m.job_id WHERE j.submission_id=s.id)
        FROM submissions s WHERE s.id=:id''')
    with admin_engine.connect() as db:
        original = db.execute(statement, {'id': sid}).one()
    assert override(client, owner['headers'], q['question_id']).status_code == 201
    assert correction(client, owner['headers'], q['question_id']).status_code == 201
    assert snapshot(admin_engine, lease) == before
    with admin_engine.connect() as db:
        assert db.execute(statement, {'id': sid}).one() == original
