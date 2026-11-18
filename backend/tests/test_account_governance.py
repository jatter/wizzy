"""Governance contracts against disposable PostgreSQL and the restricted API role."""
import json
import secrets
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from conftest import database as session_database
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_question_migration import migrate

from wizzy.security import digest

BASE = '/api/v1/admin'
FUNCTION = 'public.platform_account_governance(text,text,text,uuid,jsonb,integer,timestamptz,uuid)'


@pytest.fixture
def database(tmp_path_factory):
    yield from session_database.__wrapped__(tmp_path_factory)


@pytest.fixture
def actors(admin_engine):
    accounts = []
    with admin_engine.begin() as db:
        for role in ('security_admin', 'security_admin', 'security_admin', 'operator'):
            identifier, version, token = uuid4(), uuid4(), secrets.token_urlsafe(32)
            db.execute(text('''INSERT INTO platform_accounts
                (id,email,password_hash,encrypted_totp,credential_version,role)
                VALUES(:id,:email,'unused-hash','unused-ciphertext',:version,:role)'''),
                {'id': identifier, 'email': f'{identifier}@example.test', 'version': version, 'role': role})
            db.execute(text('''INSERT INTO platform_sessions
                (id,account_id,token_hash,credential_version,created_at,expires_at)
                VALUES(:id,:account,:hash,:version,clock_timestamp(),clock_timestamp()+interval '30 minutes')'''),
                {'id': uuid4(), 'account': identifier, 'hash': digest(token), 'version': version})
            accounts.append({'id': str(identifier), 'headers': {'Authorization': f'Bearer {token}'}})
    return accounts


def body(**overrides):
    return {'operation_id': str(uuid4()), 'expected_revision': 0, 'role': 'operator',
            'status': 'disabled', 'reason': '停用离岗账号', **overrides}


def change(client, actor, target, payload=None):
    return client.post(f"{BASE}/accounts/{target['id']}/changes", headers=actor['headers'], json=payload or body())


def decision(client, actor, proposal, action='approve', payload=None):
    return client.post(f'{BASE}/account-changes/{proposal}/{action}', headers=actor['headers'],
                       json=payload or {'operation_id': str(uuid4()), 'reason': '独立核对职责'})


def detail(client, actor, target):
    return client.get(f"{BASE}/accounts/{target['id']}", headers=actor['headers'])


def history(client, actor, target, **params):
    return client.get(f"{BASE}/accounts/{target['id']}/changes", headers=actor['headers'], params=params)


def test_disable_revokes_all_sessions_and_enable_does_not_revive(client, actors, admin_engine):
    a, _, _, target = actors
    with admin_engine.begin() as db:
        db.execute(text('''INSERT INTO platform_sessions
            (id,account_id,token_hash,credential_version,created_at,expires_at,revoked_at)
            SELECT :id,account_id,:hash,credential_version,created_at,expires_at,NULL
            FROM platform_sessions WHERE account_id=:account LIMIT 1'''),
            {'id': uuid4(), 'hash': digest('other-session'), 'account': target['id']})
    result = change(client, a, target)
    assert result.status_code == 201, result.text
    value = result.json()['data']
    assert value['state'] == 'applied' and value['resulting_revision'] == 1
    assert detail(client, target, target).status_code == 401
    enabled = change(client, a, target, body(expected_revision=1, status='active'))
    assert enabled.status_code == 201
    assert detail(client, target, target).status_code == 401
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM platform_sessions WHERE account_id=:id AND revoked_at IS NOT NULL'),
                          {'id': target['id']}).scalar_one() == 2
    current = detail(client, a, target).json()['data']
    assert current['revision'] == 2 and current['status'] == 'active'
    assert set(current) == {'id', 'email', 'role', 'status', 'revision', 'created_at'}


