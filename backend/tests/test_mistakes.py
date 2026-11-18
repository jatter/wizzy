"""Real PostgreSQL projection and live-authorized mistake reads."""
import pytest
from sqlalchemy import create_engine, text
from test_execution import execution as execution  # noqa: F401
from test_question_revisions import correction, questions
from test_result_read import child_session, invited_guardian
from test_result_read import readable_result as readable_result  # noqa: F401
from test_results import result_context as result_context  # noqa: F401
from test_verdict_revisions import override


def mistakes(client, headers, student_id, **params):
    return client.get(f'/api/v1/students/{student_id}/mistakes', headers=headers, params=params)


def test_mistakes_start_empty_for_conservative_machine_results(readable_result, client, owner, student):
    response = mistakes(client, owner['headers'], student['id'])
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['data'] == {'items': [], 'next_cursor': None, 'pending_updates': 0}


def tick(database):
    from wizzy.learning import LearningConsumer

    engine = create_engine(database['worker_url'], hide_parameters=True)
    try:
        return LearningConsumer(engine).tick()
    finally:
        engine.dispose()


def drain(database):
    for _ in range(100):
        if tick(database) == 'idle':
            return
    pytest.fail('derivation queue did not drain')


def test_verdict_changes_hide_stale_items_then_rebuild(readable_result, client, owner, student, database, admin_engine):
    drain(database)
    q = questions(client, owner, readable_result[3])[0]
    assert override(client, owner['headers'], q['question_id'], verdict='incorrect').status_code == 201
    stale = mistakes(client, owner['headers'], student['id']).json()['data']
    assert stale == {'items': [], 'next_cursor': None, 'pending_updates': 1}
    drain(database)
    current = mistakes(client, owner['headers'], student['id']).json()['data']
    assert current['pending_updates'] == 0 and len(current['items']) == 1
    item = current['items'][0]
    assert item['question_id'] == q['question_id'] and item['effective_revision'] == 2
    assert item['verdict'] == 'incorrect' and item['verdict_provenance'] == 'guardian'
    assert item['stem'] == q['stem'] and item['student_answer_raw'] == q['student_answer_raw']
    assert not ({'actor_id', 'machine_question', 'history', 'verdict_history'} & set(item))
    with admin_engine.connect() as db:
        before = db.execute(text('SELECT count(*) FROM model_runs')).scalar_one()
        receipts = db.execute(text('SELECT count(*) FROM question_derivation_receipts WHERE question_id=:id'),
                              {'id': q['question_id']}).scalar_one()
        assert receipts == 1
    assert tick(database) == 'idle'
    assert override(client, owner['headers'], q['question_id'], verdict='correct', expected_verdict_revision=1).status_code == 201
    assert mistakes(client, owner['headers'], student['id']).json()['data']['items'] == []
    drain(database)
    assert mistakes(client, owner['headers'], student['id']).json()['data']['pending_updates'] == 0
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM model_runs')).scalar_one() == before


@pytest.mark.parametrize('verdict', ['correct', 'pending_review', 'unanswered', 'unsupported'])
def test_only_incorrect_enters_mistakes(readable_result, client, owner, student, database, verdict):
    q = questions(client, owner, readable_result[3])[0]
    assert override(client, owner['headers'], q['question_id'], verdict=verdict).status_code == 201
    drain(database)
    assert mistakes(client, owner['headers'], student['id']).json()['data'] == {
        'items': [], 'next_cursor': None, 'pending_updates': 0}


def test_ocr_change_invalidates_mistake_without_losing_original(readable_result, client, owner, student, database):
    q = questions(client, owner, readable_result[3])[0]
    assert override(client, owner['headers'], q['question_id'], verdict='incorrect').status_code == 201
    drain(database)
    assert len(mistakes(client, owner['headers'], student['id']).json()['data']['items']) == 1
    assert correction(client, owner['headers'], q['question_id']).status_code == 201
    assert mistakes(client, owner['headers'], student['id']).json()['data']['items'] == []
    drain(database)
    assert mistakes(client, owner['headers'], student['id']).json()['data']['pending_updates'] == 0
    assert override(client, owner['headers'], q['question_id'], expected_revision=2,
                    expected_verdict_revision=1, verdict='incorrect').status_code == 201
    drain(database)
    item = mistakes(client, owner['headers'], student['id']).json()['data']['items'][0]
    assert item['student_answer_raw'] == '20' and item['effective_revision'] == 4


