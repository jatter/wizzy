"""Fixed offline manifests and whole-corpus, server-computed model quality."""
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_evaluation_consent import grant
from test_evaluation_datasets import (
    annotate,
    command,
    create,
    intake,
    ok,
)
from test_evaluation_datasets import evaluators as evaluators  # noqa: F401
from test_evaluation_datasets import source as source  # noqa: F401
from test_execution import execution as execution  # noqa: F401
from test_model_catalog import create as create_model
from test_platform_identity import platform as platform  # noqa: F401
from test_result_read import readable_result as readable_result  # noqa: F401
from test_results import result_context as result_context  # noqa: F401

from wizzy.security import digest

BASE = '/api/v1/admin/evaluation-runs'


@pytest.fixture
def corpus(client, evaluators, source):
    first, second, third = evaluators
    dataset = create(client, first)
    sample = intake(client, first, dataset, source)['samples'][0]['id']
    ok(annotate(client, first, dataset, sample, 2))
    ok(annotate(client, second, dataset, sample, 3))
    locked = ok(client.post(f"/api/v1/admin/evaluation-datasets/{dataset['id']}/lock",
                            headers=third, json=command(4)))
    model = ok(create_model(client, first))
    return locked, model, sample


def manifest(corpus, **changes):
    dataset, model, _ = corpus
    return command(dataset_id=dataset['id'], dataset_revision=dataset['revision'],
                   model_id=model['id'], model_revision=model['revision'],
                   prompt_hash='a' * 64, schema_version='grading-v1', rule_version='math-v1',
                   image_pipeline_version='image-v1',
                   required_strata=[{'grade': 3, 'question_type': 'arithmetic'}], **changes)


def run(client, headers, corpus):
    return ok(client.post(BASE, headers=headers, json=manifest(corpus)))


def prediction(corpus, **changes):
    return {'question_id': corpus[2], 'verdict': 'incorrect', 'automatic_release': True, **changes}


def import_body(item, corpus, **changes):
    return command(1, manifest_digest=item['manifest_digest'], source_reference='offline-run-001',
                   predictions=[prediction(corpus)], **changes)


def test_fixed_manifest_complete_predictions_and_computed_report(client, evaluators, corpus, admin_engine):
    headers = evaluators[0]
    payload = manifest(corpus)
    item = ok(client.post(BASE, headers=headers, json=payload))
    assert item['revision'] == 1 and item['state'] == 'awaiting_predictions'
    assert item['model']['fixed_version'] == '2026-10-01'
    assert item['dataset']['locked_digest'] == corpus[0]['locked_digest']
    assert item['production_eligible'] is False and item['usable'] is True
    assert 'secret_ref' not in str(item) and 'secret://' not in str(item)
    assert ok(client.post(BASE, headers=headers, json=payload), 200) == item
    imported = import_body(item, corpus)
    completed = ok(client.post(f"{BASE}/{item['id']}/predictions", headers=headers, json=imported))
    assert completed['revision'] == 2 and completed['state'] == 'completed'
    assert completed['prediction_count'] == 1
    assert completed['source_reference'] == 'offline-run-001'
    assert ok(client.post(f"{BASE}/{item['id']}/predictions", headers=headers, json=imported), 200) == completed
    report = ok(client.get(f"{BASE}/{item['id']}/report", headers=headers), 200)
    assert report['run_id'] == item['id'] and report['manifest_digest'] == item['manifest_digest']
    assert report['report']['metrics']['coverage']['estimate'] == 1
    assert report['report']['metrics']['selective_accuracy']['estimate'] == 1
    assert report['report']['sample_targets_met'] is False
    assert report['report']['production_eligible'] is False
    assert report['report']['method']['algorithm_version'] == 'grading-statistics-v1'
    assert 'student_key' not in str(report) and '12 + 8' not in str(report)
    listed = ok(client.get(BASE, headers=headers), 200)
    assert listed['items'][0]['id'] == item['id']
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE action LIKE 'evaluation.runs.%' AND actor_id=(SELECT account_id FROM platform_sessions WHERE token_hash=:hash)"), {'hash': digest(headers['Authorization'][7:])}).scalar_one() == 2