def test_promotion_requires_separate_approval_and_replays_are_unique(client, actors, admin_engine):
    a, b, _, target = actors
    request = body(role='security_admin', status='active')
    proposal = change(client, a, target, request)
    assert proposal.status_code == 201, proposal.text
    value = proposal.json()['data']
    assert value['state'] == 'pending' and value['requires_approval'] and not value['stale']
    assert detail(client, a, target).json()['data']['role'] == 'operator'
    assert detail(client, target, target).status_code == 403
    denied = decision(client, a, value['id'])
    assert denied.status_code == 409 and denied.json()['error']['code'] == 'separate_approver_required'
    approval = {'operation_id': str(uuid4()), 'reason': '第二位管理员确认'}
    result = decision(client, b, value['id'], payload=approval)
    assert result.status_code == 200, result.text
    assert result.json()['data']['state'] == 'approved'
    assert result.json()['data']['decided_by'] == b['id']
    assert detail(client, target, target).status_code == 401
    assert decision(client, b, value['id'], payload=approval).json()['data'] == result.json()['data']
    replay = change(client, a, target, request)
    assert replay.status_code == 200 and replay.json()['data']['state'] == 'approved'
    conflict = change(client, a, target, {**request, 'reason': 'changed'})
    assert conflict.status_code == 409 and conflict.json()['error']['code'] == 'operation_conflict'
    assert decision(client, b, value['id'], 'reject', approval).json()['error']['code'] == 'operation_conflict'
    assert change(client, a, b, request).json()['error']['code'] == 'operation_conflict'
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM platform_account_changes')).scalar_one() == 1
        assert db.execute(text('SELECT count(*) FROM platform_account_decisions')).scalar_one() == 1
        assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE action='accounts.approve'")).scalar_one() == 1


def test_stale_proposal_cannot_apply_but_can_be_rejected(client, actors):
    a, b, _, target = actors
    proposal = change(client, a, target, body(role='security_admin', status='active')).json()['data']['id']
    assert change(client, b, target).status_code == 201
    assert history(client, a, target).json()['data']['items'][1]['stale']
    assert decision(client, b, proposal).json()['error']['code'] == 'revision_conflict'
    rejected = decision(client, b, proposal, 'reject')
    assert rejected.status_code == 200 and rejected.json()['data']['state'] == 'rejected'
    assert rejected.json()['data']['resulting_revision'] is None
    assert decision(client, b, proposal).json()['error']['code'] == 'already_decided'


def test_proposer_losing_permission_prevents_approval(client, actors):
    a, b, c, target = actors
    proposal = change(client, a, target, body(role='security_admin', status='active')).json()['data']['id']
    assert change(client, c, a, body(status='active')).status_code == 201
    assert decision(client, b, proposal).json()['error']['code'] == 'proposer_ineligible'
    assert decision(client, b, proposal, 'reject').status_code == 200


def test_last_admin_guard_and_self_change(client, actors, admin_engine):
    a, b, c, _ = actors
    assert change(client, a, b).status_code == 201
    assert change(client, a, c).status_code == 201
    denied = change(client, a, a)
    assert denied.status_code == 409 and denied.json()['error']['code'] == 'last_security_admin'
    assert detail(client, a, a).json()['data']['revision'] == 0
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM platform_account_changes')).scalar_one() == 2


def test_permission_denied_commits_audit_and_no_history(client, actors, admin_engine):
    a, _, _, operator = actors
    assert detail(client, operator, a).status_code == 403
    assert change(client, operator, a).status_code == 403
    assert history(client, operator, a).status_code == 403
    assert decision(client, operator, uuid4()).status_code == 403
    assert client.get(f"{BASE}/accounts/{a['id']}").status_code == 401
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE actor_id=:id AND action='access.denied'"),
                          {'id': operator['id']}).scalar_one() == 4
        assert db.execute(text('SELECT count(*) FROM platform_account_changes')).scalar_one() == 0


