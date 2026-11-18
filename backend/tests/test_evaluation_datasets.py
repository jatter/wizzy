"""Gold data must come from authorized source revisions and independent humans."""
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_evaluation_consent import grant
from test_execution import execution as execution  # noqa: F401
from test_platform_identity import PASSWORD, session
from test_platform_identity import platform as platform  # noqa: F401
from test_question_revisions import questions
from test_result_read import readable_result as readable_result  # noqa: F401
from test_results import result_context as result_context  # noqa: F401

from wizzy.security import passwords

BASE = '/api/v1/admin/evaluation-datasets'


def command(revision=0, **fields):
    return {'operation_id': str(uuid4()), 'expected_revision': revision, **fields}


def label(**overrides):
    return {'stem': '12 + 8 = ?', 'student_answer': '21', 'correct_answer': '20',
            'verdict': 'incorrect', 'question_type': 'arithmetic', 'difficulty': 'easy',
            'requires_abstention': False, 'redaction_safe': True,
            'box': [100, 100, 4000, 2000], **overrides}


def ok(response, status=201):
    assert response.status_code == status, response.text
    return response.json()['data']


@pytest.fixture
def evaluators(client, platform, admin_engine):
    accounts = [platform]
    for _ in range(2):
        identifier = uuid4()
        email = f'evaluator-{identifier}@example.test'
        with admin_engine.begin() as db:
            db.execute(text('''INSERT INTO platform_accounts
                (id,email,password_hash,encrypted_totp,credential_version,role)
                VALUES(:id,:email,:password,'unused',:version,'security_admin')'''),
                {'id': identifier, 'email': email, 'password': passwords.hash(PASSWORD),
                 'version': uuid4()})
        accounts.append({'id': identifier, 'email': email})
    return [session(client, a) for a in accounts]


@pytest.fixture
def source(client, owner, student, readable_result):
    consent = grant(client, owner, student)
    q = questions(client, owner, readable_result[3])[0]
    return {'question_id': q['question_id'], 'source_revision': q['revision'],
            'authorization_id': consent['state']['authorization_id'],
            'near_duplicate_group': 'arithmetic-12-plus-8', 'redacted_stem': q['stem'],
            'redacted_answer': q['student_answer_raw'], 'redaction_note': '已人工移除身份字段'}


def create(client, headers, **changes):
    return ok(client.post(BASE, headers=headers, json=command(
        name='三年级加法验收', split='test', handbook_version='math-v1', **changes)))


def intake(client, headers, dataset, source):
    return ok(client.post(f"{BASE}/{dataset['id']}/samples", headers=headers,
                          json=command(dataset['revision'], **source)))


def annotate(client, headers, dataset, sample, revision, **changes):
    return client.post(f"{BASE}/{dataset['id']}/samples/{sample}/annotations", headers=headers,
                       json=command(revision, label=label(**changes)))


def detail(client, headers, dataset):
    return ok(client.get(f"{BASE}/{dataset['id']}", headers=headers), 200)


def test_real_source_two_blind_votes_and_irreversible_lock(client, evaluators, source):
    first, second, third = evaluators
    dataset = create(client, first)
    view = intake(client, first, dataset, source)
    sample = view['samples'][0]['id']
    view = ok(annotate(client, first, dataset, sample, 2))
    assert view['revision'] == 3 and view['samples'][0]['own_label'] == label()
    blind = detail(client, second, dataset)['samples'][0]
    assert blind['own_label'] is None and blind['gold'] is None
    assert 'incorrect' not in str(blind) and '20' not in str(blind.get('labels'))
    view = ok(annotate(client, second, dataset, sample, 3))
    assert view['samples'][0]['state'] == 'agreed'
    locked = ok(client.post(f"{BASE}/{dataset['id']}/lock", headers=third, json=command(4)))
    assert locked['state'] == 'locked' and locked['samples'][0]['gold'] == label()
    assert locked['locked_digest'] and locked['revision'] == 5
    assert client.post(f"{BASE}/{dataset['id']}/samples", headers=first,
                       json=command(5, **source)).status_code == 409
    assert annotate(client, third, dataset, sample, 5).status_code == 409
    assert detail(client, first, dataset)['locked_digest'] == locked['locked_digest']


