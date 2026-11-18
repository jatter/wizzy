"""Immutable guardian report snapshots on isolated real PostgreSQL."""
from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_execution import execution as execution  # noqa: F401
from test_question_revisions import questions
from test_result_read import readable_result as readable_result  # noqa: F401
from test_results import result_context as result_context  # noqa: F401
from test_verdict_revisions import override


@pytest.fixture
def report_result(readable_result, admin_engine):
    """Keep report sources in the declared period, independent of the wall clock."""
    with admin_engine.begin() as db:
        db.execute(text("UPDATE submissions SET created_at='2026-10-20T00:00:00Z' WHERE id=:id"),
                   {'id': readable_result[3]})
    return readable_result


def generate(client, headers, student_id, **extra):
    return client.post('/api/v1/reports', headers=headers, json={
        'student_id': student_id, 'type': 'weekly', 'period_start': '2026-10-19', **extra})


def report_path(identifier):
    return f'/api/v1/reports/{identifier}'


@pytest.mark.parametrize(('kind', 'start', 'end'), [
    ('weekly', '2026-10-19', '2026-10-25T16:00:00Z'),
    ('monthly', '2026-10-01', '2026-10-31T16:00:00Z'),
])
def test_empty_report_is_persisted_with_no_invented_accuracy(client, owner, student, kind, start, end):
    response = generate(client, owner['headers'], student['id'], type=kind, period_start=start)
    assert response.status_code == 201, response.text
    view = response.json()['data']
    assert view['type'] == kind and view['student_id'] == student['id']
    assert datetime.fromisoformat(view['period_end']) == datetime.fromisoformat(end)
    assert view['timezone'] == 'Asia/Shanghai'
    assert view['stale'] is False and view['source_count'] == 0
    assert view['summary']['submissions'] == 0
    assert view['summary']['original'] == {
        'accepted': 0, 'effective': 0, 'correct': 0, 'incorrect': 0,
        'pending_review': 0, 'unanswered': 0, 'unsupported': 0, 'accuracy': None}
    assert view['summary']['weak_points'] == {'status': 'insufficient_evidence', 'items': []}
    again = generate(client, owner['headers'], student['id'], type=kind, period_start=start)
    assert again.json()['data'] == view
    assert client.get(report_path(view['id']), headers=owner['headers']).json()['data'] == view


def test_original_denominator_counts_confirmed_questions_only(readable_result, client, owner, student, admin_engine):
    headers, sid = owner['headers'], readable_result[3]
    with admin_engine.begin() as db:
        db.execute(text("UPDATE submissions SET created_at='2026-10-18T16:00:00Z' WHERE id=:id"), {'id': sid})
    rows = questions(client, owner, sid)
    assert len(rows) >= 2
    assert override(client, headers, rows[0]['question_id'], verdict='correct').status_code == 201
    assert override(client, headers, rows[1]['question_id'], verdict='incorrect').status_code == 201
    response = generate(client, headers, student['id'])
    assert response.status_code == 201, response.text
    original = response.json()['data']['summary']['original']
    assert original['accepted'] == len(rows)
    assert original['effective'] == 2 and original['correct'] == 1 and original['incorrect'] == 1
    assert original['accuracy'] == 0.5
    assert original['pending_review'] == len(rows)-2


@pytest.mark.parametrize('start', ['2026-10-18', '2026-10-20', '9999-12-27'])
def test_week_start_must_be_a_supported_monday(client, owner, student, start):
    assert generate(client, owner['headers'], student['id'], period_start=start).status_code == 422


def test_month_start_and_type_are_validated(client, owner, student):
    for extra in ({'type': 'monthly', 'period_start': '2026-10-02'}, {'type': 'daily'}, {'unexpected': 1}):
        assert generate(client, owner['headers'], student['id'], **extra).status_code == 422


@pytest.mark.parametrize(('created_at', 'included'), [
    ('2026-10-18T15:59:59.999999Z', False), ('2026-10-18T16:00:00Z', True),
    ('2026-10-25T15:59:59.999999Z', True), ('2026-10-25T16:00:00Z', False),
])
def test_shanghai_period_is_left_closed_right_open(readable_result, client, owner, student, admin_engine, created_at, included):
    with admin_engine.begin() as db:
        db.execute(text('UPDATE submissions SET created_at=:at WHERE id=:id'), {'at': created_at, 'id': readable_result[3]})
    response = generate(client, owner['headers'], student['id'])
    assert response.status_code == 201, response.text
    summary = response.json()['data']['summary']
    assert summary['submissions'] == int(included)
    assert summary['original']['accepted'] == (len(questions(client, owner, readable_result[3])) if included else 0)


