"""Real PostgreSQL publication boundaries with an actual extracted PDF source."""
import copy
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_textbook_documents import approved as approved
from test_textbook_documents import private_storage as private_storage
from test_textbook_processing import received as received
from test_textbook_processing import submit
from test_textbook_processing import worker as worker
from test_textbook_rights import BASE, action
from test_textbook_rights import platform as platform
from test_textbook_rights import reviewer as reviewer


def payload(item, **changes):
    return {'operation_id': str(uuid4()), 'expected_revision': item['revision'], 'reason': '校核教材正文', **changes}


@pytest.fixture
def source(client, received, worker):
    headers, edition, _, _ = received
    job, _ = submit(client, received)
    assert worker.consume_one() == 'finished'
    path = f"{BASE}/{edition['id']}/processing-jobs/{job['id']}/content"
    response = client.post(path, headers=headers, json=payload(edition))
    assert response.status_code == 201, response.text
    item = response.json()['data']
    return headers, edition, item, f"{BASE}/{edition['id']}/contents"


def body(client, headers, path, item):
    response = client.get(f"{path}/{item['id']}/revisions/{item['revision']}/body", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()['data']['body']


def change(client, headers, path, item, operation, expected=201, **changes):
    response = client.post(f"{path}/{item['id']}/{operation}", headers=headers, json=payload(item, **changes))
    assert response.status_code == expected, response.text
    return response.json()['data' if expected in (200, 201) else 'error']


def ready(client, source):
    headers, _, item, path = source
    value = body(client, headers, path, item)
    for p in value['pages']:
        p['verified'] = True
    value['pages'][1]['text'] = '人工核对：原 PDF 本页为空白'
    value['chapters'] = [dict(code='CH1', title='第一章', parent_code=None, first_page=1, last_page=3),
                         dict(code='CH1_1', title='第一节', parent_code='CH1', first_page=1, last_page=2)]
    return change(client, headers, path, item, 'revisions', body=value)


def test_correction_publication_history_txt_and_withdrawal(client, source, reviewer, admin_engine):
    headers, edition, item, path = source
    assert item['verified_pages'] == 0 and item['chapter_count'] == 0 and not item['ready']
    assert not item['content_available'] and not item['ocr_performed']
    assert 'body' not in item and 'First source page' not in str(item)
    assert change(client, headers, path, item, 'submit', 409)['code'] == 'content_review_incomplete'
    item = ready(client, source)
    assert item['ready'] and item['verified_pages'] == 3
    item = change(client, headers, path, item, 'submit')
    assert item['state'] == 'pending_review' and item['required_sample_pages'] == [1, 2, 3]
    assert change(client, headers, path, item, 'approve', 409, sample_pages=[1, 2, 3])['code'] == 'independent_reviewer_required'
    assert change(client, reviewer['headers'], path, item, 'approve', 409, sample_pages=[1, 3])['code'] == 'content_sample_incomplete'
    item = change(client, reviewer['headers'], path, item, 'approve', sample_pages=[1, 2, 3])
    assert item['content_available'] and item['state'] == 'published'
    txt = client.get(f"{path}/{item['id']}/txt", headers=headers).json()['data']
    assert '[PDF page 1]\nFirst source page' in txt['text'] and txt['chapters'][1]['parent_code'] == 'CH1'
    assert change(client, headers, path, item, 'revisions', 409, body=body(client, headers, path, item))['code'] == 'content_state_conflict'
    item = change(client, headers, path, item, 'withdraw')
    assert not item['content_available'] and item['state'] == 'withdrawn'
    assert client.get(f"{path}/{item['id']}/txt", headers=headers).json()['error']['code'] == 'content_not_published'
    history = client.get(f"{path}/{item['id']}/history", headers=headers, params={'limit': 2}).json()['data']
    assert [x['action'] for x in history['items']] == ['withdraw', 'approve']
    older = client.get(f"{path}/{item['id']}/history", headers=headers,
                       params={'before': history['next_before']}).json()['data']
    assert [x['action'] for x in older['items']] == ['submit', 'revise', 'create']
    assert older['next_before'] is None
    assert all(not x['content_available'] for x in history['items'])
    original = client.get(f"{path}/{item['id']}/revisions/1/pages/2", headers=headers).json()['data']
    assert original['page'] == {'number': 2, 'text': '', 'verified': False}
    assert client.get(f"{path}/{item['id']}/revisions/1/pages/4", headers=headers).status_code == 404
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE action LIKE 'textbooks.content.%'")).scalar_one() == 5
    assert client.get(f"{BASE}/{edition['id']}", headers=headers).json()['data']['content_available'] is False  # AI integration remains pending.


def test_idempotency_cas_rejection_and_rights_revocation(client, source, reviewer):
    headers, edition, first, path = source
    value = body(client, headers, path, first)
    edit = payload(first, body=value)
    url = f"{path}/{first['id']}/revisions"
    saved = client.post(url, headers=headers, json=edit)
    assert saved.status_code == 201
    assert client.post(url, headers=headers, json=edit).json()['data'] == saved.json()['data']
    assert client.post(url, headers=headers, json={**edit, 'reason': '另一个请求'}).json()['error']['code'] == 'operation_conflict'
    assert client.post(url, headers=headers, json=payload(first, body=value)).json()['error']['code'] == 'content_revision_conflict'
    second = saved.json()['data']
    ready_item = ready(client, (headers, edition, second, path))
    pending = change(client, headers, path, ready_item, 'submit')
    rejected = change(client, reviewer['headers'], path, pending, 'reject')
    assert rejected['state'] == 'draft' and rejected['submitted_by'] is None
    pending = change(client, headers, path, rejected, 'submit')
    published = change(client, reviewer['headers'], path, pending, 'approve', sample_pages=[1, 2, 3])
    action(client, headers, edition, 'withdraw')
    assert client.get(f"{path}/{first['id']}", headers=headers).json()['data']['content_available'] is False
    for tail in ('txt', f"revisions/{published['revision']}/body", f"revisions/{published['revision']}/pages/1"):
        assert client.get(f"{path}/{first['id']}/{tail}", headers=headers).json()['error']['code'] == 'content_rights_unavailable'
    assert change(client, headers, path, published, 'withdraw')['state'] == 'withdrawn'


@pytest.mark.parametrize('mutation', [
    lambda b: b['pages'][0].update(number=True),
    lambda b: b['pages'][0].update(number=2),
    lambda b: b['pages'][0].update(verified='true'),
    lambda b: b['pages'][0].update(text='\0'),
    lambda b: b['pages'][0].update(text='x'*100001),
    lambda b: b['chapters'].append(dict(code='bad', title='章', parent_code=None, first_page=1, last_page=3)),
    lambda b: b['chapters'].append(dict(code='CH', title=' ', parent_code=None, first_page=1, last_page=3)),
    lambda b: b['chapters'].append(dict(code='CH', title='章', parent_code='CH', first_page=1, last_page=3)),
    lambda b: b['chapters'].append(dict(code='CH', title='章', parent_code=None, first_page=3, last_page=2)),
    lambda b: b['chapters'].append(dict(code='CH', title='章', parent_code=None, first_page=1, last_page=4)),
])
def test_invalid_correction_does_not_write(client, source, mutation):
    headers, _, item, path = source
    value = body(client, headers, path, item)
    mutation(value)
    change(client, headers, path, item, 'revisions', 422, body=value)
    assert client.get(f"{path}/{item['id']}", headers=headers).json()['data']['revision'] == 1


def test_concurrent_same_revision_only_one_wins(client, source, app):
    headers, _, item, path = source
    value = body(client, headers, path, item)
    def write(_):
        with TestClient(app) as other:
            return other.post(f"{path}/{item['id']}/revisions", headers=headers, json=payload(item, body=value)).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, range(2))) == [201, 409]