@pytest.mark.parametrize('kind', ['missing', 'duplicate', 'extra', 'wrong_digest', 'abstention', 'score'])
def test_invalid_prediction_import_is_atomic(client, evaluators, corpus, admin_engine, kind):
    headers = evaluators[0]
    item = run(client, headers, corpus)
    body = import_body(item, corpus)
    if kind == 'missing':
        body['predictions'] = []
    elif kind == 'duplicate':
        body['predictions'] *= 2
    elif kind == 'extra':
        body['predictions'].append(prediction(corpus, question_id=str(uuid4())))
    elif kind == 'wrong_digest':
        body['manifest_digest'] = 'b' * 64
    elif kind == 'abstention':
        body['predictions'][0]['verdict'] = 'pending_review'
    else:
        body['accuracy'] = 1.0
    response = client.post(f"{BASE}/{item['id']}/predictions", headers=headers, json=body)
    assert response.status_code == (409 if kind in ('missing', 'duplicate', 'extra', 'wrong_digest') else 422), response.text
    current = ok(client.get(f"{BASE}/{item['id']}", headers=headers), 200)
    assert current['revision'] == 1 and current['prediction_count'] == 0
    assert client.get(f"{BASE}/{item['id']}/report", headers=headers).status_code == 409
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM evaluation_run_predictions WHERE run_id=:id'), {'id': item['id']}).scalar_one() == 0


def test_revoke_blocks_report_import_and_retry_without_resurrection(client, evaluators, corpus, owner, student):
    headers = evaluators[0]
    item = run(client, headers, corpus)
    payload = import_body(item, corpus)
    ok(client.post(f"{BASE}/{item['id']}/predictions", headers=headers, json=payload))
    grant(client, owner, student, granted=False, revision=1)
    detail = ok(client.get(f"{BASE}/{item['id']}", headers=headers), 200)
    assert detail['usable'] is False
    for path in (f"{BASE}/{item['id']}/report",):
        assert client.get(path, headers=headers).status_code == 409
    assert client.post(f"{BASE}/{item['id']}/predictions", headers=headers, json=payload).status_code == 409
    grant(client, owner, student, revision=2)
    assert client.get(f"{BASE}/{item['id']}/report", headers=headers).status_code == 409


def test_real_permission_and_session_boundaries(client, evaluators, corpus, owner, admin_engine):
    headers = evaluators[0]
    item = run(client, headers, corpus)
    assert client.get(BASE).status_code == 401
    assert client.get(BASE, headers=owner['headers']).status_code == 401
    try:
        with admin_engine.begin() as db:
            db.execute(text("DELETE FROM platform_role_permissions WHERE role='security_admin' AND permission='platform.evaluation.run'"))
        assert client.post(BASE, headers=headers, json=manifest(corpus)).status_code == 403
        assert client.post(f"{BASE}/{item['id']}/predictions", headers=headers,
                           json=import_body(item, corpus)).status_code == 403
        assert client.get(BASE, headers=headers).status_code == 200
        with admin_engine.begin() as db:
            db.execute(text("DELETE FROM platform_role_permissions WHERE role='security_admin' AND permission='platform.evaluation.read'"))
        assert client.get(f"{BASE}/{item['id']}", headers=headers).status_code == 403
    finally:
        with admin_engine.begin() as db:
            db.execute(text("""INSERT INTO platform_role_permissions(role,permission) VALUES
                ('security_admin','platform.evaluation.run'),('security_admin','platform.evaluation.read')
                ON CONFLICT DO NOTHING"""))


def test_manifest_requires_locked_test_or_validation_and_fixed_existing_revision(client, evaluators, corpus):
    headers = evaluators[0]
    body = manifest(corpus)
    for key, value in [('model_revision', 999), ('dataset_revision', 4)]:
        assert client.post(BASE, headers=headers, json={**body, 'operation_id': str(uuid4()), key: value}).status_code == 409
    draft = create(client, headers)
    assert client.post(BASE, headers=headers, json={**body, 'dataset_id': draft['id'], 'dataset_revision': 1}).status_code == 409
    assert client.post(BASE, headers=headers, json={**body, 'required_strata': []}).status_code == 422


def test_cas_and_changed_idempotency_payload(client, evaluators, corpus):
    headers = evaluators[0]
    body = manifest(corpus)
    item = ok(client.post(BASE, headers=headers, json=body))
    assert client.post(BASE, headers=headers, json={**body, 'rule_version': 'v2'}).status_code == 409
    imported = import_body(item, corpus)
    url = f"{BASE}/{item['id']}/predictions"
    assert client.post(url, headers=headers, json={**imported, 'expected_revision': 0}).status_code == 409
    ok(client.post(url, headers=headers, json=imported))
    assert client.post(url, headers=headers, json={**imported, 'source_reference': 'changed'}).status_code == 409
    assert client.post(url, headers=headers, json=import_body(item, corpus)).status_code == 409
