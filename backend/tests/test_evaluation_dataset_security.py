"""Private corpus capabilities, independent roles and transaction boundaries."""
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from test_evaluation_datasets import BASE, annotate, command, create, detail, intake, label, ok
from test_evaluation_datasets import evaluators as evaluators  # noqa: F401
from test_evaluation_datasets import execution as execution  # noqa: F401
from test_evaluation_datasets import platform as platform  # noqa: F401
from test_evaluation_datasets import readable_result as readable_result  # noqa: F401
from test_evaluation_datasets import result_context as result_context  # noqa: F401
from test_evaluation_datasets import source as source  # noqa: F401

from wizzy.security import digest


def role(admin_engine, headers, value):
    with admin_engine.begin() as db:
        db.execute(text('''UPDATE platform_accounts SET role=:role WHERE id=(
            SELECT account_id FROM platform_sessions WHERE token_hash=:hash)'''),
            {'role': value, 'hash': digest(headers['Authorization'].split()[1])})


def sql(db, headers, operation, payload, dataset=None, sample=None):
    return db.execute(text('''SELECT public.evaluation_dataset_request(:hash,:operation,'dataset-security',
        CAST(:payload AS jsonb),:dataset,:sample,50,0)'''), {
        'hash': digest(headers['Authorization'].split()[1]), 'operation': operation,
        'payload': json.dumps(payload), 'dataset': dataset, 'sample': sample}).scalar_one()


def test_platform_session_required_and_operator_denied(client, evaluators, owner, admin_engine):
    assert client.get(BASE).status_code == 401
    assert client.get(BASE, headers=owner['headers']).status_code == 401
    role(admin_engine, evaluators[0], 'operator')
    assert client.get(BASE, headers=evaluators[0]).status_code == 403
    with admin_engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE action='access.denied'")).scalar_one() == 1


def test_independent_roles_and_live_permission_revocation(client, evaluators, source, admin_engine):
    curator, annotator, judge = evaluators
    dataset = create(client, curator)
    sample = intake(client, curator, dataset, source)['samples'][0]['id']
    role(admin_engine, annotator, 'evaluation_annotator')
    role(admin_engine, judge, 'evaluation_adjudicator')
    assert client.post(BASE, headers=annotator, json=command(
        name='越权集合', split='test', handbook_version='v1')).status_code == 403
    assert annotate(client, judge, dataset, sample, 2).status_code == 403
    ok(annotate(client, annotator, dataset, sample, 2))
    ok(annotate(client, curator, dataset, sample, 3, correct_answer='19'))
    assert detail(client, annotator, dataset)['samples'][0]['disagreement_labels'] is None
    votes = detail(client, judge, dataset)['samples'][0]['disagreement_labels']
    assert {v['correct_answer'] for v in votes} == {'19', '20'}
    url = f"{BASE}/{dataset['id']}/samples/{sample}/adjudications"
    body = command(4, label=label(), reason='独立复核')
    assert client.post(url, headers=annotator, json=body).status_code == 403
    ok(client.post(url, headers=judge, json=body))
    with admin_engine.begin() as db:
        db.execute(text("DELETE FROM platform_role_permissions WHERE role='evaluation_annotator' AND permission='platform.evaluation.read'"))
    assert client.get(f"{BASE}/{dataset['id']}", headers=annotator).status_code == 403


@pytest.mark.parametrize('votes', [0, 1, 2])
def test_lock_requires_two_redaction_safe_votes(client, evaluators, source, votes):
    first, second, third = evaluators
    dataset = create(client, first)
    sample = intake(client, first, dataset, source)['samples'][0]['id']
    if votes:
        ok(annotate(client, first, dataset, sample, 2, redaction_safe=False))
    if votes == 2:
        ok(annotate(client, second, dataset, sample, 3, redaction_safe=False))
    assert client.post(f"{BASE}/{dataset['id']}/lock", headers=third, json=command(2+votes)).status_code == 409
    assert detail(client, third, dataset)['state'] == 'draft'


def test_third_vote_rejected_and_abstention_survives_lock(client, evaluators, source):
    first, second, third = evaluators
    dataset = create(client, first)
    sample = intake(client, first, dataset, source)['samples'][0]['id']
    changes = {'verdict': 'unsupported', 'requires_abstention': True, 'correct_answer': ''}
    ok(annotate(client, first, dataset, sample, 2, **changes))
    ok(annotate(client, second, dataset, sample, 3, **changes))
    assert annotate(client, third, dataset, sample, 4, **changes).status_code == 409
    locked = ok(client.post(f"{BASE}/{dataset['id']}/lock", headers=third, json=command(4)))
    assert locked['samples'][0]['gold'] == label(**changes)
    assert locked['production_eligible'] is False