def test_live_permissions_children_and_cursor_validation(readable_result, client, owner, student, database):
    q = questions(client, owner, readable_result[3])[0]
    assert override(client, owner['headers'], q['question_id'], verdict='incorrect').status_code == 201
    drain(database)
    child = child_session(client, owner, student['id'])
    assert len(mistakes(client, child, student['id']).json()['data']['items']) == 1
    assigned, membership = invited_guardian(client, owner, [student['id']])
    assert mistakes(client, assigned, student['id']).status_code == 200
    assert client.delete(f'/api/v1/tenants/members/{membership}', headers=owner['headers']).status_code == 200
    assert mistakes(client, assigned, student['id']).status_code == 401
    sibling = client.post('/api/v1/students', headers=owner['headers'], json={'nickname': '其他学生', 'grade': 3}).json()['data']
    other, _ = invited_guardian(client, owner, [sibling['id']])
    assert mistakes(client, other, student['id']).status_code == 404
    assert mistakes(client, child, sibling['id']).status_code == 404
    assert mistakes(client, {}, student['id']).status_code == 401
    assert mistakes(client, owner['headers'], student['id'], cursor='not-a-cursor').status_code == 422
    assert mistakes(client, owner['headers'], student['id'], limit=0).status_code == 422
    assert mistakes(client, owner['headers'], student['id'], limit=101).status_code == 422


def test_keyset_pagination_is_stable_for_equal_timestamps(readable_result, client, owner, student, database):
    qs = questions(client, owner, readable_result[3])
    for q in qs:
        assert override(client, owner['headers'], q['question_id'], verdict='incorrect').status_code == 201
    drain(database)
    seen, cursor = [], None
    for _ in qs:
        response = mistakes(client, owner['headers'], student['id'], limit=1, **({'cursor': cursor} if cursor else {}))
        assert response.status_code == 200, response.text
        body = response.json()['data']
        assert len(body['items']) == 1
        seen.append(body['items'][0]['question_id'])
        cursor = body['next_cursor']
    assert cursor is None and set(seen) == {q['question_id'] for q in qs}


@pytest.mark.parametrize('final_verdict', ['correct', 'incorrect'])
def test_multiple_unconsumed_changes_cannot_resurrect_old_mistake(readable_result, client, owner, student, database, admin_engine, final_verdict):
    drain(database)
    q = questions(client, owner, readable_result[3])[0]
    verdicts = ['incorrect', 'correct', 'incorrect', final_verdict]
    for revision, verdict in enumerate(verdicts):
        assert override(client, owner['headers'], q['question_id'], verdict=verdict,
                        expected_verdict_revision=revision).status_code == 201
    assert mistakes(client, owner['headers'], student['id']).json()['data']['pending_updates'] == 1
    # Deliberately process newest first, then older events; event order is not a correctness premise.
    with admin_engine.begin() as db:
        db.execute(text("UPDATE question_verdict_events SET created_at=clock_timestamp() - new_revision * interval '1 second' WHERE question_id=:id"), {'id': q['question_id']})
    drain(database)
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT new_revision,state FROM question_derivation_receipts WHERE question_id=:id ORDER BY new_revision'),
                          {'id': q['question_id']}).all() == [(2, 'obsolete'), (3, 'obsolete'), (4, 'obsolete'), (5, 'applied')]
    body = mistakes(client, owner['headers'], student['id']).json()['data']
    assert body['pending_updates'] == 0
    assert len(body['items']) == (1 if final_verdict == 'incorrect' else 0)
    if body['items']:
        assert body['items'][0]['effective_revision'] == 5


def test_parallel_workers_apply_each_event_once(readable_result, client, owner, student, database, admin_engine):
    from concurrent.futures import ThreadPoolExecutor

    drain(database)
    qs = questions(client, owner, readable_result[3])
    for q in qs:
        assert override(client, owner['headers'], q['question_id'], verdict='incorrect').status_code == 201
    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(lambda _: tick(database), range(12)))
    assert outcomes.count('applied') == len(qs) and outcomes.count('idle') == 12 - len(qs)
    body = mistakes(client, owner['headers'], student['id']).json()['data']
    assert len(body['items']) == len(qs) and body['pending_updates'] == 0
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM question_derivation_receipts WHERE question_id=ANY(CAST(:ids AS uuid[]))'),
                          {'ids': [q['question_id'] for q in qs]}).scalar_one() == len(qs)


