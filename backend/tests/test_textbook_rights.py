"""Real PostgreSQL rights state machine, independent review and capability boundaries."""
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
import test_platform_identity as identities
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from wizzy.security import digest, passwords

platform = identities.platform
session = identities.session
BASE = '/api/v1/admin/textbook-editions'
FIELDS = ('title', 'stage', 'grade', 'subject', 'publisher', 'edition_label', 'academic_year', 'volume', 'rights')


def rights(**changes):
    return {'basis': 'publisher_contract', 'holder': '示例出版社', 'reference': 'RIGHTS-2026-001',
            'scopes': ['storage', 'ocr', 'retrieval', 'preview'], 'regions': ['CN'],
            'valid_from': '2020-01-01', 'valid_until': '2099-12-31', **changes}


def payload(**changes):
    return {'operation_id': str(uuid4()), 'code': 'BOOK-' + uuid4().hex[:12].upper(),
            'title': '数学', 'stage': 'primary', 'grade': 3, 'subject': '数学', 'publisher': '示例出版社',
            'edition_label': '2026修订版', 'academic_year': '2026-2027', 'volume': 'first',
            'rights': rights(), 'reason': '核验教材授权', **changes}


def create(client, headers, **changes):
    response = client.post(BASE, headers=headers, json=payload(**changes))
    assert response.status_code == 201, response.text
    return response.json()['data']


def revision(item, **changes):
    return {**{k: item[k] for k in FIELDS}, 'operation_id': str(uuid4()),
            'expected_revision': item['revision'], 'reason': '修订授权信息', **changes}


def action(client, headers, item, name, expected=201, **changes):
    response = client.post(f"{BASE}/{item['id']}/{name}", headers=headers, json={
        'operation_id': str(uuid4()), 'expected_revision': item['revision'], 'reason': '核对权利凭据', **changes})
    assert response.status_code == expected, response.text
    return response.json()['data'] if expected in (200, 201) else response.json()['error']


@pytest.fixture
def reviewer(client, platform, admin_engine):
    account_id = uuid4()
    email = f'reviewer-{account_id}@example.test'
    with admin_engine.begin() as db:
        db.execute(text('''INSERT INTO platform_accounts(id,email,password_hash,encrypted_totp,credential_version,role)
            VALUES(:id,:email,:password,'unused',:version,'security_admin')'''),
            {'id': account_id, 'email': email, 'password': passwords.hash(identities.PASSWORD),
             'version': uuid4()})
    return {'id': account_id, 'headers': session(client, {'email': email})}



@pytest.fixture
def historical_reviewer(admin_engine):
    """Seed a session for migration tests whose schema predates captcha login."""
    account_id, version, token = uuid4(), uuid4(), uuid4().hex
    with admin_engine.begin() as db:
        db.execute(text("""INSERT INTO platform_accounts
            (id,email,password_hash,encrypted_totp,credential_version,role)
            VALUES(:id,:email,'unused','unused',:version,'security_admin')"""),
            {'id': account_id, 'email': f'historical-{account_id}@example.test', 'version': version})
        db.execute(text("""INSERT INTO platform_sessions(id,account_id,credential_version,token_hash)
            VALUES(:id,:account,:version,:hash)"""),
            {'id': uuid4(), 'account': account_id, 'version': version, 'hash': digest(token)})
    headers = {'Authorization': 'Bearer ' + token}
    return {'id': account_id, 'headers': headers, 'historical_headers': headers}


def test_complete_flow_history_and_audit(client, platform, reviewer, admin_engine):
    headers = session(client, platform)
    item = create(client, headers, code=' book_demo ', title=' 数学 ')
    assert item['code'] == 'BOOK_DEMO' and item['title'] == '数学'
    assert item['state'] == 'draft' and item['availability'] == 'metadata_only'
    submitted = action(client, headers, item, 'submit')
    assert submitted['state'] == 'pending_review'
    assert action(client, headers, submitted, 'approve', 409)['code'] == 'independent_reviewer_required'
    approved = action(client, reviewer['headers'], submitted, 'approve')
    assert approved['availability'] == 'rights_approved' and approved['content_available'] is False
    assert client.post(f"{BASE}/{item['id']}/revisions", headers=headers, json=revision(approved)).status_code == 409
    withdrawn = action(client, headers, approved, 'withdraw')
    assert withdrawn['availability'] == 'withdrawn'
    revised = client.post(f"{BASE}/{item['id']}/revisions", headers=headers,
                          json=revision(withdrawn, rights=rights(reference='RIGHTS-UPDATED'))).json()['data']
    assert revised['state'] == 'draft' and revised['submitted_by'] is None
    resubmitted = action(client, headers, revised, 'submit')
    rejected = action(client, reviewer['headers'], resubmitted, 'reject')
    assert rejected['state'] == 'draft'
    history = client.get(f"{BASE}/{item['id']}/revisions?limit=2", headers=headers).json()['data']
    assert [r['revision'] for r in history['items']] == [7, 6]
    cursor = history['next_cursor']
    older = client.get(f"{BASE}/{item['id']}/revisions", headers=headers,
                       params={'cursor': cursor, 'limit': 100}).json()['data']
    assert [r['revision'] for r in older['items']] == [5, 4, 3, 2, 1]
    assert older['next_cursor'] is None
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE actor_id IN (:a,:b) AND action LIKE 'textbooks.%'"),
                          {'a': platform['id'], 'b': reviewer['id']}).scalar_one() == 7