def test_capability_acl_immutable_history_and_audit_rollback(client, source, admin_engine, database):
    headers, _, item, path = source
    capability = 'public.textbook_content_request(text,text,text,jsonb,uuid,uuid,integer,integer,integer,integer,timestamptz,uuid)'
    with admin_engine.connect() as db:
        for table in ('textbook_contents', 'textbook_content_revisions'):
            assert db.execute(text('SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid=to_regclass(:t)'), {'t': table}).scalar_one()
            for key in ('WIZZY_APP_ROLE', 'WIZZY_WORKER_ROLE', 'WIZZY_DISPATCHER_ROLE'):
                assert not db.execute(text("SELECT has_table_privilege(:r,:t,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')"), {'r': database['env'][key], 't': table}).scalar_one()
        assert db.execute(text("SELECT has_function_privilege(:r,:f,'EXECUTE')"), {'r': database['env']['WIZZY_APP_ROLE'], 'f': capability}).scalar_one()
        for key in ('WIZZY_WORKER_ROLE', 'WIZZY_DISPATCHER_ROLE'):
            assert not db.execute(text("SELECT has_function_privilege(:r,:f,'EXECUTE')"), {'r': database['env'][key], 'f': capability}).scalar_one()
        for name in ('textbook_content_request', 'textbook_content_allowed', 'textbook_content_view', 'textbook_content_valid', 'textbook_content_ready'):
            row = db.execute(text('SELECT proconfig,proacl FROM pg_proc WHERE proname=:n'), {'n': name}).one()
            assert row.proconfig == ['search_path=pg_catalog, pg_temp']
            assert not db.execute(text("SELECT EXISTS(SELECT 1 FROM pg_proc p,LATERAL aclexplode(p.proacl) a WHERE p.proname=:n AND a.grantee=0 AND a.privilege_type='EXECUTE')"), {'n': name}).scalar_one()
    for sql in ('UPDATE textbook_content_revisions SET reason=reason', 'DELETE FROM textbook_content_revisions', 'TRUNCATE textbook_content_revisions CASCADE'):
        with pytest.raises(DBAPIError), admin_engine.begin() as db:
            db.execute(text(sql))
    value = copy.deepcopy(body(client, headers, path, item))
    with admin_engine.begin() as db:
        db.execute(text("CREATE FUNCTION public.fail_content_audit() RETURNS trigger LANGUAGE plpgsql AS $$BEGIN IF NEW.action LIKE 'textbooks.content.%' THEN RAISE EXCEPTION 'audit unavailable'; END IF; RETURN NEW; END$$"))
        db.execute(text('CREATE TRIGGER fail_content_audit BEFORE INSERT ON platform_audit_events FOR EACH ROW EXECUTE FUNCTION public.fail_content_audit()'))
    try:
        with pytest.raises(DBAPIError):
            client.post(f"{path}/{item['id']}/revisions", headers=headers, json=payload(item, body=value))
    finally:
        with admin_engine.begin() as db:
            db.execute(text('DROP TRIGGER fail_content_audit ON platform_audit_events'))
            db.execute(text('DROP FUNCTION public.fail_content_audit()'))
    assert client.get(f"{path}/{item['id']}", headers=headers).json()['data']['revision'] == 1