def test_projection_failure_rolls_back_receipt_and_retries(readable_result, client, owner, student, database, admin_engine):
    from sqlalchemy.exc import DBAPIError

    drain(database)
    q = questions(client, owner, readable_result[3])[0]
    assert override(client, owner['headers'], q['question_id'], verdict='incorrect').status_code == 201
    # Fault injection is scoped to this question and always removed, including assertion failures.
    with admin_engine.begin() as db:
        db.execute(text(f"ALTER TABLE mistake_projections ADD CONSTRAINT test_derivation_failure CHECK(question_id<>'{q['question_id']}'::uuid)"))
    try:
        with pytest.raises(DBAPIError, match='test_derivation_failure'):
            tick(database)
        with admin_engine.connect() as db:
            assert db.execute(text('SELECT count(*) FROM question_derivation_receipts WHERE question_id=:id'), {'id': q['question_id']}).scalar_one() == 0
        assert mistakes(client, owner['headers'], student['id']).json()['data']['pending_updates'] == 1
    finally:
        with admin_engine.begin() as db:
            db.execute(text('ALTER TABLE mistake_projections DROP CONSTRAINT test_derivation_failure'))
    assert tick(database) == 'applied'
    assert len(mistakes(client, owner['headers'], student['id']).json()['data']['items']) == 1


@pytest.mark.parametrize('change,status', [
    ('consent', 403), ('epoch', 200), ('inactive', 404), ('tenant', 401),
    ('result_revision', 200), ('receipt_revision', 200), ('job_epoch', 200), ('cancelled', 200),
])
@pytest.mark.parametrize('consumed', [False, True])
def test_derivations_recheck_live_source(readable_result, client, owner, student, database, admin_engine, change, status, consumed):
    drain(database)
    q = questions(client, owner, readable_result[3])[0]
    assert override(client, owner['headers'], q['question_id'], verdict='incorrect').status_code == 201
    if consumed:
        drain(database)
    with admin_engine.begin() as db:
        if change in {'consent', 'epoch', 'inactive'}:
            assignment = {'consent': 'consent_active=false', 'epoch': 'deletion_epoch=deletion_epoch+1', 'inactive': "status='disabled'"}[change]
            db.execute(text(f'UPDATE students SET {assignment} WHERE id=:id'), {'id': student['id']})
        elif change == 'tenant':
            db.execute(text("UPDATE tenants SET status='suspended' WHERE id=:id"), {'id': owner['tenant_id']})
        elif change == 'result_revision':
            db.execute(text('UPDATE grading_results SET source_revision=source_revision+1 WHERE event_id=:event_id'), readable_result[0])
        elif change == 'receipt_revision':
            db.execute(text('UPDATE execution_receipts SET source_revision=source_revision+1 WHERE event_id=:event_id'), readable_result[0])
        elif change == 'job_epoch':
            db.execute(text('UPDATE jobs SET deletion_epoch=deletion_epoch+1 WHERE id=:job_id'), readable_result[0])
        else:
            db.execute(text("UPDATE submissions SET status='cancelled' WHERE id=:id"), {'id': readable_result[3]})
    response = mistakes(client, owner['headers'], student['id'])
    assert response.status_code == status, response.text
    if status == 200:
        assert response.json()['data'] == {'items': [], 'next_cursor': None, 'pending_updates': 0}
    if not consumed:
        assert tick(database) == 'invalidated'
        with admin_engine.connect() as db:
            assert db.execute(text('SELECT count(*) FROM mistake_projections WHERE question_id=:id'), {'id': q['question_id']}).scalar_one() == 0


def test_consent_regrant_does_not_resurrect_mistakes(readable_result, client, owner, student, database):
    drain(database)
    q = questions(client, owner, readable_result[3])[0]
    assert override(client, owner['headers'], q['question_id'], verdict='incorrect').status_code == 201
    drain(database)
    body = {'student_id': student['id'], 'policy_version': '2026-10-25', 'child_rules_version': '2026-10-25', 'granted': False}
    assert client.post('/api/v1/consents', headers=owner['headers'], json=body).status_code == 201
    assert client.post('/api/v1/consents', headers=owner['headers'], json=body | {'granted': True}).status_code == 201
    assert mistakes(client, owner['headers'], student['id']).json()['data'] == {
        'items': [], 'next_cursor': None, 'pending_updates': 0}