@pytest.mark.parametrize('verdict', ['pending_review', 'unanswered', 'unsupported'])
def test_non_effective_verdicts_never_enter_accuracy(report_result, client, owner, student, verdict):
    q = questions(client, owner, report_result[3])[0]
    assert override(client, owner['headers'], q['question_id'], verdict=verdict).status_code == 201
    response = generate(client, owner['headers'], student['id'])
    assert response.status_code == 201, response.text
    original = response.json()['data']['summary']['original']
    assert original['effective'] == 0 and original['accuracy'] is None
    assert original[verdict] >= 1


def all_sources(client, headers, report_id):
    items, cursor = [], None
    while True:
        response = client.get(report_path(report_id)+'/sources', headers=headers,
                              params={'limit': 1, **({'cursor': cursor} if cursor else {})})
        assert response.status_code == 200, response.text
        page = response.json()['data']
        items.extend(page['items'])
        cursor = page['next_cursor']
        if cursor is None:
            return items


def test_immutable_sources_stale_and_explicit_rebuild(report_result, client, owner, student):
    from test_question_revisions import correction

    h = owner['headers']
    qid = questions(client, owner, report_result[3])[0]['question_id']
    assert override(client, h, qid, verdict='correct').status_code == 201
    response = generate(client, h, student['id'])
    assert response.status_code == 201, response.text
    first = response.json()['data']
    sources = all_sources(client, h, first['id'])
    assert len(sources) == first['source_count']
    source = next(s for s in sources if s['kind'] == 'original' and s['question_id'] == qid)
    assert source['snapshot']['verdict'] == 'correct' and source['snapshot']['verdict_provenance'] == 'guardian'
    for internal in ('model_run_id', 'prompt_hash', 'provider_request_id', 'execution_token', 'cost_microyuan'):
        assert internal not in str(sources)
    assert correction(client, h, qid).status_code == 201
    old = client.get(report_path(first['id']), headers=h).json()['data']
    assert old == first | {'stale': True}
    assert all_sources(client, h, first['id']) == sources
    rebuilt = client.post(report_path(first['id'])+'/rebuild', headers=h)
    assert rebuilt.status_code == 201, rebuilt.text
    second = rebuilt.json()['data']
    assert second['id'] != first['id'] and second['revision'] == first['revision']+1
    assert second['stale'] is False and second['summary']['original']['effective'] == 0
    assert client.post(report_path(first['id'])+'/rebuild', headers=h).json()['data'] == second
    listing = client.get('/api/v1/reports', headers=h, params={'student_id': student['id'], 'type': 'weekly', 'limit': 1})
    assert listing.status_code == 200, listing.text
    page = listing.json()['data']
    assert [x['id'] for x in page['items']] == [first['id']]
    page2 = client.get('/api/v1/reports', headers=h, params={
        'student_id': student['id'], 'type': 'weekly', 'limit': 1, 'cursor': page['next_cursor']}).json()['data']
    assert [x['id'] for x in page2['items']] == [second['id']] and page2['next_cursor'] is None
    for path, params in [(report_path(first['id'])+'/sources', {'limit': 101}),
                         (report_path(first['id'])+'/sources', {'cursor': 'bad'}),
                         ('/api/v1/reports', {'student_id': student['id'], 'limit': 0})]:
        assert client.get(path, headers=h, params=params).status_code == 422