@pytest.mark.parametrize('invalid', [
    {'expected_revision': True}, {'expected_revision': '0'}, {'expected_revision': -1},
    {'expected_revision': 999999999}, {'role': 'owner'}, {'status': 'deleted'},
    {'reason': ''}, {'reason': '  '}, {'reason': 'a\nb'}, {'reason': 'a\x7fb'},
    {'reason': 'x'*501}, {'operation_id': 'invalid'}, {'unrecognized': True},
])
def test_request_validation(client, actors, invalid):
    assert change(client, actors[0], actors[3], body(**invalid)).status_code == 422


def test_history_pagination_noop_and_missing(client, actors):
    a, _, _, target = actors
    assert change(client, a, target, body(status='active')).status_code == 422
    assert change(client, a, target).status_code == 201
    assert change(client, a, target, body(expected_revision=1, status='active')).status_code == 201
    first = history(client, a, target, limit=1).json()['data']
    assert first['items'][0]['resulting_revision'] == 2 and first['next_cursor']
    second = history(client, a, target, limit=1, cursor=first['next_cursor']).json()['data']
    assert second['items'][0]['resulting_revision'] == 1 and second['next_cursor'] is None
    assert history(client, a, target, cursor='invalid').status_code == 422
    assert detail(client, a, {'id': str(uuid4())}).status_code == 404
    assert change(client, a, target, body()).json()['error']['code'] == 'revision_conflict'


def test_audit_failure_rolls_back_account_session_and_history(app, actors, admin_engine):
    with admin_engine.begin() as db:
        db.execute(text('''CREATE FUNCTION fail_governance_audit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.action='accounts.change' THEN RAISE EXCEPTION 'test failure'; END IF; RETURN NEW; END $$;
            CREATE TRIGGER fail_governance_audit BEFORE INSERT ON platform_audit_events
            FOR EACH ROW EXECUTE FUNCTION fail_governance_audit();'''))
    with TestClient(app, raise_server_exceptions=False) as client:
        assert change(client, actors[0], actors[3]).status_code == 500
        assert detail(client, actors[0], actors[3]).json()['data']['revision'] == 0
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM platform_account_changes')).scalar_one() == 0
        assert db.execute(text('SELECT revoked_at FROM platform_sessions WHERE account_id=:id'),
                          {'id': actors[3]['id']}).scalar_one() is None


def test_parallel_cross_demotion_keeps_one_active_admin(app, actors, admin_engine):
    a, b, c, _ = actors
    with TestClient(app) as client:
        assert change(client, a, c).status_code == 201
    def attempt(pair):
        actor, target = pair
        with TestClient(app, client=(str(uuid4()), 50000)) as client:
            return change(client, actor, target, body(status='active')).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, [(a, b), (b, a)])) == [201, 401]
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM platform_accounts WHERE role='security_admin' AND status='active'")).scalar_one() == 1


def test_parallel_approvals_create_one_decision(app, actors, admin_engine):
    a, b, c, target = actors
    with TestClient(app) as client:
        proposal = change(client, a, target, body(role='security_admin', status='active')).json()['data']['id']
    def attempt(args):
        actor, action = args
        with TestClient(app, client=(str(uuid4()), 50000)) as client:
            return decision(client, actor, proposal, action).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, [(b, 'approve'), (c, 'reject')])) == [200, 409]
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM platform_account_decisions')).scalar_one() == 1


