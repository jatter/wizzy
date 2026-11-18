"""Cross-account revocation contracts against isolated PostgreSQL and restricted roles."""
import secrets
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_account_governance import actors as actors  # noqa: F401
from test_account_governance import database as database  # noqa: F401
from test_question_migration import migrate

from wizzy.security import digest

FUNCTION = 'public.platform_account_session_revocations(text,text,text,uuid,jsonb,integer,timestamptz,uuid)'
BASE = '/api/v1/admin/accounts'


def payload(**overrides):
    return {'operation_id': str(uuid4()), 'reason': '账号异常，撤销全部已有会话', **overrides}


def revoke(client, actor, target, body=None):
    return client.post(f"{BASE}/{target['id']}/session-revocations", headers=actor['headers'], json=body or payload())


def history(client, actor, target, **params):
    return client.get(f"{BASE}/{target['id']}/session-revocations", headers=actor['headers'], params=params)


def new_session(admin_engine, account, *, expired=False, outdated=False):
    token = secrets.token_urlsafe(32)
    with admin_engine.begin() as db:
        # The login capability uses this same account row lock.
        db.execute(text('SELECT id FROM platform_accounts WHERE id=:id FOR UPDATE'), {'id': account['id']})
        db.execute(text('''INSERT INTO platform_sessions(id,account_id,token_hash,credential_version,created_at,expires_at)
            SELECT :session,id,:hash,CASE WHEN :outdated THEN gen_random_uuid() ELSE credential_version END,
            clock_timestamp(),clock_timestamp()+CASE WHEN :expired THEN interval '-1 minute' ELSE interval '30 minutes' END
            FROM platform_accounts WHERE id=:id'''),
            {'session': uuid4(), 'id': account['id'], 'hash': digest(token), 'expired': expired, 'outdated': outdated})
    return {'id': account['id'], 'headers': {'Authorization': f'Bearer {token}'}}


def test_revoke_all_count_and_no_account_change(client, actors, admin_engine):
    admin, _, _, target = actors
    additional = new_session(admin_engine, target)
    new_session(admin_engine, target, expired=True)
    new_session(admin_engine, target, outdated=True)
    before = client.get(f"{BASE}/{target['id']}", headers=admin['headers']).json()['data']
    body = payload(reason='  核对后撤销会话  ')
    result = revoke(client, admin, target, body)
    assert result.status_code == 201, result.text
    receipt = result.json()['data']
    assert receipt['revoked_count'] == 2 and receipt['reason'] == body['reason'].strip()
    assert receipt['account_id'] == target['id'] and receipt['actor_id'] == admin['id']
    assert set(receipt) == {'id', 'account_id', 'actor_id', 'operation_id', 'reason', 'revoked_count', 'created_at'}
    assert client.get('/api/v1/admin/auth/me', headers=target['headers']).status_code == 401
    assert client.get('/api/v1/admin/auth/me', headers=additional['headers']).status_code == 401
    assert client.get(f"{BASE}/{target['id']}", headers=admin['headers']).json()['data'] == before
    assert history(client, admin, target).json()['data']['items'] == [receipt]
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM platform_sessions WHERE account_id=:id AND revoked_at IS NOT NULL'), {'id': target['id']}).scalar_one() == 4
        assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE action='accounts.sessions.revoke'")).scalar_one() == 1


def test_replay_preserves_new_login_and_conflicting_key(client, actors, admin_engine):
    admin, second, _, target = actors
    body = payload()
    receipt = revoke(client, admin, target, body).json()['data']
    fresh = new_session(admin_engine, target)
    replay = revoke(client, admin, target, body)
    assert replay.status_code == 200 and replay.json()['data'] == receipt
    assert client.get('/api/v1/admin/auth/me', headers=fresh['headers']).status_code == 200
    for other, request in ((target, {**body, 'reason': '不同原因'}), (second, body)):
        conflict = revoke(client, admin, other, request)
        assert conflict.status_code == 409 and conflict.json()['error']['code'] == 'operation_conflict'
    assert client.get('/api/v1/admin/auth/me', headers=fresh['headers']).status_code == 200
    assert revoke(client, admin, target).json()['data']['revoked_count'] == 1
    assert revoke(client, admin, target).json()['data']['revoked_count'] == 0
    assert len(history(client, admin, target).json()['data']['items']) == 3


def test_concurrent_replay_is_one_operation(client, app, actors, admin_engine):
    admin, _, _, target = actors
    body = payload()
    def attempt(_):
        with TestClient(app, client=(str(uuid4()), 50000)) as browser:
            result = revoke(browser, admin, target, body)
            return result.status_code, result.json()['data']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sorted(r[0] for r in results) == [200, 201]
    assert results[0][1] == results[1][1]
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM platform_session_revocations')).scalar_one() == 1


def test_authorization_is_rechecked_and_denial_audited(client, actors, admin_engine):
    admin, other, _, operator = actors
    assert revoke(client, operator, admin).status_code == 403
    assert history(client, operator, admin).status_code == 403
    assert revoke(client, admin, admin).json()['error']['code'] == 'self_session_revoke'
    body = payload()
    assert revoke(client, admin, operator, body).status_code == 201
    assert revoke(client, other, admin).status_code == 201
    assert revoke(client, admin, operator, body).status_code == 401
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE action='access.denied' AND actor_id=:id"), {'id': operator['id']}).scalar_one() == 2


@pytest.mark.parametrize('body', [payload(reason=' '), payload(reason='x\n'), payload(reason='x'*501),
    payload(reason=123), payload(operation_id='bad'), {**payload(), 'token': 'forbidden'}])