def test_consumer_waiting_for_withdrawal_does_not_apply_stale_event(readable_result, client, owner, student, database, admin_engine):
    from concurrent.futures import ThreadPoolExecutor

    from test_execution import wait_for_blocker

    drain(database)
    q = questions(client, owner, readable_result[3])[0]
    assert override(client, owner['headers'], q['question_id'], verdict='incorrect').status_code == 201
    with ThreadPoolExecutor(max_workers=1) as pool:
        with admin_engine.begin() as db:
            blocker = db.execute(text('SELECT pg_backend_pid()')).scalar_one()
            db.execute(text('UPDATE students SET consent_active=false,deletion_epoch=deletion_epoch+1 WHERE id=:id'), {'id': student['id']})
            future = pool.submit(tick, database)
            wait_for_blocker(admin_engine, blocker)
            assert not future.done()
        assert future.result(timeout=5) == 'invalidated'


def test_derivation_has_no_grading_or_quota_side_effects(readable_result, client, owner, student, database, admin_engine):
    from test_results import snapshot

    drain(database)
    before = snapshot(admin_engine, readable_result[0])
    with admin_engine.connect() as db:
        calls = db.execute(text('SELECT count(*) FROM model_runs')).scalar_one()
    q = questions(client, owner, readable_result[3])[0]
    assert override(client, owner['headers'], q['question_id'], verdict='incorrect').status_code == 201
    drain(database)
    assert mistakes(client, owner['headers'], student['id']).status_code == 200
    assert snapshot(admin_engine, readable_result[0]) == before
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM model_runs')).scalar_one() == calls


def test_private_derivation_capabilities_and_function_authorization(readable_result, client, owner, student, database, admin_engine):
    from uuid import uuid4

    from sqlalchemy.exc import DBAPIError

    from wizzy.db import set_context
    from wizzy.learning import LearningConsumer

    drain(database)
    q = questions(client, owner, readable_result[3])[0]
    assert override(client, owner['headers'], q['question_id'], verdict='incorrect').status_code == 201
    drain(database)
    actor_id = client.get('/api/v1/auth/me', headers=owner['headers']).json()['data']['user_id']
    for key in ('url', 'worker_url', 'dispatcher_url'):
        engine = create_engine(database[key], hide_parameters=True)
        try:
            for table in ('question_derivation_receipts', 'mistake_projections', 'question_verdict_events'):
                for query in (f'SELECT * FROM public.{table}', f'DELETE FROM public.{table}'):
                    with engine.begin() as db, pytest.raises(DBAPIError):
                        db.execute(text(query))
            forbidden = ('SELECT public.consume_question_derivation()' if key != 'worker_url'
                         else 'SELECT public.read_student_mistakes(NULL,20,NULL,NULL)')
            with engine.begin() as db, pytest.raises(DBAPIError):
                db.execute(text(forbidden))
            if key != 'worker_url':
                with pytest.raises(RuntimeError):
                    LearningConsumer(engine)
            if key == 'url':
                with engine.begin() as db:
                    query = text('SELECT public.read_student_mistakes(:id,20,NULL,NULL)')
                    assert db.execute(query, {'id': student['id']}).scalar_one()['items'] == []
                    set_context(db, uuid4(), user_id=actor_id)
                    assert db.execute(query, {'id': student['id']}).scalar_one()['items'] == []
                    set_context(db, owner['tenant_id'], user_id=uuid4())
                    assert db.execute(query, {'id': student['id']}).scalar_one()['items'] == []
                    set_context(db, owner['tenant_id'], user_id=actor_id)
                    assert len(db.execute(query, {'id': student['id']}).scalar_one()['items']) == 1
        finally:
            engine.dispose()
    with pytest.raises(RuntimeError):
        LearningConsumer(admin_engine)
    with admin_engine.connect() as db:
        rows = db.execute(text("""SELECT p.prosecdef,p.proconfig,has_function_privilege('public',p.oid,'EXECUTE')
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.proname IN ('consume_question_derivation','read_student_mistakes')""")).all()
        assert len(rows) == 2
        assert all(row[0] and row[1] == ['search_path=pg_catalog, pg_temp'] and not row[2] for row in rows)
        assert db.execute(text("SELECT count(*) FROM pg_class WHERE relname IN ('question_derivation_receipts','mistake_projections') AND relrowsecurity AND relforcerowsecurity")).scalar_one() == 2
