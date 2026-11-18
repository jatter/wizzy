"""Immutable OCR edits against isolated real PostgreSQL, not grading evidence."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_execution import execution as execution  # noqa: F401
from test_execution import wait_for_blocker
from test_result_read import child_session, invited_guardian
from test_result_read import readable_result as readable_result  # noqa: F401
from test_results import complete, document, finish, snapshot
from test_results import result_context as result_context  # noqa: F401


def questions(client, owner, submission_id):
    response = client.get(f'/api/v1/submissions/{submission_id}/questions', headers=owner['headers'])
    assert response.status_code == 200, response.text
    return response.json()['data']['questions']


def correction(client, headers, question, revision=1, **extra):
    return client.post(f"/api/v1/questions/{question}/ocr-corrections", headers=headers, json={
        'expected_revision': revision, 'stem': '12 + 8 = ?', 'student_answer_raw': '20',
        'reason': '核对原图，识别成了21', **extra})


def test_machine_questions_are_created_atomically_and_reads_are_stable(readable_result, client, owner, admin_engine):
    lease, _, _, submission_id = readable_result
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM questions WHERE submission_id=:id'), {'id': submission_id}).scalar_one() == 3
    rows = questions(client, owner, submission_id)
    assert [q['page_position'] for q in rows] == [1, 2, 3]
    assert len({q['question_id'] for q in rows}) == 3
    assert rows == questions(client, owner, submission_id)
    assert all(q['revision'] == 1 and q['student_answer_raw'] == '21' for q in rows)
    assert all(q['history'][0]['origin'] == 'machine' for q in rows)
    assert 'model_run_id' not in str(rows)
    assert client.get(f"/api/v1/questions/{rows[0]['question_id']}", headers=owner['headers']).json()['data'] == rows[0]


def test_guardian_edit_preserves_original_and_appends_immutable_history(readable_result, client, owner, admin_engine):
    _, _, _, submission_id = readable_result
    original = client.get(f'/api/v1/submissions/{submission_id}/result', headers=owner['headers']).json()
    q = questions(client, owner, submission_id)[0]
    edited = correction(client, owner['headers'], q['question_id'])
    assert edited.status_code == 201, edited.text
    view = edited.json()['data']
    assert view['revision'] == 2 and view['student_answer_raw'] == '20'
    assert view['verdict'] == 'pending_review' and view['needs_guardian_review'] is True
    assert view['machine_question']['student_answer_raw'] == '21'
    assert [r['revision'] for r in view['history']] == [1, 2]
    assert view['history'][1]['origin'] == 'guardian'
    assert view['history'][1]['reason'] == '核对原图，识别成了21'
    assert client.get(f'/api/v1/submissions/{submission_id}/result', headers=owner['headers']).json()['data'] == original['data']
    assert correction(client, owner['headers'], q['question_id']).status_code == 409
    assert correction(client, owner['headers'], q['question_id'], revision=2, student_answer_raw='19').status_code == 201
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM audit_events WHERE action='question.ocr_corrected' AND resource_id=:id"), {'id': q['question_id']}).scalar_one() == 2
        assert db.execute(text('SELECT count(*) FROM model_runs WHERE submission_id=:id'), {'id': submission_id}).scalar_one() == 1
        assert db.execute(text('SELECT consumed_pages FROM quota_reservations WHERE submission_id=:id'), {'id': submission_id}).scalar_one() == 3


def test_concurrent_cas_allows_exactly_one_edit(readable_result, client, owner):
    q = questions(client, owner, readable_result[3])[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda answer: correction(client, owner['headers'], q['question_id'], student_answer_raw=answer), ['20', '22']))
    assert sorted(r.status_code for r in replies) == [201, 409]
    view = client.get(f"/api/v1/questions/{q['question_id']}", headers=owner['headers']).json()['data']
    assert view['revision'] == 2 and len(view['history']) == 2


def test_only_assigned_guardian_can_edit_and_child_is_read_only(readable_result, client, owner, student):
    q = questions(client, owner, readable_result[3])[0]
    child = child_session(client, owner, student['id'])
    url = f"/api/v1/questions/{q['question_id']}"
    assert client.get(url, headers=child).status_code == 200
    assert correction(client, child, q['question_id']).status_code == 403
    assigned, _ = invited_guardian(client, owner, [student['id']])
    assert correction(client, assigned, q['question_id']).status_code == 201
    sibling = client.post('/api/v1/students', headers=owner['headers'], json={'nickname': '其他学生', 'grade': 3}).json()['data']
    other, _ = invited_guardian(client, owner, [sibling['id']])
    assert client.get(url, headers=other).status_code == 404
    assert correction(client, other, q['question_id']).status_code == 404
    assert client.get(url).status_code == 401
    assert correction(client, owner['headers'], uuid4()).status_code == 404


@pytest.mark.parametrize('change,expected', [('consent', 403), ('epoch', 404), ('inactive', 404)])
def test_current_consent_and_deletion_epoch_fence_edits(readable_result, client, owner, student, admin_engine, change, expected):
    q = questions(client, owner, readable_result[3])[0]
    assignments = {'consent': 'consent_active=false', 'epoch': 'deletion_epoch=deletion_epoch+1', 'inactive': "status='disabled'"}
    with admin_engine.begin() as db:
        db.execute(text(f"UPDATE students SET {assignments[change]} WHERE id=:id"), {'id': student['id']})
    assert correction(client, owner['headers'], q['question_id']).status_code == expected
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM question_revisions WHERE question_id=:id'), {'id': q['question_id']}).scalar_one() == 1


@pytest.mark.parametrize('extra', [dict(expected_revision=0), dict(expected_revision=True), dict(expected_revision=2**31), dict(stem=''),
    dict(stem=' '), dict(stem='x'*8001), dict(student_answer_raw='\x00'), dict(reason=''), dict(verdict='correct')])
def test_invalid_corrections_are_rejected(readable_result, client, owner, extra):
    q = questions(client, owner, readable_result[3])[0]
    assert correction(client, owner['headers'], q['question_id'], **extra).status_code == 422


def test_runtime_roles_cannot_mutate_history_or_bypass_guardian_scope(readable_result, client, owner, database):
    q = questions(client, owner, readable_result[3])[0]
    for url in (database['url'], database['worker_url'], database['dispatcher_url']):
        engine = create_engine(url)
        try:
            for statement in ('SELECT * FROM question_revisions', 'DELETE FROM question_revisions',
                              "UPDATE question_revisions SET stem='tamper'", 'DELETE FROM questions'):
                with engine.begin() as db, pytest.raises(DBAPIError):
                    db.execute(text(statement))
        finally:
            engine.dispose()
    engine = create_engine(database['url'])
    try:
        with engine.begin() as db:
            assert db.execute(text('SELECT public.read_question(:id)'), {'id': q['question_id']}).scalar_one() is None
            assert db.execute(text("SELECT public.correct_question_ocr(:id,1,'x','y','z')"), {'id': q['question_id']}).scalar_one() == {'error': 'not_found'}
    finally:
        engine.dispose()


@pytest.mark.parametrize('successes', [0, 2, 3])
def test_failed_pages_have_no_questions_and_repeated_settlement_is_idempotent(
        execution, result_context, client, owner, admin_engine, successes):
    lease, call, _ = result_context
    with admin_engine.connect() as db:
        submission_id = str(db.execute(text('SELECT submission_id FROM jobs WHERE id=:job_id'), lease).scalar_one())
    assert client.get(f'/api/v1/submissions/{submission_id}/questions', headers=owner['headers']).status_code == 409
    finish(execution, lease, call, outcome='succeeded' if successes else 'failed')
    result = document(call, succeeded=successes)
    if successes:
        # Existing conservative machine results permit whitespace, unlike guardian edits.
        result['pages'][0]['questions'][0]['stem'] = ' '
    assert complete(execution, lease, result)
    rows = questions(client, owner, submission_id)
    assert [q['page_position'] for q in rows] == list(range(1, successes + 1))
    assert complete(execution, lease, result)
    assert questions(client, owner, submission_id) == rows
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM questions WHERE submission_id=:id'), {'id': submission_id}).scalar_one() == successes
        assert db.execute(text('SELECT count(*) FROM question_revisions h JOIN questions q ON q.id=h.question_id WHERE q.submission_id=:id'), {'id': submission_id}).scalar_one() == successes


def test_waiting_correction_rechecks_live_consent(readable_result, client, owner, student, admin_engine):
    q = questions(client, owner, readable_result[3])[0]
    with ThreadPoolExecutor(max_workers=1) as pool:
        with admin_engine.begin() as db:
            blocker = db.execute(text('SELECT pg_backend_pid()')).scalar_one()
            db.execute(text('UPDATE students SET consent_active=false,deletion_epoch=deletion_epoch+1 WHERE id=:id'), {'id': student['id']})
            future = pool.submit(correction, client, owner['headers'], q['question_id'])
            wait_for_blocker(admin_engine, blocker)
            assert not future.done()
        response = future.result(timeout=5)
    assert response.status_code == 403, response.text
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT current_revision FROM questions WHERE id=:id'), {'id': q['question_id']}).scalar_one() == 1
        assert db.execute(text("SELECT count(*) FROM audit_events WHERE resource_id=:id AND action='question.ocr_corrected'"), {'id': q['question_id']}).scalar_one() == 0


def test_access_removal_and_other_tenant_cannot_read_or_correct(readable_result, client, owner, student, admin_engine):
    q = questions(client, owner, readable_result[3])[0]
    headers, user_id = invited_guardian(client, owner, [student['id']])
    with admin_engine.begin() as db:
        db.execute(text('DELETE FROM student_access WHERE student_id=:student AND user_id=:user'), {'student': student['id'], 'user': user_id})
    assert client.get(f"/api/v1/questions/{q['question_id']}", headers=headers).status_code == 404
    assert correction(client, headers, q['question_id']).status_code == 404
    from test_identity_security import registered

    _, user = registered(client)
    response = client.post('/api/v1/tenants', headers={'Authorization': f"Bearer {user['access_token']}"}, json={'name': '其他家庭'})
    other = {'Authorization': f"Bearer {response.json()['data']['access_token']}"}
    assert client.get(f"/api/v1/questions/{q['question_id']}", headers=other).status_code == 404
    assert correction(client, other, q['question_id']).status_code == 404


def test_database_correction_requires_live_guardian_principal(readable_result, client, owner, student, database, admin_engine):
    from wizzy.db import set_context

    q = questions(client, owner, readable_result[3])[0]
    engine = create_engine(database['url'], hide_parameters=True)
    query = text("SELECT public.correct_question_ocr(:id,1,'x','y','z')")
    try:
        with engine.begin() as db:
            set_context(db, owner['tenant_id'])
            assert db.execute(query, {'id': q['question_id']}).scalar_one() == {'error': 'not_found'}
            set_context(db, owner['tenant_id'], student_id=student['id'])
            assert db.execute(query, {'id': q['question_id']}).scalar_one() == {'error': 'not_found'}
        headers, user_id = invited_guardian(client, owner, [student['id']])
        with admin_engine.begin() as db:
            db.execute(text('DELETE FROM student_access WHERE student_id=:student AND user_id=:user'), {'student': student['id'], 'user': user_id})
        with engine.begin() as db:
            set_context(db, owner['tenant_id'], user_id=user_id)
            assert db.execute(query, {'id': q['question_id']}).scalar_one() == {'error': 'not_found'}
    finally:
        engine.dispose()
    for key in ('worker_url', 'dispatcher_url'):
        engine = create_engine(database[key], hide_parameters=True)
        try:
            for statement in ('SELECT public.read_question(:id)', 'SELECT public.question_submission(:id)',
                              'SELECT public.read_submission_questions(:id)', str(query)):
                with engine.begin() as db, pytest.raises(DBAPIError):
                    db.execute(text(statement), {'id': q['question_id']})
        finally:
            engine.dispose()


def test_question_materialization_rolls_back_with_result_transaction(execution, result_context, admin_engine):
    lease, call, _ = result_context
    finish(execution, lease, call)
    before = snapshot(admin_engine, lease)
    # Fail after the AFTER INSERT materializer has run; the whole completion must roll back.
    with admin_engine.begin() as db:
        db.execute(text("""CREATE FUNCTION public.reject_question_result() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.event_id='""" + str(lease['event_id']) + """'::uuid THEN
                    IF NOT EXISTS(SELECT 1 FROM public.questions WHERE result_id=NEW.id) THEN
                        RAISE EXCEPTION 'materializer did not run';
                    END IF;
                    RAISE EXCEPTION 'injected post-materialization failure';
                END IF;
                RETURN NEW;
            END $$"""))
        db.execute(text('CREATE TRIGGER zz_reject_question_result AFTER INSERT ON public.grading_results FOR EACH ROW EXECUTE FUNCTION public.reject_question_result()'))
    try:
        with pytest.raises(DBAPIError, match='injected post-materialization failure'):
            complete(execution, lease, document(call))
        assert snapshot(admin_engine, lease) == before
        with admin_engine.connect() as db:
            assert db.execute(text('SELECT count(*) FROM questions q JOIN jobs j ON j.submission_id=q.submission_id WHERE j.id=:job_id'), lease).scalar_one() == 0
    finally:
        with admin_engine.begin() as db:
            db.execute(text('DROP TRIGGER zz_reject_question_result ON public.grading_results'))
            db.execute(text('DROP FUNCTION public.reject_question_result()'))
    assert complete(execution, lease, document(call))