def test_corrections_and_reviews_have_separate_time_and_denominators(readable_result, client, owner, student, admin_engine):
    from test_correction_attempts import judge, submit
    from test_result_read import child_session
    from test_review_workbench import attempt, enroll, save

    h = owner['headers']
    qid = questions(client, owner, readable_result[3])[0]['question_id']
    assert override(client, h, qid, verdict='incorrect').status_code == 201
    # Original belongs to the previous week; attempts belong to this week.
    with admin_engine.begin() as db:
        db.execute(text("UPDATE submissions SET created_at='2026-10-17T00:00:00Z' WHERE id=:id"), {'id': readable_result[3]})
    response = submit(client, h, qid)
    assert response.status_code == 201, response.text
    correction_id = response.json()['data']['id']
    assert judge(client, h, qid, correction_id).status_code == 201
    task = enroll(client, h, qid).json()['data']
    child = child_session(client, owner, student['id'])
    assert save(client, child, task['id']).status_code == 200
    assert attempt(client, child, task['id'], expected_draft_revision=1, outcome='submitted').status_code == 201
    with admin_engine.begin() as db:
        db.execute(text("UPDATE correction_attempts SET created_at='2026-10-20T00:00:00Z' WHERE id=:id"),
                   {'id': correction_id})
        db.execute(text("UPDATE review_attempts SET created_at='2026-10-21T00:00:00Z' WHERE task_id=:id"),
                   {'id': task['id']})
    response = generate(client, h, student['id'])
    assert response.status_code == 201, response.text
    view = response.json()['data']
    assert view['summary']['original']['accepted'] == 0
    assert view['summary']['corrections'] == {'attempts': 1, 'correct': 1, 'incorrect': 0, 'pending_review': 0, 'accuracy': 1.0}
    assert view['summary']['reviews'] == {'attempts': 1, 'submitted': 1, 'awaiting_review': 1, 'needs_help': 0, 'skipped': 0, 'completed': 0}
    sources = all_sources(client, h, view['id'])
    assert {s['kind'] for s in sources} == {'correction', 'review'}
    assert judge(client, h, qid, correction_id, expected_judgment_revision=1, verdict='incorrect').status_code == 201
    assert client.get(report_path(view['id']), headers=h).json()['data']['stale'] is True
    assert all_sources(client, h, view['id']) == sources


def test_guardian_only_assigned_access_and_consent(client, owner, student, admin_engine):
    from test_result_read import child_session, invited_guardian

    h = owner['headers']
    response = generate(client, h, student['id'])
    assert response.status_code == 201, response.text
    rid = response.json()['data']['id']
    assigned, _ = invited_guardian(client, owner, [student['id']])
    sibling = client.post('/api/v1/students', headers=h, json={'nickname': '其他学生', 'grade': 3})
    assert sibling.status_code == 201, sibling.text
    unassigned, _ = invited_guardian(client, owner, [sibling.json()['data']['id']])
    child = child_session(client, owner, student['id'])
    for headers, expected in [(assigned, 200), (unassigned, 404), (child, 403), ({}, 401)]:
        assert client.get(report_path(rid), headers=headers).status_code == expected
        assert client.get(report_path(rid)+'/sources', headers=headers).status_code == expected
    assert generate(client, child, student['id']).status_code == 403
    assert generate(client, h, str(uuid4())).status_code == 404
    assert client.get(report_path(uuid4()), headers=h).status_code == 404
    with admin_engine.begin() as db:
        db.execute(text('UPDATE students SET consent_active=false WHERE id=:id'), {'id': student['id']})
    assert client.get(report_path(rid), headers=h).status_code in (403, 404)
    assert client.post(report_path(rid)+'/rebuild', headers=h).status_code in (403, 404)
    with admin_engine.begin() as db:
        db.execute(text('UPDATE students SET consent_active=true,deletion_epoch=deletion_epoch+1 WHERE id=:id'), {'id': student['id']})
    assert client.get(report_path(rid), headers=h).status_code == 404
    assert generate(client, h, student['id']).json()['data']['id'] != rid


def test_parallel_generators_reuse_one_snapshot(client, owner, student, admin_engine):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: generate(client, owner['headers'], student['id']), range(2)))
    assert [r.status_code for r in responses] == [201, 201]
    assert responses[0].json()['data'] == responses[1].json()['data']
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM reports WHERE student_id=:id'), {'id': student['id']}).scalar_one() == 1


def test_audit_failure_rolls_back_report_and_sources(report_result, client, owner, student, admin_engine, monkeypatch):
    import wizzy.reports as module

    def fail(*args, **kwargs):
        raise RuntimeError('injected report audit failure')

    monkeypatch.setattr(module, 'audit', fail)
    with pytest.raises(RuntimeError, match='injected report audit failure'):
        generate(client, owner['headers'], student['id'])
    with admin_engine.connect() as db:
        for table in ('reports', 'report_sources'):
            assert db.execute(text(f'SELECT count(*) FROM {table} WHERE student_id=:id'),
                              {'id': student['id']}).scalar_one() == 0


def report_calls(report_id, student_id):
    return [
        ('generate_learning_report(:id,:kind,:starts)', {'id': student_id, 'kind': 'weekly', 'starts': '2026-10-19'}),
        ('read_learning_report(:id)', {'id': report_id}),
        ('read_learning_reports(:id,:kind,:maximum,:date,:after)', {
            'id': student_id, 'kind': 'weekly', 'maximum': 20, 'date': None, 'after': None}),
        ('read_learning_report_sources(:id,:maximum,:date,:after)', {
            'id': report_id, 'maximum': 20, 'date': None, 'after': None}),
        ('rebuild_learning_report(:id)', {'id': report_id}),
    ]


