"""Model registry metadata never acts as grading evidence or production approval."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
import test_platform_identity as identity_fixtures
from fastapi.testclient import TestClient
from sqlalchemy import text

platform = identity_fixtures.platform
session = identity_fixtures.session

BASE = '/api/v1/admin/models'


def config(**overrides):
    return {'provider': 'example-provider', 'model_key': 'vision-math-' + uuid4().hex,
            'fixed_version': '2026-10-01', 'region': 'cn-east', 'display_name': '数学视觉模型',
            'purposes': ['structure', 'verify'], 'secret_ref': 'secret://providers/example/primary',
            'contract_status': 'approved', 'contract_reference': 'contract-2026-001',
            'contract_expires_on': (date.today() + timedelta(days=60)).isoformat(),
            'input_price_micros': 1000000, 'output_price_micros': None, 'enabled': False,
            'reason': '首次登记，未完成评测', **overrides}


def create(client, headers, **overrides):
    return client.post(BASE, headers=headers, json={'operation_id': str(uuid4()), **config(**overrides)})


def revise(client, headers, model, **overrides):
    mutable = config(**overrides)
    for key in ('provider', 'model_key', 'fixed_version', 'region'):
        mutable.pop(key)
    return client.post(BASE + '/' + model['id'] + '/revisions', headers=headers,
                       json={'operation_id': str(uuid4()), 'expected_revision': model['revision'], **mutable})


def test_registry_requires_platform_session(client, owner):
    for headers in ({}, owner['headers']):
        assert client.get(BASE, headers=headers).status_code == 401
        assert create(client, headers).status_code == 401


def test_registry_round_trip_no_secrets_or_fake_scores(client, platform, admin_engine):
    headers = session(client, platform)
    response = create(client, headers)
    assert response.status_code == 201, response.text
    item = response.json()['data']
    assert item['revision'] == 1 and item['credential_configured'] is True
    assert item['accuracy'] is None and item['evaluation_status'] == 'not_evaluated'
    assert item['production_eligible'] is False and item['enabled'] is False
    assert item['input_price_micros'] == 1000000 and item['output_price_micros'] is None
    assert client.get(BASE+'/'+item['id'], headers=headers).json()['data'] == item
    listed = client.get(BASE, headers=headers).json()['data']
    assert any(x['id'] == item['id'] for x in listed['items'])
    updated = revise(client, headers, item, display_name='已维护的视觉模型', enabled=True)
    assert updated.status_code == 201, updated.text
    assert updated.json()['data']['revision'] == 2
    assert updated.json()['data']['enabled'] is True
    assert updated.json()['data']['production_eligible'] is False
    history = client.get(BASE+'/'+item['id']+'/revisions', headers=headers)
    assert [x['revision'] for x in history.json()['data']['items']] == [2, 1]
    for body in (response.text, str(listed), updated.text, history.text):
        assert 'secret://' not in body and 'secret_ref' not in body
        assert 'password_hash' not in body and 'encrypted_totp' not in body
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE actor_id=:actor AND action IN ('models.create','models.revise')"),
                          {'actor': platform['id']}).scalar_one() == 2


@pytest.mark.parametrize('overrides', [
    {'fixed_version': 'latest'}, {'fixed_version': 'vision-latest'}, {'fixed_version': 'auto'},
    {'secret_ref': 'sk-do-not-store-this'}, {'secret_ref': 'https://secret.example/key'},
    {'purposes': []}, {'purposes': ['chat']}, {'purposes': ['verify', 'verify']},
    {'input_price_micros': -1}, {'input_price_micros': 1.2}, {'input_price_micros': True},
    {'contract_status': 'approved', 'contract_reference': None},
    {'contract_status': 'approved', 'contract_expires_on': None},
    {'enabled': True, 'contract_status': 'pending'},
    {'enabled': True, 'contract_expires_on': '2020-01-01'},
    {'enabled': True, 'secret_ref': None}, {'display_name': '  '}, {'reason': ''},
    {'provider': 'Bad Provider'}, {'region': 'x\nsecret'}, {'api_key': 'sk-raw'},
])
def test_registry_rejects_invalid_metadata(client, platform, overrides):
    headers = session(client, platform)
    response = create(client, headers, **overrides)
    assert response.status_code == 422, response.text
    assert 'sk-raw' not in response.text and 'sk-do-not-store-this' not in response.text


def test_idempotency_identity_and_cas(client, platform):
    headers = session(client, platform)
    payload = {'operation_id': str(uuid4()), **config()}
    first = client.post(BASE, headers=headers, json=payload)
    assert first.status_code == 201, first.text
    item = first.json()['data']
    assert client.post(BASE, headers=headers, json=payload).json()['data'] == item
    assert client.post(BASE, headers=headers, json={**payload, 'display_name': '改变'}).status_code == 409
    assert create(client, headers, model_key=item['model_key']).status_code == 409
    assert revise(client, headers, item).status_code == 201
    assert revise(client, headers, item).status_code == 409
    assert client.post(BASE, headers=headers, json=payload).json()['data'] == item
    mutated_identity = {k: v for k, v in payload.items() if k not in ('provider','model_key','fixed_version','region')}
    mutated_identity.update(expected_revision=2, provider='another-provider')
    assert client.post(BASE+'/'+item['id']+'/revisions', headers=headers, json=mutated_identity).status_code == 422


def test_concurrent_revision_one_wins_and_replay_is_stable(client, platform, app):
    headers = session(client, platform)
    item = create(client, headers).json()['data']
    def write(_):
        with TestClient(app) as other:
            return revise(other, headers, item)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, range(2)))
    assert sorted(r.status_code for r in results) == [201, 409]


def test_live_permissions_and_operator_read_only(client, platform, admin_engine):
    headers = session(client, platform)
    item = create(client, headers).json()['data']
    with admin_engine.begin() as db:
        db.execute(text("UPDATE platform_accounts SET role='operator' WHERE id=:id"), {'id': platform['id']})
    assert client.get(BASE, headers=headers).status_code == 200
    assert create(client, headers, fixed_version='2026-10-02').status_code == 403
    assert revise(client, headers, item).status_code == 403
    with admin_engine.begin() as db:
        db.execute(text("UPDATE platform_accounts SET status='disabled' WHERE id=:id"), {'id': platform['id']})
    assert client.get(BASE+'/'+item['id'], headers=headers).status_code == 401


def test_registry_filters_pagination_and_not_found(client, platform):
    headers = session(client, platform)
    first = create(client, headers, fixed_version='2026-10-03', display_name='甲模型').json()['data']
    second = create(client, headers, fixed_version='2026-10-04', display_name='乙模型').json()['data']
    page = client.get(BASE, headers=headers, params={'q':'模型','limit':1}).json()['data']
    assert page['items'][0]['id'] == second['id'] and page['next_cursor']
    next_page = client.get(BASE, headers=headers, params={'q':'模型','limit':1, 'cursor':page['next_cursor']}).json()['data']
    assert next_page['items'][0]['id'] == first['id']
    assert client.get(BASE, headers=headers, params={'q':'不存在'+uuid4().hex}).json()['data']['items'] == []
    assert client.get(BASE, headers=headers, params={'q':'甲模型','enabled':True}).json()['data']['items'] == []
    assert client.get(BASE, headers=headers, params={'cursor':'invalid'}).status_code == 422
    assert client.get(BASE+'/'+str(uuid4()), headers=headers).status_code == 404


def test_audit_failure_rolls_back_model_revision(client, platform, admin_engine, app):
    headers = session(client, platform)
    item = create(client, headers).json()['data']
    with admin_engine.begin() as db:
        db.execute(text('''CREATE FUNCTION public.fail_model_audit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.action='models.revise' THEN RAISE EXCEPTION 'model audit failure'; END IF; RETURN NEW; END $$;
            CREATE TRIGGER fail_model_audit BEFORE INSERT ON platform_audit_events
            FOR EACH ROW EXECUTE FUNCTION public.fail_model_audit()'''))
    try:
        with TestClient(app, raise_server_exceptions=False) as failing:
            response = revise(failing, headers, item, display_name='不应保存')
        assert response.status_code == 500 and 'model audit failure' not in response.text
    finally:
        with admin_engine.begin() as db:
            db.execute(text('DROP TRIGGER fail_model_audit ON platform_audit_events; DROP FUNCTION public.fail_model_audit()'))
    assert client.get(BASE+'/'+item['id'], headers=headers).json()['data']['revision'] == 1
    assert len(client.get(BASE+'/'+item['id']+'/revisions', headers=headers).json()['data']['items']) == 1


@pytest.mark.parametrize('microseconds', [0, 123450, 123456])
def test_expired_contract_projection_preserves_config_and_history(client, platform, admin_engine, microseconds):
    headers = session(client, platform)
    item = create(client, headers).json()['data']
    yesterday = (datetime.now(ZoneInfo('Asia/Shanghai')).date() - timedelta(days=1)).isoformat()
    with admin_engine.begin() as db:
        db.execute(text("""INSERT INTO model_catalog_revisions
            (id,model_id,revision,config,reason,actor_id,operation_id,request_snapshot,created_at)
            SELECT gen_random_uuid(),model_id,2,
                config || jsonb_build_object('enabled',true,'contract_expires_on',CAST(:expiry AS text)),
                '历史启用记录',actor_id,:operation,request_snapshot,
                date_trunc('second',clock_timestamp()-interval '2 days')+:micros*interval '1 microsecond'
            FROM model_catalog_revisions WHERE model_id=:id AND revision=1"""),
            {'id': item['id'], 'expiry': yesterday, 'operation': uuid4(), 'micros': microseconds})
        db.execute(text('UPDATE model_catalog SET current_revision=2 WHERE id=:id'), {'id': item['id']})
        before = db.execute(text("SELECT count(*) FROM platform_audit_events WHERE actor_id=:actor"),
                            {'actor': platform['id']}).scalar_one()
    current = client.get(BASE+'/'+item['id'], headers=headers).json()['data']
    assert current['enabled'] is True
    assert current.get('catalog_status') == 'contract_expired'
    assert current['production_eligible'] is False and current['accuracy'] is None
    history = client.get(BASE+'/'+item['id']+'/revisions', headers=headers).json()['data']['items']
    # This synthetic historical revision predates registration; pagination is by time, not revision.
    assert {entry['revision']: entry['catalog_status'] for entry in history} == {1: 'disabled', 2: 'contract_expired'}
    assert [entry['created_at'] for entry in history] == sorted(
        (entry['created_at'] for entry in history), reverse=True)
    page = client.get(BASE, headers=headers, params={'q':item['model_key'],'enabled':True}).json()['data']
    assert page['items'][0]['created_at'] == current['created_at']
    assert page['items'][0]['registered_at'] == current['registered_at']
    assert page['items'] == [current]
    assert next(entry for entry in history if entry['revision'] == 2) == current
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM model_catalog_revisions WHERE model_id=:id'),
                          {'id': item['id']}).scalar_one() == 2
        assert db.execute(text('SELECT count(*) FROM platform_audit_events WHERE actor_id=:actor'),
                          {'actor': platform['id']}).scalar_one() == before


@pytest.mark.parametrize('overrides', [
    {'enabled':True,'contract_status':'pending'}, {'enabled':True,'secret_ref':None},
])
def test_metadata_constraint_rejects_inconsistent_enabled_config(admin_engine, overrides):
    from psycopg.types.json import Jsonb
    metadata = config(**overrides)
    for key in ('provider','model_key','fixed_version','region','reason'):
        metadata.pop(key)
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT public.model_metadata_valid(:config)'),
                          {'config':Jsonb(metadata)}).scalar_one() is False


@pytest.mark.parametrize(('at','status'), [
    ('2026-10-26T15:59:59.999999Z','available'),
    ('2026-10-26T16:00:00Z','contract_expired'),
])
@pytest.mark.parametrize('timezone', ['UTC','America/Los_Angeles','Asia/Shanghai'])
def test_catalog_availability_uses_shanghai_contract_day(admin_engine, at, status, timezone):
    from psycopg.types.json import Jsonb
    metadata = config(enabled=True, contract_expires_on='2026-10-26')
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT to_regprocedure('public.model_catalog_availability(jsonb,timestamp with time zone)') IS NOT NULL")).scalar_one(), 'Missing server-side catalog availability projection'
        db.execute(text("SELECT set_config('TimeZone',:zone,true)"), {'zone':timezone})
        assert db.execute(text('SELECT public.model_catalog_availability(:config,CAST(:at AS timestamptz))'),
                          {'config':Jsonb(metadata),'at':at}).scalar_one() == status