def test_invalid_requests_are_rejected(client, actors, body):
    admin, _, _, target = actors
    assert revoke(client, admin, target, body).status_code == 422
    assert history(client, admin, target).json()['data']['items'] == []


def test_history_cursor_with_fractional_timestamps(client, actors, admin_engine):
    admin, _, _, target = actors
    for fraction in ('12', '12345', '123451'):
        with admin_engine.begin() as db:
            db.execute(text('''INSERT INTO platform_session_revocations
                (id,account_id,actor_id,operation_id,reason,revoked_count,created_at)
                VALUES(:id,:account,:actor,:operation,'已核对',0,CAST(:time AS timestamptz))'''),
                {'id': uuid4(), 'account': target['id'], 'actor': admin['id'], 'operation': uuid4(),
                 'time': f'2026-10-26T00:00:00.{fraction}+00:00'})
    ids = []
    cursor = None
    while True:
        response = history(client, admin, target, limit=1, **({'cursor': cursor} if cursor else {}))
        assert response.status_code == 200, response.text
        page = response.json()['data']
        ids.extend(r['id'] for r in page['items'])
        cursor = page['next_cursor']
        if not cursor:
            break
    assert len(ids) == len(set(ids)) == 3
    assert history(client, admin, target, cursor='invalid').status_code == 422


def test_restricted_capability_and_immutable_history(client, actors, database, admin_engine):
    admin, _, _, target = actors
    assert revoke(client, admin, target).status_code == 201
    with admin_engine.connect() as db:
        meta = db.execute(text('SELECT prosecdef,proconfig FROM pg_proc WHERE oid=to_regprocedure(:fn)'), {'fn': FUNCTION}).one()
        assert meta.prosecdef and meta.proconfig == ['search_path=pg_catalog, pg_temp']
        assert db.execute(text("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid='platform_session_revocations'::regclass")).scalar_one()
        assert not db.execute(text('''SELECT EXISTS(SELECT 1 FROM pg_proc p,LATERAL aclexplode(p.proacl) a
            WHERE p.oid=to_regprocedure(:fn) AND a.grantee=0 AND a.privilege_type='EXECUTE')'''), {'fn': FUNCTION}).scalar_one()
        for key in ('WIZZY_APP_ROLE', 'WIZZY_WORKER_ROLE', 'WIZZY_DISPATCHER_ROLE'):
            assert db.execute(text("SELECT has_function_privilege(:role,:fn,'EXECUTE')"), {'role': database['env'][key], 'fn': FUNCTION}).scalar_one() == (key == 'WIZZY_APP_ROLE')
            for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE'):
                assert not db.execute(text("SELECT has_table_privilege(:role,'platform_session_revocations',:privilege)"), {'role': database['env'][key], 'privilege': privilege}).scalar_one()
    for statement in ('UPDATE platform_session_revocations SET reason=\'改写\'', 'DELETE FROM platform_session_revocations'):
        with pytest.raises(DBAPIError, match='immutable'), admin_engine.begin() as db:
            db.execute(text(statement))
    runtime = create_engine(database['url'])
    try:
        for statement in ('SELECT * FROM platform_session_revocations', 'TRUNCATE platform_session_revocations'):
            with pytest.raises(DBAPIError), runtime.begin() as db:
                db.execute(text(statement))
    finally:
        runtime.dispose()
    with pytest.raises(RuntimeError, match='Cannot discard session revocation history'):
        migrate(admin_engine, database, 'downgrade', '0030_evaluation_runs')


def test_removed_permission_blocks_replay_and_history_checks_target(client, actors, admin_engine):
    admin, _, _, target = actors
    body = payload()
    assert revoke(client, admin, target, body).status_code == 201
    fresh = new_session(admin_engine, target)
    with admin_engine.begin() as db:
        db.execute(text("DELETE FROM platform_role_permissions WHERE role='security_admin' AND permission='platform.accounts.sessions.revoke'"))
    assert revoke(client, admin, target, body).status_code == 403
    assert client.get('/api/v1/admin/auth/me', headers=fresh['headers']).status_code == 200
    missing = {'id': str(uuid4())}
    assert history(client, admin, missing).status_code == 404
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM platform_session_revocations')).scalar_one() == 1
        assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE action='access.denied' AND actor_id=:id"), {'id': admin['id']}).scalar_one() == 1


def test_missing_target_has_no_receipt(client, actors, admin_engine):
    assert revoke(client, actors[0], {'id': str(uuid4())}).status_code == 404
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM platform_session_revocations')).scalar_one() == 0


def test_audit_failure_rolls_back_revocations_and_receipt(app, actors, admin_engine):
    admin, _, _, target = actors
    with admin_engine.begin() as db:
        db.execute(text('''CREATE FUNCTION fail_session_audit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.action='accounts.sessions.revoke' THEN RAISE EXCEPTION 'test failure'; END IF; RETURN NEW; END $$;
            CREATE TRIGGER fail_session_audit BEFORE INSERT ON platform_audit_events
            FOR EACH ROW EXECUTE FUNCTION fail_session_audit();'''))
    with TestClient(app, raise_server_exceptions=False) as client:
        assert revoke(client, admin, target).status_code == 500
        assert client.get('/api/v1/admin/auth/me', headers=target['headers']).status_code == 200
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM platform_session_revocations')).scalar_one() == 0
        assert db.execute(text('SELECT revoked_at FROM platform_sessions WHERE account_id=:id'), {'id': target['id']}).scalar_one() is None