def test_disagreement_needs_independent_third_adjudicator(client, evaluators, source):
    first, second, third = evaluators
    dataset = create(client, first)
    sample = intake(client, first, dataset, source)['samples'][0]['id']
    ok(annotate(client, first, dataset, sample, 2))
    ok(annotate(client, second, dataset, sample, 3, correct_answer='19'))
    assert client.post(f"{BASE}/{dataset['id']}/lock", headers=third, json=command(4)).status_code == 409
    url = f"{BASE}/{dataset['id']}/samples/{sample}/adjudications"
    body = command(4, label=label(), reason='依据加法判例手册第一条核对')
    assert client.post(url, headers=first, json=body).status_code == 409
    assert detail(client, first, dataset)['samples'][0].get('disagreement_labels') is None
    votes = detail(client, third, dataset)['samples'][0]['disagreement_labels']
    assert len(votes) == 2
    ok(client.post(url, headers=third, json=body))
    locked = ok(client.post(f"{BASE}/{dataset['id']}/lock", headers=third, json=command(5)))
    assert locked['samples'][0]['state'] == 'adjudicated'
    assert locked['agreement']['agreed'] == 0 and locked['agreement']['double_labeled'] == 1


def test_retries_cas_and_duplicate_voter(client, evaluators, source):
    first, second, _ = evaluators
    body = command(name='可重试', split='test', handbook_version='v1')
    dataset = ok(client.post(BASE, headers=first, json=body))
    assert ok(client.post(BASE, headers=first, json=body), 200) == dataset
    assert client.post(BASE, headers=first, json={**body, 'name': '不同请求'}).status_code == 409
    view = intake(client, first, dataset, source)
    sample = view['samples'][0]['id']
    payload = command(2, label=label())
    url = f"{BASE}/{dataset['id']}/samples/{sample}/annotations"
    ok(client.post(url, headers=first, json=payload))
    assert ok(client.post(url, headers=first, json=payload), 200)['revision'] == 3
    assert annotate(client, first, dataset, sample, 3).status_code == 409
    assert annotate(client, second, dataset, sample, 2).status_code == 409


def test_withdrawal_blocks_read_write_and_retry_without_resurrection(client, evaluators, source, owner, student):
    first, second, third = evaluators
    dataset = create(client, first)
    sample = intake(client, first, dataset, source)['samples'][0]['id']
    ok(annotate(client, first, dataset, sample, 2))
    ok(annotate(client, second, dataset, sample, 3))
    payload = command(4)
    ok(client.post(f"{BASE}/{dataset['id']}/lock", headers=third, json=payload))
    grant(client, owner, student, granted=False, revision=1)
    read = detail(client, third, dataset)
    assert read['usable'] is False
    assert read['samples'][0]['state'] == 'authorization_inactive'
    assert read['samples'][0]['redacted_stem'] is None and read['samples'][0]['gold'] is None
    assert client.post(f"{BASE}/{dataset['id']}/lock", headers=third, json=payload).status_code == 409
    grant(client, owner, student, revision=2)
    assert detail(client, third, dataset)['usable'] is False


def test_source_mismatch_and_global_student_split_guard(client, evaluators, source):
    first = evaluators[0]
    dataset = create(client, first)
    assert client.post(f"{BASE}/{dataset['id']}/samples", headers=first,
                       json=command(1, **{**source, 'source_revision': 999})).status_code == 404
    intake(client, first, dataset, source)
    train = ok(client.post(BASE, headers=first, json=command(
        name='训练集合', split='train', handbook_version='v1')))
    assert client.post(f"{BASE}/{train['id']}/samples", headers=first,
                       json=command(1, **source)).status_code == 409


@pytest.mark.parametrize('changes', [{'verdict': 'unsupported', 'requires_abstention': False},
                                    {'box': [10, 10, 1, 1]}, {'question_type': ''},
                                    {'redaction_safe': 'true'}, {'unknown': 'value'}])
def test_invalid_labels_rejected(client, evaluators, source, changes):
    first = evaluators[0]
    dataset = create(client, first)
    sample = intake(client, first, dataset, source)['samples'][0]['id']
    assert annotate(client, first, dataset, sample, 2, **changes).status_code == 422