@pytest.mark.parametrize('changes', [
    {'title': ' '}, {'title': 'a' * 121}, {'title': 'x\ny'}, {'grade': True}, {'grade': '3'}, {'grade': 3.0},
    {'stage': 'junior', 'grade': 3}, {'stage': 'senior', 'grade': 9}, {'grade': 13}, {'volume': 'third'},
    {'academic_year': '2026-2028'}, {'academic_year': '1999-2000'}, {'rights': {}}, {'code': 'X'},
    {'rights': rights(scopes=['ocr'])}, {'rights': rights(scopes=['storage', 'storage'])},
    {'rights': rights(regions=['cn'])}, {'rights': rights(regions=['CN', 'CN'])},
    {'rights': rights(valid_from='2026-02-30')}, {'rights': rights(valid_until='2020-01-01T00:00:00Z')},
    {'rights': rights(valid_from='2099-01-01', valid_until='2020-01-01')},
    {'rights': rights(valid_from=0)}, {'rights': rights(reference='x\ny')}, {'extra': True},
])
def test_invalid_fields(client, platform, changes):
    assert client.post(BASE, headers=session(client, platform), json=payload(**changes)).status_code == 422


def test_missing_expired_future_rights_and_same_title(client, platform, reviewer, admin_engine):
    headers = session(client, platform)
    empty = create(client, headers, rights=None)
    assert action(client, headers, empty, 'submit', 422)['code'] == 'textbook_rights_incomplete'
    expired = create(client, headers, rights=rights(valid_until='2020-01-01'))
    assert expired['availability'] == 'expired'
    assert action(client, headers, expired, 'submit', 422)['code'] == 'textbook_rights_incomplete'
    future = create(client, headers, rights=rights(valid_from='2090-01-01'))
    pending = action(client, headers, future, 'submit')
    approved = action(client, reviewer['headers'], pending, 'approve')
    assert approved['availability'] == 'scheduled' and not approved['content_available']
    assert len({empty['id'], expired['id'], future['id']}) == 3
    with admin_engine.connect() as db:
        for instant, expected in [('2099-12-31T15:59:59Z', 'rights_approved'),
                                  ('2099-12-31T16:00:00Z', 'expired'), ('2089-12-31T15:59:59Z', 'scheduled')]:
            assert db.execute(text("SELECT public.textbook_availability(CAST(:c AS jsonb),'approved',:at)"),
                              {'c': json.dumps({k: approved[k] for k in FIELDS}), 'at': instant}).scalar_one() == expected


def test_current_editor_and_submitter_cannot_review(client, platform, reviewer):
    headers = session(client, platform)
    item = create(client, headers)
    edited = client.post(f"{BASE}/{item['id']}/revisions", headers=reviewer['headers'], json=revision(item)).json()['data']
    pending = action(client, headers, edited, 'submit')
    assert action(client, reviewer['headers'], pending, 'reject', 409)['code'] == 'independent_reviewer_required'
    assert action(client, headers, pending, 'reject', 409)['code'] == 'independent_reviewer_required'


def test_replay_conflicts_state_checks_and_pagination(client, platform):
    headers = session(client, platform)
    body = payload()
    first = client.post(BASE, headers=headers, json=body).json()['data']
    assert client.post(BASE, headers=headers, json=body).json()['data'] == first
    assert client.post(BASE, headers=headers, json={**body, 'title': '语文'}).status_code == 409
    assert client.post(BASE, headers=headers, json={**body, 'operation_id': str(uuid4())}).json()['error']['code'] == 'textbook_code_conflict'
    for op in ('approve', 'reject', 'withdraw'):
        assert action(client, headers, first, op, 409)['code'] == 'textbook_state_conflict'
    operation = str(uuid4())
    pending = action(client, headers, first, 'submit', operation_id=operation)
    assert action(client, headers, first, 'submit', 200, operation_id=operation) == pending
    assert action(client, headers, first, 'submit', 409)['code'] == 'textbook_revision_conflict'
    other = create(client, headers, title='另一本教材', edition_label=first['code'])
    page = client.get(BASE, headers=headers, params={'q': first['code'], 'limit': 1}).json()['data']
    assert page['items'] == [other]
    page2 = client.get(BASE, headers=headers, params={'q': first['code'], 'limit': 1, 'cursor': page['next_cursor']}).json()['data']
    assert page2['items'] == [pending] and page2['next_cursor'] is None
    filtered = client.get(BASE, headers=headers, params={'q': first['code'], 'state': 'pending_review'}).json()['data']
    assert filtered['items'] == [pending]
    for path in (f'/{uuid4()}', f'/{uuid4()}/revisions'):
        assert client.get(BASE + path, headers=headers).status_code == 404
    assert client.get(BASE, headers=headers, params={'cursor': 'invalid'}).status_code == 422


