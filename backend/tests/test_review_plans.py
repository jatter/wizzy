"""Shared bounded plans on real PostgreSQL; no duplicate completion counters."""
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_execution import execution as execution  # noqa: F401
from test_learning_reports import generate as report
from test_learning_reports import report_result as report_result  # noqa: F401
from test_question_revisions import questions
from test_result_read import child_session
from test_result_read import readable_result as readable_result  # noqa: F401
from test_results import result_context as result_context  # noqa: F401
from test_review_workbench import attempt, enroll, save
from test_verdict_revisions import override


def generate(client, headers, student_id, key=None, **extra):
    return client.post('/api/v1/review-plans', headers=headers | {
        'Idempotency-Key': str(key or uuid4())}, json={
            'student_id': student_id, 'type': 'weekly', 'period_start': '2026-10-26', **extra})


def path(identifier):
    return '/api/v1/review-plans/'+str(identifier)


@pytest.fixture
def plan_tasks(readable_result, client, owner, admin_engine):
    tasks = []
    for q in questions(client, owner, readable_result[3]):
        qid = q['question_id']
        assert override(client, owner['headers'], qid, verdict='incorrect').status_code == 201
        response = enroll(client, owner['headers'], qid)
        assert response.status_code == 201, response.text
        tasks.append(response.json()['data'])
    with admin_engine.begin() as db:
        db.execute(text("UPDATE review_tasks SET base_date='2026-10-20',due_date='2026-10-21' WHERE student_id=:id"),
                   {'id': tasks[0]['student_id']})
    return tasks


def test_empty_plan_is_honest_persistent_and_idempotent(client, owner, student, admin_engine):
    key = uuid4()
    response = generate(client, owner['headers'], student['id'], key)
    assert response.status_code == 201, response.text
    plan = response.json()['data']
    assert plan['entries'] == [] and plan['question_count'] == 0 and plan['estimated_minutes'] == 0
    assert plan['limits'] == {'max_questions': 20, 'max_minutes': 20, 'minutes_per_question': 2}
    assert plan['timezone'] == 'Asia/Shanghai' and plan['stale'] is False
    assert generate(client, owner['headers'], student['id'], key).json()['data'] == plan
    assert client.get(path(plan['id']), headers=owner['headers']).json()['data'] == plan
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM audit_events WHERE resource_id=:id AND action='review_plan.generated'"),
                          {'id': plan['id']}).scalar_one() == 1


def test_week_and_month_reference_one_task_and_live_pending_state(plan_tasks, client, owner, student, admin_engine):
    headers = owner['headers']
    weekly = generate(client, headers, student['id']).json()['data']
    monthly = generate(client, headers, student['id'], type='monthly', period_start='2026-10-01').json()['data']
    assert {e['task_id'] for e in weekly['entries']} == {t['id'] for t in plan_tasks}
    assert {e['task_id'] for e in monthly['entries']} == {t['id'] for t in plan_tasks}
    child = child_session(client, owner, student['id'])
    tid = weekly['entries'][0]['task_id']
    assert save(client, child, tid).status_code == 200
    assert attempt(client, child, tid, expected_draft_revision=1, outcome='submitted').status_code == 201
    for p in (weekly, monthly):
        view = client.get(path(p['id']), headers=child).json()['data']
        entry = next(e for e in view['entries'] if e['task_id'] == tid)
        assert entry['task']['state'] == 'awaiting_review'
        assert len(entry['task']['attempts']) == 1
        assert 'completed' not in view and 'mastered' not in view
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM review_attempts WHERE task_id=:id'), {'id': tid}).scalar_one() == 1
        assert db.execute(text('SELECT count(*) FROM review_tasks WHERE student_id=:id'), {'id': student['id']}).scalar_one() == len(plan_tasks)


def test_question_and_time_limits_both_apply(plan_tasks, client, owner, student):
    for limits in ({'max_questions': 1}, {'max_minutes': 3}, {'max_questions': 4, 'max_minutes': 2}):
        response = generate(client, owner['headers'], student['id'], **limits)
        assert response.status_code == 201, response.text
        plan = response.json()['data']
        assert plan['question_count'] == 1 and plan['estimated_minutes'] == 2
    tiny = generate(client, owner['headers'], student['id'], max_minutes=1).json()['data']
    assert tiny['entries'] == []


def test_idempotency_key_cannot_change_limits(client, owner, student):
    key = uuid4()
    assert generate(client, owner['headers'], student['id'], key).status_code == 201
    response = generate(client, owner['headers'], student['id'], key, max_questions=5)
    assert response.status_code == 409 and response.json()['error']['code'] == 'idempotency_conflict'