def test_restricted_function_and_immutable_history(client, actors, database, admin_engine):
    a, _, _, target = actors
    assert change(client, a, target).status_code == 201
    with admin_engine.connect() as db:
        for function in (FUNCTION, 'public.platform_account_change_view(uuid)'):
            row = db.execute(text('SELECT prosecdef,proconfig FROM pg_proc WHERE oid=to_regprocedure(:fn)'), {'fn': function}).one()
            assert row.prosecdef and row.proconfig == ['search_path=pg_catalog, pg_temp']
            assert not db.execute(text('''SELECT EXISTS(SELECT 1 FROM pg_proc p,LATERAL aclexplode(p.proacl) a
                WHERE p.oid=to_regprocedure(:fn) AND a.grantee=0 AND a.privilege_type='EXECUTE')'''), {'fn': function}).scalar_one()
            for key in ('WIZZY_APP_ROLE', 'WIZZY_WORKER_ROLE', 'WIZZY_DISPATCHER_ROLE'):
                assert db.execute(text("SELECT has_function_privilege(:role,:fn,'EXECUTE')"),
                                  {'role': database['env'][key], 'fn': function}).scalar_one() == (key == 'WIZZY_APP_ROLE' and function == FUNCTION)
    runtime = create_engine(database['url'])
    try:
        for table in ('platform_account_changes', 'platform_account_decisions'):
            with pytest.raises(DBAPIError), runtime.begin() as db:
                db.execute(text('SELECT * FROM '+table))
            with pytest.raises(DBAPIError, match='immutable'), admin_engine.begin() as db:
                db.execute(text('UPDATE '+table+" SET reason='rewrite'"))
        with runtime.begin() as db:
            result = db.execute(text('''SELECT public.platform_account_governance(:hash,'change','test',:id,
                CAST(:payload AS jsonb),50,NULL,NULL)'''),
                {'hash': digest(a['headers']['Authorization'].split()[1]), 'id': target['id'],
                 'payload': json.dumps(body(expected_revision=True))}).scalar_one()
            assert result['status'] == 422
    finally:
        runtime.dispose()
    with pytest.raises(RuntimeError, match='Cannot discard account governance history'):
        migrate(admin_engine, database, 'downgrade', '0020_evaluation_consent')


@pytest.mark.parametrize('role,permission', [
    ('evaluation_annotator', 'platform.evaluation.annotate'),
    ('evaluation_adjudicator', 'platform.evaluation.adjudicate'),
])
def test_evaluator_role_assignment_replay_and_live_restriction(client, actors, admin_engine, role, permission):
    admin, _, _, target = actors
    payload = body(role=role, status='active', reason='分配独立评测职责')
    result = change(client, admin, target, payload)
    assert result.status_code == 201, result.text
    assert result.json()['data']['state'] == 'applied'
    assert result.json()['data']['requires_approval'] is False
    assert change(client, admin, target, payload).status_code == 200
    view = detail(client, admin, target).json()['data']
    assert view['role'] == role and view['revision'] == 1
    assert client.get(f'{BASE}/auth/me', headers=target['headers']).status_code == 401
    # Role changes revoke existing sessions. Represent a fresh login after reassignment.
    token = secrets.token_urlsafe(32)
    with admin_engine.begin() as db:
        db.execute(text('''INSERT INTO platform_sessions
            (id,account_id,token_hash,credential_version,created_at,expires_at)
            SELECT :session,id,:hash,credential_version,clock_timestamp(),clock_timestamp()+interval '30 minutes'
            FROM platform_accounts WHERE id=:id'''),
            {'session': uuid4(), 'hash': digest(token), 'id': target['id']})
    target = {**target, 'headers': {'Authorization': f'Bearer {token}'}}
    identity = client.get(f'{BASE}/auth/me', headers=target['headers'])
    assert identity.status_code == 200, identity.text
    assert set(identity.json()['data']['permissions']) == {'platform.evaluation.read', permission}
    assert client.get(f'{BASE}/evaluation-datasets', headers=target['headers']).status_code == 200
    assert client.get(f'{BASE}/accounts', headers=target['headers']).status_code == 403
    assert change(client, admin, target, body(expected_revision=1, status='active')).status_code == 201
    assert client.get(f'{BASE}/evaluation-datasets', headers=target['headers']).status_code == 401
    changes = history(client, admin, target).json()['data']['items']
    assert len(changes) == 2 and any(c['role'] == role for c in changes)