def test_permission_and_session_rechecked_before_replay(client, platform, owner, admin_engine):
    headers = session(client, platform)
    body = payload()
    item = client.post(BASE, headers=headers, json=body).json()['data']
    assert client.get(BASE, headers=owner['headers']).status_code == 401
    with admin_engine.begin() as db:
        db.execute(text("UPDATE platform_accounts SET role='operator' WHERE id=:id"), {'id': platform['id']})
    assert client.get(BASE, headers=headers).status_code == 200
    assert client.post(BASE, headers=headers, json=body).status_code == 403
    assert action(client, headers, item, 'approve', 403)['code'] == 'forbidden'
    with admin_engine.begin() as db:
        assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE actor_id=:id AND action='access.denied'"), {'id': platform['id']}).scalar_one() == 2
        db.execute(text("UPDATE platform_accounts SET status='disabled' WHERE id=:id"), {'id': platform['id']})
    assert client.get(BASE, headers=headers).status_code == 401


@pytest.mark.parametrize('change', [
    {'operation_id': 'bad'}, {'expected_revision': True}, {'expected_revision': 1e100},
    {'rights': rights(valid_from='2026-02-30')}, {'rights': rights(scopes='storage')},
    {'rights': rights(regions={})}, {'title': []}, {'grade': 3.0}, {'rights': rights(scopes=[True])},
    {'rights': rights(holder=None)}, {'extra': 1}, {'reason': '\t理由'},
])
def test_direct_sql_validation(client, platform, app, change):
    headers = session(client, platform)
    item = create(client, headers)
    with app.state.engine.begin() as db:
        result = db.execute(text("""SELECT public.textbook_request(:hash,'revise','sql-validation',
            CAST(:payload AS jsonb),:id,50,NULL,NULL,'',NULL)"""),
            {'hash': digest(headers['Authorization'].split()[1]), 'id': item['id'],
             'payload': json.dumps(revision(item, **change))}).scalar_one()
    assert result['status'] == 422


def test_concurrent_replay_and_cas(client, app, platform):
    headers = session(client, platform)
    body = payload()
    def post(data):
        with TestClient(app, client=(f'parallel-{uuid4()}', 50000)) as other:
            return other.post(BASE, headers=headers, json=data)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(post, [body, body]))
    assert sorted(r.status_code for r in results) == [200, 201]
    item = results[0].json()['data']
    def edit(data):
        with TestClient(app, client=(f'parallel-{uuid4()}', 50000)) as other:
            return other.post(f"{BASE}/{item['id']}/revisions", headers=headers, json=data).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(edit, [revision(item), revision(item)])) == [201, 409]


def test_audit_rollback_and_immutable_history(client, platform, app, admin_engine):
    headers = session(client, platform)
    item = create(client, headers)
    with admin_engine.begin() as db:
        db.execute(text('''CREATE FUNCTION public.fail_textbook_audit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.action='textbooks.submit' THEN RAISE EXCEPTION 'injected failure'; END IF; RETURN NEW; END $$;
            CREATE TRIGGER fail_textbook_audit BEFORE INSERT ON platform_audit_events
                FOR EACH ROW EXECUTE FUNCTION public.fail_textbook_audit()'''))
    try:
        with TestClient(app, raise_server_exceptions=False) as failing:
            response = failing.post(f"{BASE}/{item['id']}/submit", headers=headers, json={
                'operation_id': str(uuid4()), 'expected_revision': 1, 'reason': '提交'})
        assert response.status_code == 500 and 'injected' not in response.text
    finally:
        with admin_engine.begin() as db:
            db.execute(text('DROP TRIGGER fail_textbook_audit ON platform_audit_events; DROP FUNCTION public.fail_textbook_audit()'))
    assert client.get(f"{BASE}/{item['id']}", headers=headers).json()['data'] == item
    for command in ('UPDATE textbook_edition_revisions SET reason=\'篡改\' WHERE edition_id=:id',
                    'DELETE FROM textbook_edition_revisions WHERE edition_id=:id', 'TRUNCATE textbook_edition_revisions CASCADE'):
        with pytest.raises(DBAPIError), admin_engine.begin() as db:
            db.execute(text(command), {'id': item['id']})
