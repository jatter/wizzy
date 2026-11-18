import json

import pytest

from wizzy.homework_results import output_contract, page_answers, parse_page_answers


def answer():
    return {'pages': [{'position': 1, 'subject': 'math', 'note': '', 'questions': [
        {'number': str(i), 'verdict': verdict, 'student_answer': '', 'reasoning': '核对题意',
         'steps': ['40+2=42'], 'answer': '42'}
        for i, verdict in enumerate(['correct', 'incorrect', 'unanswered', 'unclear'])]}]}


def test_counts_derive_from_separate_verdicts_and_never_count_unanswered_as_wrong():
    result = page_answers(json.dumps(answer()), 1)
    assert result[0]['counts'] == dict(correct=1, incorrect=1, unanswered=1, unclear=1)
    assert result[0]['questions'][0]['steps'] == ['40+2=42']
    assert page_answers('```json\n' + json.dumps(answer()) + '\n```', 1) == result


@pytest.mark.parametrize('change', [
    lambda body: body['pages'].append(body['pages'][0]),
    lambda body: body['pages'][0].update(position=2),
    lambda body: body['pages'][0].update(position=True),
    lambda body: body['pages'][0].update(subject='invented'),
    lambda body: body['pages'][0]['questions'][0].update(verdict='maybe'),
    lambda body: body['pages'][0]['questions'].append(body['pages'][0]['questions'][0]),
    lambda body: body['pages'][0]['questions'][0].update(steps='not a list'),
])
def test_invalid_or_ambiguous_image_mapping_is_not_projected(change):
    body = answer()
    change(body)
    assert page_answers(json.dumps(body), 1) == []


def test_legacy_incomplete_or_invalid_responses_keep_unknown_counts():
    assert page_answers('第1张第1题：正确', 1) == []
    assert page_answers(json.dumps(answer()), 2) == []
    assert page_answers('{bad json', 1) == []


def vocabulary(number, word, verdict='correct', basis='spelling_only', item_type='word', answer=''):
    return dict(number=number, item_type=item_type, grading_basis=basis, verdict=verdict,
                student_answer=word, reasoning='仅核对可见拼写，未提供听写目标', steps=[], answer=answer)


def test_unnumbered_columns_phrases_and_mixed_pages_keep_locations_and_original_spelling():
    body = answer()
    body['pages'].append(dict(position=2, subject='english', note='仅检查拼写', questions=[
        vocabulary('左列第1项', 'weather'),
        vocabulary('右列第1项', 'whether'),
        vocabulary('右列第2项', 'take care of', item_type='phrase'),
        vocabulary('左列第2项', 'applle', 'incorrect', answer='apple'),
        vocabulary('左列第3项', 'w?rd', 'unclear', 'none'),
    ]))
    pages = page_answers(json.dumps(body), 2)
    assert len(pages) == 2
    assert pages[0]['questions'][0]['item_type'] == 'exercise'  # old results remain valid
    assert pages[1]['counts'] == dict(correct=3, incorrect=1, unanswered=0, unclear=1)
    assert pages[1]['questions'][3]['student_answer'] == 'applle'
    assert pages[1]['questions'][2]['item_type'] == 'phrase'


def test_reference_comparison_supports_missing_extra_and_wrong_words():
    questions = [vocabulary('第1项', 'weather', 'incorrect', 'task_reference', answer='whether'),
                 vocabulary('第2项', '', 'unanswered', 'task_reference', answer='apple'),
                 vocabulary('额外第1项', 'banana', 'incorrect', 'task_reference')]
    pages = page_answers(json.dumps(dict(pages=[dict(position=1, subject='english', note='', questions=questions)])), 1)
    assert pages[0]['counts'] == dict(correct=0, incorrect=2, unanswered=1, unclear=0)


@pytest.mark.parametrize('change', [
    dict(grading_basis='none', verdict='correct'),
    dict(grading_basis='none', verdict='incorrect'),
    dict(item_type='exercise', grading_basis='spelling_only'),
    dict(item_type='unknown'),
])
def test_unsupported_or_unjustified_grading_is_rejected(change):
    body = answer()
    body['pages'][0]['questions'] = [vocabulary('左列第1项', 'weather') | change]
    assert page_answers(json.dumps(body), 1) == []


def test_new_vocabulary_items_must_state_their_grading_basis():
    body = answer()
    body['pages'][0]['questions'][0]['item_type'] = 'word'
    assert page_answers(json.dumps(body), 1) == []


def test_diagnostics_distinguish_format_coverage_schema_and_duplicate_locations():
    assert parse_page_answers('{bad', 1)[1] == 'invalid_json'
    assert parse_page_answers(json.dumps(answer()) + 'extra', 1)[1] == 'invalid_json'
    assert parse_page_answers(json.dumps(answer()), 2)[1] == 'page_coverage_mismatch'
    body = answer()
    body['pages'][0]['questions'][0]['verdict'] = 'maybe'
    assert parse_page_answers(json.dumps(body), 1)[1] == 'schema_validation_failed'
    body = answer()
    body['pages'][0]['questions'].append(body['pages'][0]['questions'][0])
    assert parse_page_answers(json.dumps(body), 1)[1] == 'duplicate_item_number'
    body['pages'][0]['questions'] = []
    pages, error = parse_page_answers(json.dumps(body), 1)
    assert error is None and pages[0]['counts']['incorrect'] == 0


def test_contract_covers_vocabulary_boundaries_without_a_new_classifier_call():
    contract = output_contract(2)
    for rule in ('左列第3项', 'word', 'phrase', 'spelling_only', 'task_reference', 'weather', 'whether', '漏写', '多写'):
        assert rule in contract
