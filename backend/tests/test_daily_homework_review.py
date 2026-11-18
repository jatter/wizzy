"""Regression coverage for checklist receipt, child scope and concurrent publication."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from sqlalchemy import create_engine, text
from test_daily_homework import body, create_item, url
from test_media import upload


def make_import(client, owner, student):
    media_id = upload(client, owner['headers'], student['id'])
    response = client.post('/api/v1/homework-imports', headers=owner['headers'],
                           json=body(student_id=student['id'], images=[{'media_id': media_id}]))
    assert response.status_code == 202, response.text
    return response.json()['data']


def seed_draft(admin_engine, imported, draft):
    with admin_engine.begin() as connection:
        connection.execute(text("UPDATE homework_imports SET status='review_ready',draft=CAST(:draft AS jsonb) WHERE id=:id"),
                           {'id': imported['id'], 'draft': json.dumps(draft)})
        connection.execute(text("UPDATE homework_import_images SET status='succeeded' WHERE import_id=:id"),
                           {'id': imported['id']})


def wait_for_database_lock(admin_engine, fragment):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        with admin_engine.connect() as connection:
            found = connection.execute(text("""SELECT EXISTS(SELECT 1 FROM pg_stat_activity
                WHERE datname=current_database() AND pid<>pg_backend_pid()
                AND wait_event_type='Lock' AND query LIKE :pattern)"""),
                {'pattern': '%' + fragment + '%'}).scalar_one()
        if found:
            return
        time.sleep(.01)
    raise AssertionError('Concurrent operation never reached its database lock')


def test_deleted_item_returns_original_receipt_on_replay(client, owner, student):
    item = create_item(client, owner, student)
    payload = body(expected_revision=item['revision'], deleted=True, reason='重复录入')
    endpoint = f"/api/v1/homework-items/{item['id']}"
    first = client.put(endpoint, headers=owner['headers'], json=payload)
    replay = client.put(endpoint, headers=owner['headers'], json=payload)
    assert first.status_code == replay.status_code == 200
    assert first.json()['data'] == replay.json()['data']
    assert client.get(endpoint, headers=owner['headers']).status_code == 404
    changed_key = payload | {'client_request_id': str(uuid4())}
    assert client.put(endpoint, headers=owner['headers'], json=changed_key).status_code == 404


def test_excluded_no_homework_claim_can_finish_import(client, owner, student, admin_engine):
    imported = make_import(client, owner, student)
    seed_draft(admin_engine, imported, {'items': [], 'notes': [], 'no_homework': [{
        'id': 'excluded-none', 'image_id': imported['images'][0]['id'], 'subject': 'math', 'excluded': True,
    }]})
    response = client.post(f"/api/v1/homework-imports/{imported['id']}/apply", headers=owner['headers'],
                           json=body(expected_revision=imported['revision']))
    assert response.status_code == 200, response.text
    assert response.json()['data']['status'] == 'applied'
    assert response.json()['data']['applied_ids'] == {'excluded-none': 'excluded'}


def test_duplicate_hint_never_discloses_unassigned_sibling(client, owner, student):
    sibling = client.post('/api/v1/students', headers=owner['headers'],
                          json={'nickname': '哥哥', 'grade': 5}).json()['data']
    consent = client.post('/api/v1/consents', headers=owner['headers'], json={
        'student_id': sibling['id'], 'policy_version': '2026-10-25',
        'child_rules_version': '2026-10-25', 'granted': True,
    })
    assert consent.status_code == 201, consent.text
    unauthorized_import = make_import(client, owner, sibling)
    allowed_import = make_import(client, owner, student)
    same_child_import = make_import(client, owner, student)
    email = f'limited-{uuid4().hex}@example.test'
    registered = client.post('/api/v1/auth/register', json={
        'email': email, 'password': 'Second-guardian-strong-passphrase',
        'privacy_version': '2026-10-25', 'accepted_privacy': True,
    }).json()['data']
    invite = client.post('/api/v1/guardian-invitations', headers=owner['headers'],
                         json={'email': email, 'student_ids': [student['id']]}).json()['data']
    accepted = client.post('/api/v1/guardian-invitations/accept',
                           headers={'Authorization': 'Bearer ' + registered['access_token']},
                           json={'code': invite['code']})
    assert accepted.status_code == 200, accepted.text
    headers = {'Authorization': 'Bearer ' + accepted.json()['data']['access_token']}
    assert client.get(f"/api/v1/homework-imports/{unauthorized_import['id']}", headers=headers).status_code == 404
    response = client.get(f"/api/v1/homework-imports/{allowed_import['id']}", headers=headers)
    assert response.status_code == 200, response.text
    hints = {entry['previous_import_id'] for entry in response.json()['data']['duplicate_images']}
    assert unauthorized_import['id'] not in hints
    assert same_child_import['id'] in hints


def test_manual_edit_and_import_merge_follow_one_lock_order(client, owner, student, admin_engine, monkeypatch):
    from wizzy import daily_homework

    item = create_item(client, owner, student)
    day = client.get(url(student), headers=owner['headers']).json()['data']
    imported = make_import(client, owner, student)
    candidate = {'id': 'merge-candidate', 'image_id': imported['images'][0]['id'],
                 'subject': 'chinese', 'title': item['title'], 'assigned_date': '2026-11-13',
                 'date_confirmed': True, 'decision': 'merge', 'merge_item_id': item['id']}
    seed_draft(admin_engine, imported, {'items': [candidate], 'no_homework': [], 'notes': []})
    edit_locked, release_edit = threading.Event(), threading.Event()
    original = daily_homework.invalidate_day

    def pause_edit(*args, **kwargs):
        edit_locked.set()
        assert release_edit.wait(10), 'Concurrent edit was never released'
        return original(*args, **kwargs)

    monkeypatch.setattr(daily_homework, 'invalidate_day', pause_edit)
    with ThreadPoolExecutor(max_workers=2) as pool:
        editing = pool.submit(client.put, f"/api/v1/homework-items/{item['id']}", headers=owner['headers'],
                              json=body(expected_revision=item['revision'], title='修改后的第15课'))
        try:
            assert edit_locked.wait(8)
            merging = pool.submit(client.post, f"/api/v1/homework-imports/{imported['id']}/apply",
                                  headers=owner['headers'], json=body(expected_revision=imported['revision'],
                                  day_revisions={'2026-11-13': day['revision']}))
            wait_for_database_lock(admin_engine, 'homework_')
        finally:
            release_edit.set()
        edited = editing.result(timeout=10)
        merged = merging.result(timeout=10)
    assert edited.status_code == 200, edited.text
    assert merged.status_code == 409, merged.text
    current = client.get(f"/api/v1/homework-items/{item['id']}", headers=owner['headers']).json()['data']
    assert current['title'] == '修改后的第15课'
    assert current['sources'] == []


def test_consent_revocation_and_worker_claim_follow_one_lock_order(client, owner, student, admin_engine, database, monkeypatch):
    from wizzy import daily_homework

    imported = make_import(client, owner, student)
    with admin_engine.begin() as connection:
        connection.execute(text("UPDATE homework_imports SET created_at='2000-01-01' WHERE id=:id"),
                           {'id': imported['id']})
    revoke_locked, release_revoke = threading.Event(), threading.Event()
    original = daily_homework.cancel_imports

    def pause_revoke(*args, **kwargs):
        revoke_locked.set()
        assert release_revoke.wait(10), 'Consent revocation was never released'
        return original(*args, **kwargs)

    monkeypatch.setattr(daily_homework, 'cancel_imports', pause_revoke)
    worker = create_engine(database['worker_url'], hide_parameters=True)

    def claim():
        with worker.begin() as connection:
            return connection.execute(text("SELECT public.claim_homework_import(NULL,'review prompt','review')")).scalar_one()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            revoking = pool.submit(client.post, '/api/v1/consents', headers=owner['headers'], json={
                'student_id': student['id'], 'policy_version': '2026-10-25',
                'child_rules_version': '2026-10-25', 'granted': False,
            })
            try:
                assert revoke_locked.wait(8)
                claiming = pool.submit(claim)
                wait_for_database_lock(admin_engine, 'claim_homework_import')
            finally:
                release_revoke.set()
            revoked = revoking.result(timeout=10)
            assert revoked.status_code == 201, revoked.text
            claimed = claiming.result(timeout=10)
            assert claimed is None or claimed['import_id'] != imported['id']
        with admin_engine.connect() as connection:
            assert connection.execute(text('SELECT status FROM homework_imports WHERE id=:id'),
                                      {'id': imported['id']}).scalar_one() == 'cancelled'
            assert connection.execute(text('SELECT count(*) FROM homework_import_runs WHERE import_id=:id'),
                                      {'id': imported['id']}).scalar_one() == 0
    finally:
        worker.dispose()


def test_no_homework_claim_on_another_date_blocks_complete_scope(client, owner, student, admin_engine):
    item = create_item(client, owner, student)
    verified = client.post(f"/api/v1/homework-items/{item['id']}/verify", headers=owner['headers'],
                           json=body(expected_revision=item['revision'], completed=True))
    assert verified.status_code == 200, verified.text
    media_id = upload(client, owner['headers'], student['id'])
    response = client.post('/api/v1/homework-imports', headers=owner['headers'], json=body(
        student_id=student['id'], target_date='2026-11-12', images=[{'media_id': media_id}]))
    assert response.status_code == 202, response.text
    imported = response.json()['data']
    draft = {'items': [], 'notes': [], 'no_homework': [{
        'id': 'different-date-none', 'image_id': imported['images'][0]['id'], 'subject': 'chinese',
        'assigned_date': '2026-11-13', 'date_raw': '2026年11月13日', 'confirmed': True,
    }]}
    seed_draft(admin_engine, imported, draft)
    saved = client.put(f"/api/v1/homework-imports/{imported['id']}/draft", headers=owner['headers'],
                       json=body(expected_revision=imported['revision'], draft=draft))
    assert saved.status_code == 200, saved.text
    day = client.get(url(student), headers=owner['headers']).json()['data']
    completed = client.put(url(student) + '/scope', headers=owner['headers'], json=body(
        expected_revision=day['revision'], scope_state='complete', subjects={
            'chinese': 'has_items', 'math': 'confirmed_none', 'english': 'confirmed_none', 'other': 'confirmed_none',
        }))
    assert completed.status_code == 409, completed.text
    assert day['pending_imports'] >= 1
    assert day['summary']['all_verified'] is False