def test_sql_capabilities_recheck_guardian_and_parameters(client, owner, student, database):
    from sqlalchemy import create_engine

    from wizzy.db import set_context

    h = owner['headers']
    actor = client.get('/api/v1/auth/me', headers=h).json()['data']['user_id']
    rid = generate(client, h, student['id']).json()['data']['id']
    engine = create_engine(database['url'], hide_parameters=True)
    try:
        for function, args in report_calls(rid, student['id']):
            with engine.begin() as db:
                statement = text('SELECT public.'+function)
                assert db.execute(statement, args).scalar_one() == {'error': 'not_found'}
                for context in ({'tenant_id': owner['tenant_id'], 'student_id': student['id']},
                                {'tenant_id': owner['tenant_id'], 'student_id': student['id'], 'user_id': actor},
                                {'tenant_id': uuid4(), 'user_id': actor},
                                {'tenant_id': owner['tenant_id'], 'user_id': uuid4()}):
                    set_context(db, **context)
                    assert db.execute(statement, args).scalar_one() == {'error': 'not_found'}
                set_context(db, owner['tenant_id'], user_id=actor)
                assert 'error' not in db.execute(statement, args).scalar_one()
                if 'maximum' in args:
                    for extra in ({'maximum': None}, {'maximum': 0}, {'maximum': 101},
                                  {'date': datetime.now().astimezone()}, {'after': uuid4()}):
                        assert db.execute(statement, args | extra).scalar_one() == {'error': 'invalid_report'}
                if 'starts' in args:
                    for extra in ({'starts': None}, {'starts': '2026-10-20'}, {'starts': '1999-12-27'},
                                  {'starts': '9999-12-27'}, {'kind': None}, {'kind': 'daily'},
                                  {'kind': 'monthly', 'starts': '2026-10-02'}):
                        assert db.execute(statement, args | extra).scalar_one() == {'error': 'invalid_report'}
    finally:
        engine.dispose()


@pytest.mark.parametrize('change', ['tenant', 'member', 'user', 'student', 'consent', 'epoch'])
def test_sql_capabilities_recheck_live_status(client, owner, student, database, admin_engine, change):
    from sqlalchemy import create_engine

    from wizzy.db import set_context

    actor = client.get('/api/v1/auth/me', headers=owner['headers']).json()['data']['user_id']
    rid = generate(client, owner['headers'], student['id']).json()['data']['id']
    changes = {
        'tenant': ("UPDATE tenants SET status='suspended' WHERE id=:id", owner['tenant_id']),
        'member': ("UPDATE memberships SET status='revoked' WHERE user_id=:id", actor),
        'user': ("UPDATE users SET status='disabled' WHERE id=:id", actor),
        'student': ("UPDATE students SET status='deleted' WHERE id=:id", student['id']),
        'consent': ('UPDATE students SET consent_active=false WHERE id=:id', student['id']),
        'epoch': ('UPDATE students SET deletion_epoch=deletion_epoch+1 WHERE id=:id', student['id']),
    }
    query, identifier = changes[change]
    with admin_engine.begin() as db:
        db.execute(text(query), {'id': identifier})
    engine = create_engine(database['url'], hide_parameters=True)
    try:
        with engine.begin() as db:
            set_context(db, owner['tenant_id'], user_id=actor)
            for function, args in report_calls(rid, student['id']):
                result = db.execute(text('SELECT public.'+function), args).scalar_one()
                # A new deletion generation may create/list only new empty reports.
                if change == 'epoch' and function.startswith(('generate_', 'read_learning_reports(')):
                    assert 'error' not in result
                    if 'report' in result:
                        assert result['report']['source_count'] == 0 and result['report']['id'] != rid
                    else:
                        assert all(row['id'] != rid for row in result['items'])
                else:
                    assert result == {'error': 'not_found'}
    finally:
        engine.dispose()


@pytest.mark.parametrize('operation', range(5))
@pytest.mark.parametrize('revoke', ['consent', 'assignment'])
def test_waiting_sql_capability_rechecks_revocation(client, owner, student, database, admin_engine, operation, revoke):
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import create_engine
    from test_execution import wait_for_blocker
    from test_result_read import invited_guardian

    from wizzy.db import set_context

    rid = generate(client, owner['headers'], student['id']).json()['data']['id']
    _, actor = invited_guardian(client, owner, [student['id']])
    function, args = report_calls(rid, student['id'])[operation]
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
