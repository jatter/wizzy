"""Real PostgreSQL correction history; no implicit grading or mastery."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_execution import execution as execution  # noqa: F401
from test_media import intent, upload
from test_question_revisions import correction, questions
from test_result_read import child_session, invited_guardian
from test_result_read import readable_result as readable_result  # noqa: F401
from test_results import result_context as result_context  # noqa: F401
from test_verdict_revisions import override

from wizzy.corrections import written
from wizzy.db import set_context


def attempts_path(question_id):
    return f'/api/v1/questions/{question_id}/correction-attempts'


def submit(client, headers, question_id, *, key=None, **extra):
    return client.post(attempts_path(question_id), headers=headers | {
        'Idempotency-Key': str(key or uuid4())}, json={
            'expected_effective_revision': 2, 'student_answer': '20', **extra})


def judge(client, headers, question_id, attempt_id, *, key=None, **extra):
    return client.post(f'{attempts_path(question_id)}/{attempt_id}/judgments', headers=headers | {
        'Idempotency-Key': str(key or uuid4())}, json={
            'expected_judgment_revision': 0, 'verdict': 'correct', 'reason': '核对了订正答案', **extra})


@pytest.fixture
def wrong_question(readable_result, client, owner):
    q = questions(client, owner, readable_result[3])[0]
    response = override(client, owner['headers'], q['question_id'], verdict='incorrect')
    assert response.status_code == 201, response.text
    return response.json()['data']


def history(client, headers, qid, **params):
    return client.get(attempts_path(qid), headers=headers, params=params)


def test_preserves_original_answer_and_every_attempt_and_judgment(wrong_question, client, owner, admin_engine):
    qid, headers = wrong_question['question_id'], owner['headers']
    before = client.get(f'/api/v1/questions/{qid}', headers=headers).json()['data']
    response = submit(client, headers, qid)
    assert response.status_code == 201, response.text
    attempt = response.json()['data']
    assert attempt['student_answer'] == '20' and attempt['media_id'] is None
    assert attempt['verdict'] == 'pending_review' and attempt['verdict_provenance'] == 'unverified'
    assert attempt['judgment_revision'] == 0 and attempt['judgment_history'] == []
    assert attempt['source_is_current'] is True
    assert attempt['source'] == {
        'question_revision': 1, 'verdict_revision': 1, 'effective_revision': 2,
        'stem': before['stem'], 'student_answer_raw': '21', 'verdict': 'incorrect',
        'verdict_provenance': 'guardian'}
    response = judge(client, headers, qid, attempt['id'], verdict='incorrect')
    assert response.status_code == 201, response.text
    assert response.json()['data']['verdict'] == 'incorrect'
    second = submit(client, headers, qid, student_answer='再算一次得到20').json()['data']
    assert second['id'] != attempt['id']
    response = judge(client, headers, qid, attempt['id'], expected_judgment_revision=1)
    assert response.status_code == 201, response.text
    value = response.json()['data']
    assert value['judgment_revision'] == 2 and value['verdict_provenance'] == 'guardian'
    assert [j['verdict'] for j in value['judgment_history']] == ['incorrect', 'correct']
    assert all(j['provenance'] == 'guardian' for j in value['judgment_history'])
    assert len(history(client, headers, qid).json()['data']['items']) == 2
    assert client.get(f'/api/v1/questions/{qid}', headers=headers).json()['data'] == before
    assert not ({'actor_id', 'tenant_id', 'raw_key', 'body_hash', 'mastered'} & set(value))
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM question_verdict_events WHERE question_id=:id'), {'id': qid}).scalar_one() == 1
        assert db.execute(text("SELECT count(*) FROM audit_events WHERE action='correction.submitted' AND resource_id IN (:a,:b)"),
                          {'a': attempt['id'], 'b': second['id']}).scalar_one() == 2


def test_idempotent_submission_and_judgment_replays_are_not_new_events(wrong_question, client, owner, admin_engine):
    qid, h, key = wrong_question['question_id'], owner['headers'], uuid4()
    first = submit(client, h, qid, key=key).json()['data']
    assert submit(client, h, qid, key=key).json()['data'] == first
    assert submit(client, h, qid, key=key, student_answer='19').status_code == 409
    judgment_key = uuid4()
    judged = judge(client, h, qid, first['id'], key=judgment_key).json()['data']
    assert judge(client, h, qid, first['id'], key=judgment_key).json()['data'] == judged
    assert judge(client, h, qid, first['id'], key=judgment_key, verdict='incorrect').status_code == 409
    assert judge(client, h, qid, first['id']).status_code == 409
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM audit_events WHERE resource_id=:id AND action='correction.submitted'"), {'id': first['id']}).scalar_one() == 1
        assert db.execute(text("SELECT count(*) FROM audit_events WHERE resource_id=:id AND action='correction.judged'"), {'id': first['id']}).scalar_one() == 1


def test_child_may_submit_but_only_assigned_guardians_may_judge(wrong_question, client, owner, student):
    qid = wrong_question['question_id']
    child = child_session(client, owner, student['id'])
    response = submit(client, child, qid)
    assert response.status_code == 201, response.text
    attempt = response.json()['data']
    assert history(client, child, qid).status_code == 200
    assert judge(client, child, qid, attempt['id']).status_code == 403
    assigned, _ = invited_guardian(client, owner, [student['id']])
    assert judge(client, assigned, qid, attempt['id']).status_code == 201
    sibling = client.post('/api/v1/students', headers=owner['headers'], json={'nickname': '另一学生', 'grade': 3}).json()['data']
    other, _ = invited_guardian(client, owner, [sibling['id']])
    assert client.post('/api/v1/consents', headers=owner['headers'], json={'student_id': sibling['id'],
        'policy_version': '2026-10-25', 'child_rules_version': '2026-10-25', 'granted': True}).status_code == 201
    other_child = child_session(client, owner, sibling['id'])
    for headers in (other, other_child):
        assert history(client, headers, qid).status_code == 404
        assert submit(client, headers, qid).status_code == 404
    assert judge(client, other, qid, attempt['id']).status_code == 404
    assert history(client, {}, qid).status_code == 401
    assert submit(client, owner['headers'], uuid4()).status_code == 404
    assert judge(client, owner['headers'], qid, uuid4()).status_code == 404


def test_text_or_sanitized_same_student_image(wrong_question, client, owner, student):
    qid, h = wrong_question['question_id'], owner['headers']
    media_id = upload(client, h, student['id'])
    response = submit(client, h, qid, student_answer=None, media_id=media_id)
    assert response.status_code == 201, response.text
    attempt = response.json()['data']
    assert attempt['student_answer'] is None and attempt['media_id'] == media_id
    assert attempt['verdict'] == 'pending_review'
    unready, _ = intent(client, h, student['id'])
    assert submit(client, h, qid, student_answer=None, media_id=unready['media_id']).status_code == 404
    sibling = client.post('/api/v1/students', headers=h, json={'nickname': '其他学生', 'grade': 3}).json()['data']
    assert client.post('/api/v1/consents', headers=h, json={'student_id': sibling['id'], 'policy_version': '2026-10-25', 'child_rules_version': '2026-10-25', 'granted': True}).status_code == 201
    foreign = upload(client, h, sibling['id'])
    assert submit(client, h, qid, student_answer=None, media_id=foreign).status_code == 404


@pytest.mark.parametrize('extra', [dict(student_answer=None), dict(student_answer=''), dict(student_answer=' \t'),
    dict(student_answer='x'*8001), dict(student_answer='\x00'), dict(media_id=str(uuid4())),
    dict(expected_effective_revision=True), dict(expected_effective_revision=0), dict(expected_effective_revision=2**63),
    dict(verdict='correct'), dict(mastered=True)])
def test_invalid_submission_is_rejected(wrong_question, client, owner, extra):
    assert submit(client, owner['headers'], wrong_question['question_id'], **extra).status_code == 422


@pytest.mark.parametrize('extra', [dict(expected_judgment_revision=True), dict(expected_judgment_revision=-1),
    dict(expected_judgment_revision=2**31), dict(verdict='unsupported'), dict(reason=''), dict(reason=' \t'),
    dict(reason='x'*1001), dict(reason='\x00'), dict(mastered=True)])
def test_invalid_judgment_is_rejected(wrong_question, client, owner, extra):
    qid, h = wrong_question['question_id'], owner['headers']
    attempt = submit(client, h, qid).json()['data']
    assert judge(client, h, qid, attempt['id'], **extra).status_code == 422


def test_missing_or_malformed_operation_key_is_rejected(wrong_question, client, owner):
    path, h = attempts_path(wrong_question['question_id']), owner['headers']
    body = {'expected_effective_revision': 2, 'student_answer': '20'}
    assert client.post(path, headers=h, json=body).status_code == 422
    assert client.post(path, headers=h | {'Idempotency-Key': 'bad'}, json=body).status_code == 422


def test_source_changes_retain_history_but_block_new_judgments(wrong_question, client, owner):
    qid, h, key, jkey = wrong_question['question_id'], owner['headers'], uuid4(), uuid4()
    attempt = submit(client, h, qid, key=key).json()['data']
    assert judge(client, h, qid, attempt['id'], key=jkey).status_code == 201
    assert correction(client, h, qid).status_code == 201
    previous = history(client, h, qid).json()['data']['items'][0]
    assert previous['source_is_current'] is False
    assert previous['source']['student_answer_raw'] == '21'
    assert submit(client, h, qid, key=key).json()['data'] == previous
    assert judge(client, h, qid, attempt['id'], key=jkey).json()['data'] == previous
    assert submit(client, h, qid).status_code == 409
    assert submit(client, h, qid, expected_effective_revision=3).status_code == 409
    assert judge(client, h, qid, attempt['id'], expected_judgment_revision=1).status_code == 409
    assert override(client, h, qid, expected_revision=2, expected_verdict_revision=1, verdict='incorrect').status_code == 201
    assert submit(client, h, qid, expected_effective_revision=4).status_code == 201
    assert history(client, h, qid).json()['data']['items'][0] == previous


@pytest.mark.parametrize('change,expected', [('consent', 403), ('epoch', 404), ('inactive', 404)])
def test_replay_revalidates_live_authority(wrong_question, client, owner, student, admin_engine, change, expected):
    qid, h, key = wrong_question['question_id'], owner['headers'], uuid4()
    attempt = submit(client, h, qid, key=key).json()['data']
    assignments = {'consent': 'consent_active=false', 'epoch': 'deletion_epoch=deletion_epoch+1', 'inactive': "status='disabled'"}
    with admin_engine.begin() as db:
        db.execute(text(f"UPDATE students SET {assignments[change]} WHERE id=:id"), {'id': student['id']})
    assert submit(client, h, qid, key=key).status_code == expected
    assert history(client, h, qid).status_code == expected
    assert judge(client, h, qid, attempt['id']).status_code == expected


def test_history_pagination_and_equal_timestamps(wrong_question, client, owner, admin_engine):
    qid, h = wrong_question['question_id'], owner['headers']
    ids = [submit(client, h, qid).json()['data']['id'] for _ in range(3)]
    with admin_engine.begin() as db:
        db.execute(text("UPDATE correction_attempts SET created_at='2026-10-25T00:00:00Z' WHERE question_id=:id"), {'id': qid})
    found, cursor = [], None
    for _ in range(3):
        params = {'limit': 1} | ({'cursor': cursor} if cursor else {})
        response = history(client, h, qid, **params)
        assert response.status_code == 200, response.text
        page = response.json()['data']
        found.extend(item['id'] for item in page['items'])
        cursor = page['next_cursor']
    assert found == sorted(ids) and cursor is None
    assert history(client, h, qid, cursor='invalid').status_code == 422
    assert history(client, h, qid, limit=101).status_code == 422


def test_concurrent_idempotency_and_judgment_cas(wrong_question, client, owner):
    qid, h, key = wrong_question['question_id'], owner['headers'], uuid4()
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: submit(client, h, qid, key=key), range(2)))
    assert [r.status_code for r in replies] == [201, 201]
    assert replies[0].json()['data']['id'] == replies[1].json()['data']['id']
    attempt_id = replies[0].json()['data']['id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda verdict: judge(client, h, qid, attempt_id, verdict=verdict), ['correct', 'incorrect']))
    assert sorted(r.status_code for r in replies) == [201, 409]
    assert len(history(client, h, qid).json()['data']['items'][0]['judgment_history']) == 1


def test_audit_failure_rolls_back_attempt_and_judgment(wrong_question, client, owner, admin_engine, monkeypatch):
    import wizzy.corrections as module

    qid, h = wrong_question['question_id'], owner['headers']
    original = module.audit
    def fail(*args, **kwargs):
        raise RuntimeError('injected audit failure')
    monkeypatch.setattr(module, 'audit', fail)
    with pytest.raises(RuntimeError, match='injected audit failure'):
        submit(client, h, qid)
    assert history(client, h, qid).json()['data']['items'] == []
    monkeypatch.setattr(module, 'audit', original)
    attempt = submit(client, h, qid).json()['data']
    monkeypatch.setattr(module, 'audit', fail)
    with pytest.raises(RuntimeError, match='injected audit failure'):
        judge(client, h, qid, attempt['id'])
    assert history(client, h, qid).json()['data']['items'][0] == attempt


def test_private_tables_and_internal_functions_are_not_runtime_capabilities(wrong_question, client, owner, database):
    for url in (database['url'], database['worker_url'], database['dispatcher_url']):
        engine = create_engine(url, hide_parameters=True)
        try:
            for table in ('correction_attempts', 'correction_judgments'):
                for query in (f'SELECT * FROM {table}', f'DELETE FROM {table}', f"UPDATE {table} SET actor_id=gen_random_uuid()"):
                    with engine.begin() as db, pytest.raises(DBAPIError):
                        db.execute(text(query))
            with engine.begin() as db, pytest.raises(DBAPIError):
                db.execute(text('SELECT public.lock_correction_question(:id)'), {'id': wrong_question['question_id']})
        finally:
            engine.dispose()


def test_unconfirmed_or_nonincorrect_questions_cannot_accept_new_attempts(readable_result, client, owner):
    qid, h = questions(client, owner, readable_result[3])[0]['question_id'], owner['headers']
    assert submit(client, h, qid, expected_effective_revision=1).status_code == 409
    for revision, verdict in enumerate(('correct', 'pending_review', 'unanswered', 'unsupported'), start=1):
        assert override(client, h, qid, expected_verdict_revision=revision-1, verdict=verdict).status_code == 201
        assert submit(client, h, qid, expected_effective_revision=revision+1).status_code == 409


def test_actor_scoped_keys_and_revoked_assignment(wrong_question, client, owner, student, admin_engine):
    qid, key = wrong_question['question_id'], uuid4()
    h, actor = invited_guardian(client, owner, [student['id']])
    a = submit(client, h, qid, key=key).json()['data']
    b = submit(client, owner['headers'], qid, key=key).json()['data']
    assert a['id'] != b['id']
    with admin_engine.begin() as db:
        db.execute(text('DELETE FROM student_access WHERE student_id=:student AND user_id=:actor'),
                   {'student': student['id'], 'actor': actor})
    assert submit(client, h, qid, key=key).status_code == 404
    assert history(client, h, qid).status_code == 404
    assert judge(client, h, qid, a['id']).status_code == 404


def test_sql_entry_points_recheck_content_authority_and_source(wrong_question, client, owner, student, database):
    qid, h = wrong_question['question_id'], owner['headers']
    actor = client.get('/api/v1/auth/me', headers=h).json()['data']['user_id']
    engine = create_engine(database['url'], hide_parameters=True)
    sql = text('SELECT public.submit_correction_attempt(:id,:version,:answer,:media,:key)')
    args = dict(id=qid, version=2, answer='20', media=None, key=uuid4())
    jsql = text('SELECT public.judge_correction_attempt(:id,:attempt,:version,:verdict,:reason,:key)')
    try:
        with engine.begin() as db:
            assert db.execute(sql, args).scalar_one() == {'error': 'not_found'}
            set_context(db, owner['tenant_id'], user_id=actor)
            for extra in (dict(answer=None), dict(answer=' \t'), dict(answer='x'*8001), dict(key=None),
                          dict(version=0), dict(media=uuid4())):
                assert db.execute(sql, args | extra).scalar_one() == {'error': 'invalid_correction'}
        attempt = submit(client, h, qid).json()['data']
        jargs = dict(id=qid, attempt=attempt['id'], version=0, verdict='correct', reason='核对', key=uuid4())
        with engine.begin() as db:
            set_context(db, owner['tenant_id'], student_id=student['id'])
            assert db.execute(jsql, jargs).scalar_one() == {'error': 'not_found'}
            set_context(db, owner['tenant_id'], user_id=actor)
            for extra in (dict(version=-1), dict(version=2147483647), dict(verdict='unsupported'),
                          dict(reason=' \t'), dict(reason=None), dict(key=None)):
                assert db.execute(jsql, jargs | extra).scalar_one() == {'error': 'invalid_correction'}
        assert correction(client, h, qid).status_code == 201
        with engine.begin() as db:
            set_context(db, owner['tenant_id'], user_id=actor)
            assert db.execute(sql, args).scalar_one() == {'error': 'source_conflict'}
            assert db.execute(jsql, jargs).scalar_one() == {'error': 'source_conflict'}
    finally:
        engine.dispose()


@pytest.mark.parametrize('fraction', ['', '.1', '.12', '.123', '.1234', '.12345', '.123456'])
def test_write_timestamp_matches_history_serialization(fraction):
    timestamp = f'2026-10-25T12:55:48{fraction}+00:00'
    request = SimpleNamespace(state=SimpleNamespace(request_id='req_timestamp'))
    value = written(None, request, None, {'created': False, 'attempt': {
        'created_at': timestamp}}, 'correction.submitted')
    assert value['data']['created_at'] == datetime.fromisoformat(timestamp).isoformat()
