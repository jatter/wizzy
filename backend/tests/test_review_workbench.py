"""Review drafts and unverified attempts on a disposable real PostgreSQL database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from test_correction_attempts import wrong_question as wrong_question  # noqa: F401
from test_execution import execution as execution  # noqa: F401
from test_question_revisions import correction, questions
from test_result_read import child_session, invited_guardian
from test_result_read import readable_result as readable_result  # noqa: F401
from test_results import result_context as result_context  # noqa: F401
from test_results import snapshot
from test_verdict_revisions import override

from wizzy.db import set_context
from wizzy.reviews import written


def enroll(client, headers, question_id, **extra):
    return client.post(f'/api/v1/questions/{question_id}/review-tasks', headers=headers,
                       json={'expected_effective_revision': 2, **extra})


def task_path(task_id):
    return f'/api/v1/review-tasks/{task_id}'


@pytest.fixture
def review_task(wrong_question, client, owner):
    response = enroll(client, owner['headers'], wrong_question['question_id'])
    assert response.status_code == 201, response.text
    return response.json()['data']


def test_enrollment_is_unique_and_uses_first_confirmed_local_date(wrong_question, client, owner, admin_engine):
    qid, headers = wrong_question['question_id'], owner['headers']
    # Deterministic UTC/local-date boundary, not the test runner's wall clock.
    with admin_engine.begin() as db:
        db.execute(text("UPDATE verdict_revisions SET created_at='2026-10-24T16:30:00Z' WHERE question_id=:id AND revision=1"), {'id': qid})
    response = enroll(client, headers, qid)
    assert response.status_code == 201, response.text
    task = response.json()['data']
    assert task['base_date'] == '2026-10-25' and task['due_date'] == '2026-10-26'
    assert task['timezone'] == 'Asia/Shanghai' and task['step'] == 1
    assert task['state'] == 'open' and task['source_is_current'] is True
    assert task['draft'] == {'revision': 0, 'student_answer': ''}
    assert task['attempts'] == [] and task['reschedules'] == []
    assert task['schedule_revision'] == 0
    assert enroll(client, headers, qid).json()['data'] == task
    assert client.get(task_path(task['id']), headers=headers).json()['data'] == task
    assert not ({'student_answer_raw', 'machine_question', 'reference_answer', 'feedback'} & set(task))


@pytest.mark.parametrize('method', ['patch', 'post'])
def test_child_draft_survives_reload_without_completion(review_task, client, owner, student, admin_engine, method):
    headers = child_session(client, owner, student['id'])
    path = task_path(review_task['id'])
    response = client.request(method, path+'/draft', headers=headers, json={
        'expected_draft_revision': 0, 'student_answer': '先计算括号中的数字'})
    assert response.status_code == 200, response.text
    task = response.json()['data']
    assert task['draft'] == {'revision': 1, 'student_answer': '先计算括号中的数字'}
    assert task['state'] == 'open' and task['attempts'] == []
    assert client.get(path, headers=headers).json()['data'] == task
    assert client.request(method, path+'/draft', headers=headers, json={
        'expected_draft_revision': 0, 'student_answer': '过期草稿'}).status_code == 409
    assert client.request(method, path+'/draft', headers=owner['headers'], json={
        'expected_draft_revision': 1, 'student_answer': '家长代写'}).status_code == 403
    assert client.request(method, path+'/draft', json={
        'expected_draft_revision': 1, 'student_answer': '未登录'}).status_code == 401
    sibling = client.post('/api/v1/students', headers=owner['headers'], json={'nickname': '弟弟', 'grade': 2}).json()['data']
    consent = client.post('/api/v1/consents', headers=owner['headers'], json={
        'student_id': sibling['id'], 'policy_version': '2026-10-25',
        'child_rules_version': '2026-10-25', 'granted': True})
    assert consent.status_code == 201, consent.text
    sibling_headers = child_session(client, owner, sibling['id'])
    assert client.request(method, path+'/draft', headers=sibling_headers, json={
        'expected_draft_revision': 1, 'student_answer': '其他孩子'}).status_code == 404
    assert client.get(path, headers=headers).json()['data'] == task
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM audit_events WHERE resource_id=:id AND action='review.draft_saved'"),
                          {'id': review_task['id']}).scalar_one() == 1


@pytest.fixture
def child_headers(client, owner, student):
    return child_session(client, owner, student['id'])


def save(client, headers, task_id, **extra):
    return client.patch(task_path(task_id)+'/draft', headers=headers,
                        json={'expected_draft_revision': 0, 'student_answer': '20', **extra})


def attempt(client, headers, task_id, key=None, **extra):
    return client.post(task_path(task_id)+'/attempts',
                       headers=headers | {'Idempotency-Key': str(key or uuid4())},
                       json={'expected_draft_revision': 0, 'outcome': 'needs_help', **extra})


def reschedule(client, headers, task_id, key=None, **extra):
    return client.post(task_path(task_id)+'/reschedules',
                       headers=headers | {'Idempotency-Key': str(key or uuid4())},
                       json={'expected_schedule_revision': 0, 'due_date': '2026-10-28',
                             'reason': '调整复习时间', **extra})


def listing(client, headers, student_id, **params):
    return client.get(f'/api/v1/students/{student_id}/review-tasks', headers=headers, params=params)


def test_help_skip_and_submission_are_pending_not_mastery(review_task, child_headers, client, owner, wrong_question, admin_engine, readable_result):
    tid, qid, h = review_task['id'], wrong_question['question_id'], owner['headers']
    source = client.get(f'/api/v1/questions/{qid}', headers=h).json()['data']
    before = snapshot(admin_engine, readable_result[0])
    assert attempt(client, child_headers, tid, outcome='submitted').status_code == 422
    assert save(client, child_headers, tid).status_code == 200
    helped = attempt(client, child_headers, tid, expected_draft_revision=1).json()['data']
    assert helped['draft'] == {'revision': 2, 'student_answer': '20'}
    skipped = attempt(client, child_headers, tid, expected_draft_revision=2, outcome='skipped').json()['data']
    assert skipped['state'] == 'open' and skipped['draft']['revision'] == 3
    assert save(client, child_headers, tid, expected_draft_revision=3, student_answer='独立重算20').status_code == 200
    submitted = attempt(client, child_headers, tid, expected_draft_revision=4, outcome='submitted').json()['data']
    assert submitted['state'] == 'awaiting_review' and submitted['step'] == 1
    assert submitted['draft'] == {'revision': 5, 'student_answer': '独立重算20'}
    assert [a['outcome'] for a in submitted['attempts']] == ['needs_help', 'skipped', 'submitted']
    assert [a['student_answer'] for a in submitted['attempts']] == ['20', '20', '独立重算20']
    assert all(a['verdict'] == 'pending_review' and a['independent_correct'] is False for a in submitted['attempts'])
    assert not ({'completed', 'mastered', 'correct_count', 'reference_answer', 'feedback'} & set(submitted))
    assert save(client, child_headers, tid, expected_draft_revision=5).status_code == 409
    assert attempt(client, child_headers, tid, expected_draft_revision=5).status_code == 409
    assert reschedule(client, h, tid).status_code == 409
    assert client.get(task_path(tid), headers=child_headers).json()['data'] == submitted
    assert client.get(f'/api/v1/questions/{qid}', headers=h).json()['data'] == source
    assert snapshot(admin_engine, readable_result[0]) == before


def test_draft_cas_clear_and_concurrent_devices(review_task, client, child_headers):
    tid = review_task['id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda answer: save(client, child_headers, tid, student_answer=answer), ['19', '20']))
    assert sorted(r.status_code for r in replies) == [200, 409]
    assert save(client, child_headers, tid, expected_draft_revision=1, student_answer='').json()['data']['draft'] == {
        'revision': 2, 'student_answer': ''}
    assert attempt(client, child_headers, tid, expected_draft_revision=2, outcome='submitted').status_code == 422


@pytest.mark.parametrize('extra', [dict(expected_draft_revision=True), dict(expected_draft_revision=-1),
    dict(expected_draft_revision=2**31-1), dict(student_answer=None), dict(student_answer='x'*8001),
    dict(student_answer='\x00'), dict(mastered=True)])
def test_invalid_draft(review_task, client, child_headers, extra):
    assert save(client, child_headers, review_task['id'], **extra).status_code == 422


@pytest.mark.parametrize('extra', [dict(expected_draft_revision=True), dict(expected_draft_revision=-1),
    dict(outcome='correct'), dict(independent_correct=True)])
def test_invalid_attempt(review_task, client, child_headers, extra):
    assert attempt(client, child_headers, review_task['id'], **extra).status_code == 422


@pytest.mark.parametrize('extra', [dict(expected_schedule_revision=True), dict(expected_schedule_revision=-1),
    dict(due_date='bad'), dict(reason=' \t'), dict(reason='x'*1001), dict(reason='\x00')])
def test_invalid_reschedule(review_task, client, owner, extra):
    assert reschedule(client, owner['headers'], review_task['id'], **extra).status_code == 422


def test_role_separation_and_live_assignment(review_task, client, owner, student, child_headers, admin_engine):
    tid, qid, h = review_task['id'], review_task['question_id'], owner['headers']
    assert save(client, h, tid).status_code == 403
    assert attempt(client, h, tid).status_code == 403
    assert enroll(client, child_headers, qid).status_code == 403
    assert reschedule(client, child_headers, tid).status_code == 403
    sibling = client.post('/api/v1/students', headers=h, json={'nickname': '另一个孩子', 'grade': 3}).json()['data']
    assert client.post('/api/v1/consents', headers=h, json={'student_id': sibling['id'], 'policy_version': '2026-10-25',
        'child_rules_version': '2026-10-25', 'granted': True}).status_code == 201
    other, _ = invited_guardian(client, owner, [sibling['id']])
    assigned, user = invited_guardian(client, owner, [student['id']])
    assert client.get(task_path(tid), headers=assigned).status_code == 200
    assert reschedule(client, assigned, tid).status_code == 201
    with admin_engine.begin() as db:
        db.execute(text('DELETE FROM student_access WHERE student_id=:student AND user_id=:user'),
                   {'student': student['id'], 'user': user})
    for denied in (other, assigned):
        assert client.get(task_path(tid), headers=denied).status_code == 404
        assert listing(client, denied, student['id']).status_code == 404
        assert reschedule(client, denied, tid).status_code == 404
    consent = client.post('/api/v1/consents', headers=owner['headers'], json={
        'student_id': sibling['id'], 'policy_version': '2026-10-25',
        'child_rules_version': '2026-10-25', 'granted': True})
    assert consent.status_code == 201, consent.text
    sibling_headers = child_session(client, owner, sibling['id'])
    assert client.get(task_path(tid), headers=sibling_headers).status_code == 404
    assert save(client, sibling_headers, tid).status_code == 404
    assert client.get(task_path(tid)).status_code == 401
    assert client.get(task_path(uuid4()), headers=h).status_code == 404


def test_attempt_idempotency_returns_current_projection(review_task, client, child_headers, owner, admin_engine):
    tid, key = review_task['id'], uuid4()
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: attempt(client, child_headers, tid, key=key), range(2)))
    assert [r.status_code for r in replies] == [201, 201]
    assert replies[0].json()['data'] == replies[1].json()['data']
    assert attempt(client, child_headers, tid, key=key, outcome='skipped').status_code == 409
    assert save(client, child_headers, tid, expected_draft_revision=1).status_code == 200
    current = client.get(task_path(tid), headers=child_headers).json()['data']
    assert attempt(client, child_headers, tid, key=key).json()['data'] == current
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM audit_events WHERE resource_id=:id AND action='review.attempted'"), {'id': tid}).scalar_one() == 1


def test_different_attempt_keys_same_version_only_one_wins(review_task, client, child_headers):
    tid = review_task['id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: attempt(client, child_headers, tid), range(2)))
    assert sorted(r.status_code for r in replies) == [201, 409]
    assert len(client.get(task_path(tid), headers=child_headers).json()['data']['attempts']) == 1


def test_reschedule_history_past_date_idempotency_and_cas(review_task, client, owner, admin_engine):
    tid, key, h = review_task['id'], uuid4(), owner['headers']
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: reschedule(client, h, tid, key=key, due_date='2000-01-01'), range(2)))
    assert [r.status_code for r in replies] == [201, 201]
    first = replies[0].json()['data']
    assert first == replies[1].json()['data'] and first['due_date'] == '2000-01-01'
    assert first['reschedules'][0]['previous_due_date'] == review_task['due_date']
    assert reschedule(client, h, tid, key=key).status_code == 409
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda date: reschedule(client, h, tid, expected_schedule_revision=1, due_date=date), ['2026-11-01', '2026-11-02']))
    assert sorted(r.status_code for r in replies) == [201, 409]
    current = client.get(task_path(tid), headers=h).json()['data']
    assert current['schedule_revision'] == 2 and len(current['reschedules']) == 2
    assert reschedule(client, h, tid, key=key, due_date='2000-01-01').json()['data'] == current
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM audit_events WHERE resource_id=:id AND action='review.rescheduled'"), {'id': tid}).scalar_one() == 2


@pytest.mark.parametrize('source_change', ['ocr', 'verdict'])
def test_old_source_history_survives_and_replays_but_new_writes_fail(review_task, client, owner, child_headers, student, source_change):
    tid, qid, h, key, rkey = review_task['id'], review_task['question_id'], owner['headers'], uuid4(), uuid4()
    assert attempt(client, child_headers, tid, key=key).status_code == 201
    assert reschedule(client, h, tid, key=rkey).status_code == 201
    if source_change == 'ocr':
        assert correction(client, h, qid).status_code == 201
    else:
        assert override(client, h, qid, expected_verdict_revision=1, verdict='correct').status_code == 201
    old = client.get(task_path(tid), headers=h).json()['data']
    assert old['source_is_current'] is False and len(old['attempts']) == len(old['reschedules']) == 1
    assert listing(client, h, student['id']).json()['data']['items'] == []
    assert attempt(client, child_headers, tid, key=key).json()['data'] == old
    assert reschedule(client, h, tid, key=rkey).json()['data'] == old
    assert save(client, child_headers, tid, expected_draft_revision=1).status_code == 409
    assert attempt(client, child_headers, tid, expected_draft_revision=1).status_code == 409
    assert reschedule(client, h, tid, expected_schedule_revision=1).status_code == 409
    assert enroll(client, h, qid).status_code == 409
    assert override(client, h, qid, expected_revision=2 if source_change == 'ocr' else 1,
                    expected_verdict_revision=1 if source_change == 'ocr' else 2, verdict='incorrect').status_code == 201
    new = enroll(client, h, qid, expected_effective_revision=4).json()['data']
    assert new['id'] != tid and new['source_is_current'] is True
    assert [t['id'] for t in listing(client, h, student['id']).json()['data']['items']] == [new['id']]


@pytest.mark.parametrize('change,expected', [('consent', 404), ('epoch', 404), ('inactive', 404), ('invalid_result', 404)])
def test_replay_rechecks_authority(review_task, client, owner, student, admin_engine, change, expected):
    tid, h, key = review_task['id'], owner['headers'], uuid4()
    assert reschedule(client, h, tid, key=key).status_code == 201
    with admin_engine.begin() as db:
        if change == 'invalid_result':
            db.execute(text('UPDATE grading_results SET source_revision=source_revision+1 WHERE id=(SELECT result_id FROM questions WHERE id=:id)'), {'id': review_task['question_id']})
        else:
            assignments = {'consent': 'consent_active=false', 'epoch': 'deletion_epoch=deletion_epoch+1', 'inactive': "status='disabled'"}
            db.execute(text(f'UPDATE students SET {assignments[change]} WHERE id=:id'), {'id': student['id']})
    assert reschedule(client, h, tid, key=key).status_code == expected
    assert client.get(task_path(tid), headers=h).status_code == expected
    # Task lookups deliberately hide revoked content; student lists report consent explicitly.
    assert listing(client, h, student['id']).status_code == (403 if change == 'consent' else 404 if change == 'inactive' else 200)


def test_enrollment_requires_guardian_confirmed_incorrect(readable_result, client, owner):
    qid, h = questions(client, owner, readable_result[3])[0]['question_id'], owner['headers']
    assert enroll(client, h, qid, expected_effective_revision=1).status_code == 409
    for revision, verdict in enumerate(('correct', 'pending_review', 'unanswered', 'unsupported'), start=1):
        assert override(client, h, qid, expected_verdict_revision=revision-1, verdict=verdict).status_code == 201
        assert enroll(client, h, qid, expected_effective_revision=revision+1).status_code == 409
    assert enroll(client, h, qid, expected_effective_revision=True).status_code == 422


def test_concurrent_enrollment_is_unique_and_audited_once(wrong_question, client, owner, admin_engine):
    qid, h = wrong_question['question_id'], owner['headers']
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: enroll(client, h, qid), range(2)))
    assert [r.status_code for r in replies] == [201, 201]
    assert replies[0].json()['data'] == replies[1].json()['data']
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM review_tasks WHERE question_id=:id'), {'id': qid}).scalar_one() == 1
        assert db.execute(text("SELECT count(*) FROM audit_events WHERE resource_id=:id AND action='review.enrolled'"), {'id': replies[0].json()['data']['id']}).scalar_one() == 1


def test_listing_equal_timestamps_due_filter_and_pagination(wrong_question, readable_result, client, owner, student, admin_engine):
    h = owner['headers']
    ids = [enroll(client, h, wrong_question['question_id']).json()['data']['id']]
    for q in questions(client, owner, readable_result[3])[1:]:
        assert override(client, h, q['question_id'], verdict='incorrect').status_code == 201
        ids.append(enroll(client, h, q['question_id']).json()['data']['id'])
    with admin_engine.begin() as db:
        db.execute(text("UPDATE review_tasks SET created_at='2026-10-25T00:00:00Z',due_date='9999-01-01' WHERE student_id=:id"), {'id': student['id']})
        today = db.execute(text("SELECT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Shanghai')::date")).scalar_one()
    assert listing(client, h, student['id'], due_only=True).json()['data']['items'] == []
    assert reschedule(client, h, ids[0], due_date=today.isoformat()).status_code == 201
    assert [t['id'] for t in listing(client, h, student['id'], due_only=True).json()['data']['items']] == ids[:1]
    found, cursor = [], None
    for _ in ids:
        value = listing(client, h, student['id'], limit=1, **({'cursor': cursor} if cursor else {})).json()['data']
        found += [t['id'] for t in value['items']]
        cursor = value['next_cursor']
    assert found == sorted(ids) and cursor is None
    assert listing(client, h, student['id'], cursor='invalid').status_code == 422
    assert listing(client, h, student['id'], limit=101).status_code == 422


def test_empty_list_and_missing_keys(review_task, client, owner, child_headers, student):
    tid, h = review_task['id'], owner['headers']
    assert listing(client, child_headers, student['id']).status_code == 200
    for suffix, headers, body in [('attempts', child_headers, {'expected_draft_revision': 0, 'outcome': 'skipped'}),
            ('reschedules', h, {'expected_schedule_revision': 0, 'due_date': '2026-11-01', 'reason': '改期'})]:
        for key in (None, 'invalid'):
            assert client.post(task_path(tid)+'/'+suffix, headers=headers | ({'Idempotency-Key': key} if key else {}), json=body).status_code == 422


def test_audit_failure_rolls_back_all_review_writes(wrong_question, client, owner, child_headers, monkeypatch):
    import wizzy.reviews as module

    h, qid = owner['headers'], wrong_question['question_id']

    def fail(*args, **kwargs):
        raise RuntimeError('injected review audit failure')

    original = module.audit
    monkeypatch.setattr(module, 'audit', fail)
    with pytest.raises(RuntimeError, match='injected review audit failure'):
        enroll(client, h, qid)
    monkeypatch.setattr(module, 'audit', original)
    task = enroll(client, h, qid).json()['data']
    monkeypatch.setattr(module, 'audit', fail)
    for operation in (lambda: save(client, child_headers, task['id']), lambda: attempt(client, child_headers, task['id']),
                      lambda: reschedule(client, h, task['id'])):
        with pytest.raises(RuntimeError, match='injected review audit failure'):
            operation()
        assert client.get(task_path(task['id']), headers=h).json()['data'] == task


def test_sql_capabilities_recheck_principal_and_parameters(review_task, client, owner, student, database):
    tid, qid, h = review_task['id'], review_task['question_id'], owner['headers']
    actor = client.get('/api/v1/auth/me', headers=h).json()['data']['user_id']
    engine = create_engine(database['url'], hide_parameters=True)
    calls = [
        ('public.create_review_task(:id,:version)', {'id': qid, 'version': 2}, [{'version': None}, {'version': 0}]),
        ('public.save_review_draft(:id,:version,:answer)', {'id': tid, 'version': 0, 'answer': '20'},
         [{'version': -1}, {'version': None}, {'answer': None}, {'answer': 'x'*8001}]),
        ('public.submit_review_attempt(:id,:version,:outcome,:key)', {'id': tid, 'version': 0, 'outcome': 'skipped', 'key': uuid4()},
         [{'version': -1}, {'key': None}, {'outcome': None}, {'outcome': 'correct'}]),
        ('public.reschedule_review_task(:id,:version,:date,:reason,:key)', {'id': tid, 'version': 0, 'date': '2026-11-01', 'reason': '改期', 'key': uuid4()},
         [{'version': -1}, {'date': None}, {'reason': ' \t'}, {'reason': None}, {'key': None}]),
    ]
    try:
        for index, (function, args, invalid) in enumerate(calls):
            with engine.begin() as db:
                statement = text('SELECT '+function)
                assert db.execute(statement, args).scalar_one() == {'error': 'not_found'}
                is_child = index in (1, 2)
                set_context(db, owner['tenant_id'], student_id=student['id'] if not is_child else None,
                            user_id=actor if is_child else None)
                assert db.execute(statement, args).scalar_one() == {'error': 'not_found'}
                set_context(db, owner['tenant_id'], student_id=student['id'] if is_child else None,
                            user_id=None if is_child else actor)
                for extra in invalid:
                    assert db.execute(statement, args | extra).scalar_one() == {'error': 'invalid_review'}
    finally:
        engine.dispose()


@pytest.mark.parametrize('fraction', ['', '.1', '.12', '.123', '.1234', '.12345', '.123456'])
def test_task_and_history_timestamps_share_serialization(fraction):
    timestamp = f'2026-10-25T12:55:48{fraction}+00:00'
    request = SimpleNamespace(state=SimpleNamespace(request_id='req_timestamp'))
    task = {'created_at': timestamp, 'attempts': [{'created_at': timestamp}], 'reschedules': [{'created_at': timestamp}]}
    view = written(None, request, None, {'created': False, 'task': task}, 'review.attempted')['data']
    expected = datetime.fromisoformat(timestamp).isoformat()
    assert view['created_at'] == view['attempts'][0]['created_at'] == view['reschedules'][0]['created_at'] == expected