def test_report_can_enroll_current_wrong_questions_once(report_result, client, owner, student, admin_engine):
    qid = questions(client, owner, report_result[3])[0]['question_id']
    assert override(client, owner['headers'], qid, verdict='incorrect').status_code == 201
    response = report(client, owner['headers'], student['id'])
    assert response.status_code == 201, response.text
    rid = response.json()['data']['id']
    # Keep enrollment's first confirmed incorrect date deterministic.
    with admin_engine.begin() as db:
        db.execute(text("UPDATE verdict_revisions SET created_at='2026-10-20T01:00:00Z' WHERE question_id=:id"), {'id': qid})
    response = generate(client, owner['headers'], student['id'], report_id=rid)
    assert response.status_code == 201, response.text
    plan = response.json()['data']
    assert plan['report_id'] == rid and plan['question_count'] == 1
    assert plan['entries'][0]['task']['question_id'] == qid
    again = generate(client, owner['headers'], student['id'], report_id=rid).json()['data']
    assert again['entries'][0]['task_id'] == plan['entries'][0]['task_id']


@pytest.mark.parametrize('extra', [
    {'max_questions': 0}, {'max_questions': 101}, {'max_questions': True},
    {'max_minutes': 0}, {'max_minutes': 121}, {'minutes_per_question': 31},
    {'period_start': '2026-10-27'}, {'type': 'monthly', 'period_start': '2026-10-02'},
    {'period_start': '9999-12-27'}, {'unexpected': 1},
])
def test_invalid_plan_inputs(client, owner, student, extra):
    assert generate(client, owner['headers'], student['id'], **extra).status_code == 422


def test_priority_uses_verified_errors_after_overdue_tasks(plan_tasks, client, owner, student, admin_engine):
    from test_correction_attempts import judge, submit

    h = owner['headers']
    for task, count in zip(plan_tasks, (1, 2, 0), strict=True):
        for _ in range(count):
            response = submit(client, h, task['question_id'])
            assert response.status_code == 201, response.text
            assert judge(client, h, task['question_id'], response.json()['data']['id'],
                         verdict='incorrect').status_code == 201
    with admin_engine.begin() as db:
        for task in plan_tasks[:2]:
            db.execute(text("UPDATE review_tasks SET due_date='2026-10-28' WHERE id=:id"), {'id': task['id']})
    plan = generate(client, h, student['id']).json()['data']
    assert [e['task_id'] for e in plan['entries']] == [plan_tasks[i]['id'] for i in (2, 1, 0)]
    assert [e['priority'] for e in plan['entries']] == ['overdue', 'repeated_error', 'remaining']
    assert [e['confirmed_incorrect_attempts'] for e in plan['entries']] == [0, 2, 1]
    limited = generate(client, h, student['id'], max_questions=2).json()['data']
    assert limited['question_count'] == 2
    assert [e['task_id'] for e in limited['entries']] == [plan_tasks[i]['id'] for i in (2, 1)]


def test_future_and_already_submitted_tasks_are_not_selected(plan_tasks, client, owner, student, admin_engine):
    child = child_session(client, owner, student['id'])
    assert save(client, child, plan_tasks[0]['id']).status_code == 200
    assert attempt(client, child, plan_tasks[0]['id'], expected_draft_revision=1, outcome='submitted').status_code == 201
    with admin_engine.begin() as db:
        db.execute(text("UPDATE review_tasks SET due_date='2026-11-02' WHERE id=:id"), {'id': plan_tasks[1]['id']})
    plan = generate(client, owner['headers'], student['id']).json()['data']
    assert [e['task_id'] for e in plan['entries']] == [plan_tasks[2]['id']]


@pytest.mark.parametrize('change', ['ocr', 'verdict', 'reschedule'])
def test_source_changes_mark_existing_plan_stale(plan_tasks, client, owner, student, change):
    from test_question_revisions import correction
    from test_review_workbench import reschedule

    h, task = owner['headers'], plan_tasks[0]
    key = uuid4()
    plan = generate(client, h, student['id'], key).json()['data']
    if change == 'ocr':
        response = correction(client, h, task['question_id'])
    elif change == 'verdict':
        response = override(client, h, task['question_id'], expected_verdict_revision=1)
    else:
        response = reschedule(client, h, task['id'])
    assert response.status_code == 201, response.text
    view = client.get(path(plan['id']), headers=h).json()['data']
    assert view['stale'] is True
    assert [e['task_id'] for e in view['entries']] == [e['task_id'] for e in plan['entries']]
    assert generate(client, h, student['id'], key).json()['data'] == view