@pytest.mark.parametrize('changes', [
    {'box': {}}, {'box': None}, {'box': 'invalid'}, {'box': [0, 1, 2]},
    {'box': [0, 0, True, 100]}, {'box': [0, 0, 1.5, 100]}, {'box': [0, 0, 10001, 100]},
    {'stem': ' '}, {'redaction_safe': 'true'}, {'requires_abstention': None},
    {'correct_answer': ''}, {'verdict': 'unsupported'}, {'extra': 'forged'},
])
def test_sql_strict_label_validation_without_http(client, evaluators, source, changes):
    first = evaluators[0]
    dataset = create(client, first)
    sample = intake(client, first, dataset, source)['samples'][0]['id']
    with client.app.state.engine.begin() as db:
        result = sql(db, first, 'annotate', command(2, label=label(**changes)), dataset['id'], sample)
    assert result['status'] == 422
    assert detail(client, first, dataset)['revision'] == 2


def test_audit_failure_rolls_back_corpus_revision_and_vote(app, client, evaluators, source, admin_engine):
    first = evaluators[0]
    dataset = create(client, first)
    sample = intake(client, first, dataset, source)['samples'][0]['id']
    payload = command(2, label=label())
    with admin_engine.begin() as db:
        db.execute(text('''CREATE FUNCTION fail_dataset_audit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.action='evaluation.datasets.annotate' THEN RAISE EXCEPTION 'test audit failure';
            END IF; RETURN NEW; END $$;
            CREATE TRIGGER fail_dataset_audit BEFORE INSERT ON platform_audit_events
            FOR EACH ROW EXECUTE FUNCTION fail_dataset_audit();'''))
    try:
        with TestClient(app, raise_server_exceptions=False) as failing:
            assert failing.post(f"{BASE}/{dataset['id']}/samples/{sample}/annotations",
                                headers=first, json=payload).status_code == 500
        view = detail(client, first, dataset)
        assert view['revision'] == 2 and view['samples'][0]['own_label'] is None
        with admin_engine.connect() as db:
            assert not db.execute(text('SELECT EXISTS(SELECT 1 FROM evaluation_dataset_operations WHERE operation_id=:id)'),
                                  {'id': payload['operation_id']}).scalar_one()
    finally:
        with admin_engine.begin() as db:
            db.execute(text('DROP TRIGGER fail_dataset_audit ON platform_audit_events; DROP FUNCTION fail_dataset_audit()'))
    ok(client.post(f"{BASE}/{dataset['id']}/samples/{sample}/annotations", headers=first, json=payload))


def test_concurrent_same_operation_has_one_vote(app, client, evaluators, source, admin_engine):
    first = evaluators[0]
    dataset = create(client, first)
    sample = intake(client, first, dataset, source)['samples'][0]['id']
    payload = command(2, label=label())

    def vote(_):
        with TestClient(app, client=(str(uuid4()), 50000)) as caller:
            return caller.post(f"{BASE}/{dataset['id']}/samples/{sample}/annotations", headers=first, json=payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(vote, range(2)))
    assert sorted(r.status_code for r in responses) == [200, 201]
    assert detail(client, first, dataset)['revision'] == 3
    with admin_engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM evaluation_annotations WHERE sample_id=:sample'),
                          {'sample': sample}).scalar_one() == 1


def test_cross_split_concurrent_intake_has_one_winner(app, client, evaluators, source):
    first, second, _ = evaluators
    datasets = [create(client, first), ok(client.post(BASE, headers=second, json=command(
        name='验证集合', split='validation', handbook_version='v1')))]

    def import_sample(pair):
        headers, dataset = pair
        with TestClient(app, client=(str(uuid4()), 50000)) as caller:
            return caller.post(f"{BASE}/{dataset['id']}/samples", headers=headers, json=command(1, **source))

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(import_sample, zip([first, second], datasets, strict=True)))
    assert sorted(r.status_code for r in responses) == [201, 409]
    assert sum(detail(client, first, d)['sample_count'] for d in datasets) == 1