def test_stale_or_foreign_report_cannot_seed_new_plan(report_result, client, owner, student):
    from test_question_revisions import correction

    h = owner['headers']
    qid = questions(client, owner, report_result[3])[0]['question_id']
    assert override(client, h, qid, verdict='incorrect').status_code == 201
    rid = report(client, h, student['id']).json()['data']['id']
    plan = generate(client, h, student['id'], report_id=rid).json()['data']
    assert correction(client, h, qid).status_code == 201
    assert client.get(path(plan['id']), headers=h).json()['data']['stale'] is True
    response = generate(client, h, student['id'], report_id=rid)
    assert response.status_code == 409 and response.json()['error']['code'] == 'source_conflict'
    sibling = client.post('/api/v1/students', headers=h, json={'nickname': '另一学生', 'grade': 3}).json()['data']
    assert generate(client, h, student['id'], report_id=str(uuid4())).status_code == 404
    assert generate(client, h, sibling['id'], report_id=rid).status_code == 404


def test_only_assigned_guardians_and_bound_child_can_read(client, owner, student):
    from test_result_read import invited_guardian

    h = owner['headers']
    plan = generate(client, h, student['id']).json()['data']
    assigned, _ = invited_guardian(client, owner, [student['id']])
    sibling = client.post('/api/v1/students', headers=h, json={'nickname': '兄弟姐妹', 'grade': 3}).json()['data']
    unassigned, _ = invited_guardian(client, owner, [sibling['id']])
    child = child_session(client, owner, student['id'])
    for headers, expected in [(assigned, 200), (unassigned, 404), (child, 200), ({}, 401)]:
        assert client.get(path(plan['id']), headers=headers).status_code == expected
    assert generate(client, child, student['id']).status_code == 403
    assert generate(client, unassigned, student['id']).status_code == 404
    assert client.get(path(uuid4()), headers=h).status_code == 404


@pytest.mark.parametrize('field', ['type', 'period_start', 'max_questions', 'max_minutes',
                                  'minutes_per_question', 'report_id'])
def test_idempotency_binds_all_generation_inputs(client, owner, student, field):
    key = uuid4()
    assert generate(client, owner['headers'], student['id'], key).status_code == 201
    changed = {'type': 'monthly', 'period_start': '2026-11-02', 'max_questions': 3,
               'max_minutes': 5, 'minutes_per_question': 3, 'report_id': str(uuid4())}
    extra = {field: changed[field]}
    if field == 'type':
        extra['period_start'] = '2026-10-01'
    response = generate(client, owner['headers'], student['id'], key, **extra)
    assert response.status_code == 409 and response.json()['error']['code'] == 'idempotency_conflict'


def test_parallel_idempotent_generation_creates_one_plan_and_audit(plan_tasks, client, owner, student, admin_engine):
    from concurrent.futures import ThreadPoolExecutor

    key = uuid4()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: generate(client, owner['headers'], student['id'], key), range(2)))
    assert [r.status_code for r in responses] == [201, 201]
    assert responses[0].json()['data'] == responses[1].json()['data']
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM review_plans WHERE student_id=:id'),
                          {'id': student['id']}).scalar_one() == 1
        assert db.execute(text("SELECT count(*) FROM audit_events WHERE resource_id=:id AND action='review_plan.generated'"),
                          {'id': responses[0].json()['data']['id']}).scalar_one() == 1


def test_audit_failure_rolls_back_plan_entries_and_report_enrollment(report_result, client, owner, student,
                                                                  admin_engine, monkeypatch):
    import wizzy.review_plans as module

    h = owner['headers']
    qid = questions(client, owner, report_result[3])[0]['question_id']
    assert override(client, h, qid, verdict='incorrect').status_code == 201
    rid = report(client, h, student['id']).json()['data']['id']

    def fail(*args, **kwargs):
        raise RuntimeError('injected review plan audit failure')

    monkeypatch.setattr(module, 'audit', fail)
    with pytest.raises(RuntimeError, match='injected review plan audit failure'):
        generate(client, h, student['id'], report_id=rid)
    with admin_engine.connect() as db:
        for table in ('review_plans', 'review_plan_entries', 'review_tasks'):
            assert db.execute(text(f'SELECT count(*) FROM {table} WHERE student_id=:id'),
                              {'id': student['id']}).scalar_one() == 0


def plan_calls(plan_id, student_id):
    return [
        ('read_review_plan(:id)', {'id': plan_id}),
        ('generate_review_plan(:id,:kind,:starts,:maximum,:minutes,:each,:report,:key)', {
            'id': student_id, 'kind': 'weekly', 'starts': '2026-10-26', 'maximum': 20,
            'minutes': 20, 'each': 2, 'report': None, 'key': uuid4()}),
    ]


def test_sql_capabilities_reject_missing_mixed_or_foreign_context(client, owner, student, database):
    from sqlalchemy import create_engine

    from wizzy.db import set_context

    actor = client.get('/api/v1/auth/me', headers=owner['headers']).json()['data']['user_id']
    plan_id = generate(client, owner['headers'], student['id']).json()['data']['id']
    engine = create_engine(database['url'], hide_parameters=True)
    try:
        for function, args in plan_calls(plan_id, student['id']):
            with engine.begin() as db:
                statement = text('SELECT public.'+function)
                assert db.execute(statement, args).scalar_one() == {'error': 'not_found'}
                for context in ({'tenant_id': owner['tenant_id'], 'student_id': student['id'], 'user_id': actor},
                                {'tenant_id': uuid4(), 'user_id': actor},
                                {'tenant_id': owner['tenant_id'], 'student_id': uuid4()},
                                {'tenant_id': owner['tenant_id'], 'user_id': uuid4()}):
                    set_context(db, **context)
                    assert db.execute(statement, args).scalar_one() == {'error': 'not_found'}
                set_context(db, owner['tenant_id'], user_id=actor)
                assert 'error' not in db.execute(statement, args).scalar_one()
                if 'kind' in args:
                    for extra in ({'kind': None}, {'kind': 'daily'}, {'starts': None}, {'starts': '2026-10-27'},
                                  {'starts': '1999-12-27'}, {'starts': '9999-12-27'}, {'maximum': None},
                                  {'maximum': 0}, {'maximum': 101}, {'minutes': None}, {'minutes': 0},
                                  {'minutes': 121}, {'each': None}, {'each': 0}, {'each': 31}, {'key': None}):
                        assert db.execute(statement, args | extra).scalar_one() == {'error': 'invalid_plan'}
                set_context(db, owner['tenant_id'], student_id=student['id'])
                result = db.execute(statement, args).scalar_one()
                assert ('error' not in result) if function.startswith('read_') else result == {'error': 'not_found'}
    finally:
        engine.dispose()


@pytest.mark.parametrize('change', ['tenant', 'member', 'user', 'student', 'consent', 'epoch', 'assignment'])
def test_sql_capabilities_recheck_live_authorization(client, owner, student, database, admin_engine, change):
    from sqlalchemy import create_engine
    from test_result_read import invited_guardian

    from wizzy.db import set_context

    h = owner['headers']
    _, actor = invited_guardian(client, owner, [student['id']])
    key = uuid4()
    plan_id = generate(client, h, student['id'], key).json()['data']['id']
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
            for function, args in plan_calls(plan_id, student['id']):
                if 'key' in args:
                    args['key'] = key
                result = db.execute(text('SELECT public.'+function), args).scalar_one()
                if change == 'epoch' and function.startswith('generate_'):
                    assert result['created'] is True and result['plan']['id'] != plan_id
                    assert result['plan']['entries'] == []
                else:
                    assert result == {'error': 'not_found'}
    finally:
        engine.dispose()


@pytest.mark.parametrize('operation', [0, 1])
@pytest.mark.parametrize('revoke', ['consent', 'assignment'])
def test_waiting_capability_rechecks_revocation(client, owner, student, database, admin_engine, operation, revoke):
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import create_engine
    from test_execution import wait_for_blocker
    from test_result_read import invited_guardian

    from wizzy.db import set_context

    plan_id = generate(client, owner['headers'], student['id']).json()['data']['id']
    _, actor = invited_guardian(client, owner, [student['id']])
    function, args = plan_calls(plan_id, student['id'])[operation]
    engine = create_engine(database['url'], hide_parameters=True)

    def call():
        with engine.begin() as db:
            set_context(db, owner['tenant_id'], user_id=actor)
            return db.execute(text('SELECT public.'+function), args).scalar_one()

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with admin_engine.begin() as db:
                blocker = db.execute(text('SELECT pg_backend_pid()')).scalar_one()
                query = ('UPDATE students SET consent_active=false WHERE id=:id' if revoke == 'consent'
                         else 'DELETE FROM student_access WHERE student_id=:id AND user_id=:actor')
                db.execute(text(query), {'id': student['id'], 'actor': actor})
                future = pool.submit(call)
                wait_for_blocker(admin_engine, blocker)
                assert not future.done()
            assert future.result(timeout=5) == {'error': 'not_found'}
    finally:
        engine.dispose()
