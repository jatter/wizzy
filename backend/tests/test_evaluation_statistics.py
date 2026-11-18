"""Statistical contracts from docs/04, independent of suppliers and private child content."""
import json
import math

import pytest
from pydantic import ValidationError

from wizzy.evaluation_statistics import (
    EvaluationGold,
    EvaluationPrediction,
    EvaluationStratum,
    build_evaluation_report,
)


def gold(index, **overrides):
    return EvaluationGold.model_validate({
        'question_id': f'q{index}', 'student_key': f's{index}', 'page_key': f'p{index}',
        'grade': 3, 'question_type': 'integer_arithmetic', 'difficulty': 'medium',
        'verdict': 'incorrect', **overrides,
    })


def prediction(index, **overrides):
    return EvaluationPrediction.model_validate({
        'question_id': f'q{index}', 'verdict': 'incorrect', 'automatic_release': True,
        **overrides,
    })


def report(golds, predictions, **overrides):
    return build_evaluation_report(golds, predictions, required_strata=[
        EvaluationStratum(grade=3, question_type='integer_arithmetic'),
    ], **overrides)


def test_abstentions_and_unsupported_remain_in_denominator():
    golds = [gold(i, verdict=v) for i, v in enumerate([
        'incorrect', 'incorrect', 'correct', 'unanswered', 'pending_review', 'unsupported',
    ])]
    predictions = [prediction(i, verdict=v, automatic_release=automatic)
                   for i, (v, automatic) in enumerate([
                       ('incorrect', True), ('correct', True), ('incorrect', True),
                       ('unanswered', True), ('pending_review', False), ('unsupported', False),
                   ])]
    result = report(golds, predictions)
    assert result['total_questions'] == 6
    assert result['automatic_questions'] == 4
    assert result['conflicts'] == 2
    metrics = result['metrics']
    assert metrics['coverage']['estimate'] == pytest.approx(4 / 6)
    assert metrics['selective_accuracy']['estimate'] == .5
    assert metrics['false_correct_rate']['numerator'] == 1
    assert metrics['false_correct_rate']['denominator'] == 2
    assert metrics['false_incorrect_rate']['estimate'] == 1
    assert metrics['incorrect_precision']['estimate'] == .5
    assert metrics['incorrect_recall']['estimate'] == .5
    assert result['prediction_counts']['pending_review'] == 1
    assert result['prediction_counts']['unsupported'] == 1
    assert result['production_eligible'] is False
    json.dumps(result, allow_nan=False)


def test_definitive_verdict_awaiting_human_review_is_not_automatic():
    result = report([gold(1)], [prediction(1, automatic_release=False)])
    assert result['automatic_questions'] == 0
    assert result['metrics']['coverage']['estimate'] == 0
    assert result['metrics']['selective_accuracy']['estimate'] is None
    assert result['metrics']['incorrect_precision']['estimate'] is None
    assert result['metrics']['incorrect_recall']['estimate'] == 0
    assert result['numeric_thresholds_met'] is False


def test_false_correct_denominator_includes_all_gold_errors_not_just_released():
    result = report([gold(i) for i in range(10)], [
        prediction(i, verdict='correct' if i == 0 else 'pending_review',
                   automatic_release=i == 0) for i in range(10)
    ])
    assert result['metrics']['false_correct_rate']['estimate'] == .1
    assert result['metrics']['incorrect_recall']['estimate'] == 0
    assert result['metrics']['selective_accuracy']['estimate'] == 0


@pytest.mark.parametrize(('count', 'passes'), [(1, False), (298, False), (299, True)])
def test_zero_errors_uses_one_sided_not_two_sided_clopper_pearson(count, passes):
    result = report([gold(i) for i in range(count)], [prediction(i) for i in range(count)])
    accuracy = result['metrics']['selective_accuracy']
    false_correct = result['metrics']['false_correct_rate']
    assert accuracy['one_sided_95']['lower'] == pytest.approx(.05 ** (1 / count))
    assert false_correct['one_sided_95']['upper'] == pytest.approx(1 - .05 ** (1 / count))
    assert (accuracy['one_sided_95']['lower'] >= .99) is passes
    assert result['production_eligible'] is False
    # Statistical point estimates do not prove the planned 10k/1k/100 sample targets.
    assert result['sample_targets_met'] is False


def test_nonzero_errors_have_exact_one_sided_bounds():
    # These are independent beta-quantile reference values, not rounded gate percentages.
    result = report([gold(i) for i in range(1000)], [
        prediction(i, verdict='correct' if i == 0 else 'incorrect') for i in range(1000)
    ])
    assert result['metrics']['selective_accuracy']['one_sided_95']['lower'] == pytest.approx(
        0.9952650064245002, abs=1e-10)
    assert result['metrics']['false_correct_rate']['one_sided_95']['upper'] == pytest.approx(
        0.0047349935754998, abs=1e-10)


def test_no_wrong_gold_means_unknown_false_correct_risk_not_perfect_score():
    result = report([gold(i, verdict='correct') for i in range(300)], [
        prediction(i, verdict='correct') for i in range(300)
    ])
    metric = result['metrics']['false_correct_rate']
    assert metric['estimate'] is None
    assert metric['one_sided_95'] == {'lower': None, 'upper': None}
    assert metric['conservative_one_sided_95'] == {'lower': None, 'upper': None}
    assert result['numeric_thresholds_met'] is False


def test_clustered_errors_widen_bounds_instead_of_counting_correlated_questions_as_iid():
    # All five errors are on the same student's page; 5,000 questions are not 5,000 students.
    golds = [gold(i, student_key=f's{i // 500}', page_key=f'p{i // 500}') for i in range(5000)]
    predictions = [prediction(i, verdict='correct' if i < 5 else 'incorrect') for i in range(5000)]
    metric = report(golds, predictions)['metrics']['false_correct_rate']
    assert metric['clusters']['student']['cluster_count'] == 10
    assert metric['clusters']['page']['cluster_count'] == 10
    assert metric['clusters']['student']['upper'] > metric['one_sided_95']['upper']
    assert metric['conservative_one_sided_95']['upper'] == max(
        metric['one_sided_95']['upper'], metric['clusters']['student']['upper'],
        metric['clusters']['page']['upper'])


def test_single_cluster_cannot_create_fake_confidence():
    result = report([gold(i, student_key='one', page_key='one') for i in range(400)], [
        prediction(i) for i in range(400)
    ])
    metric = result['metrics']['selective_accuracy']
    assert metric['one_sided_95']['lower'] > .99
    assert metric['clusters']['student']['status'] == 'insufficient_clusters'
    assert metric['conservative_one_sided_95'] == {'lower': 0.0, 'upper': 1.0}
    assert result['numeric_thresholds_met'] is False


def test_zero_denominator_resamples_are_not_discarded_to_inflate_certainty():
    golds = [gold(i, verdict='incorrect' if i == 0 else 'correct') for i in range(20)]
    predictions = [prediction(i, verdict='incorrect' if i == 0 else 'correct') for i in range(20)]
    metric = report(golds, predictions)['metrics']['false_correct_rate']
    cluster = metric['clusters']['student']
    assert cluster['undefined_resamples'] > 0
    assert cluster['upper'] == 1
    assert metric['conservative_one_sided_95']['upper'] == 1


def test_reports_every_observed_stratum_and_missing_required_stratum():
    result = build_evaluation_report([
        gold(1), gold(2, grade=4, question_type='fraction', difficulty='hard'),
    ], [prediction(1), prediction(2, verdict='pending_review', automatic_release=False)],
        required_strata=[EvaluationStratum(grade=3, question_type='integer_arithmetic'),
                        EvaluationStratum(grade=5, question_type='division')])
    assert result['total_questions'] == 2
    assert {entry['grade'] for entry in result['by_grade']} == {3, 4}
    assert {entry['question_type'] for entry in result['by_question_type']} == {
        'integer_arithmetic', 'fraction'}
    assert {entry['difficulty'] for entry in result['by_difficulty']} == {'medium', 'hard'}
    missing = next(entry for entry in result['by_grade_question_type'] if entry['grade'] == 5)
    assert missing['required'] is True and missing['total_questions'] == 0
    assert missing['numeric_thresholds_met'] is False
    assert 'missing_required_strata' in result['evidence_issues']
    assert result['numeric_thresholds_met'] is False


def test_blank_critical_scope_never_claims_all_strata_passed():
    result = build_evaluation_report([gold(1)], [prediction(1)], required_strata=[])
    assert 'critical_scope_not_defined' in result['evidence_issues']
    assert result['numeric_thresholds_met'] is False


def test_unsafe_releases_are_counted_not_filtered_out():
    golds = [gold(0, verdict='unanswered'), gold(1, verdict='unsupported'),
             gold(2, requires_abstention=True)]
    result = report(golds, [prediction(i, verdict='correct') for i in range(3)])
    assert result['total_questions'] == result['automatic_questions'] == 3
    assert result['conflicts'] == 3
    assert result['safety_violations'] == {
        'unanswered_as_correct': 1, 'unjudgeable_released': 1, 'required_abstention_released': 1,
    }
    assert result['numeric_thresholds_met'] is False


def test_order_independent_reproducible_report_without_child_identifiers():
    golds = [gold(i, student_key=f's{i // 2}', page_key=f'p{i // 2}') for i in range(20)]
    predictions = [prediction(i, verdict='correct' if i == 0 else 'incorrect') for i in range(20)]
    first = report(golds, predictions)
    assert first == report(list(reversed(golds)), list(reversed(predictions)))
    serialized = json.dumps(first, allow_nan=False)
    assert 'student_key' not in serialized and 'page_key' not in serialized
    assert 'q0' not in serialized
    assert first['method']['confidence_level'] == .95
    assert first['method']['bootstrap_resamples'] >= 9999
    assert first['method']['algorithm_version']


@pytest.mark.parametrize('mutation', ['duplicate_gold', 'duplicate_prediction', 'missing', 'extra'])
def test_predictions_must_exactly_match_gold_set(mutation):
    golds, predictions = [gold(1)], [prediction(1)]
    if mutation == 'duplicate_gold':
        golds *= 2
    elif mutation == 'duplicate_prediction':
        predictions *= 2
    elif mutation == 'missing':
        predictions = []
    else:
        predictions.append(prediction(2))
    with pytest.raises(ValueError):
        report(golds, predictions)


def test_empty_dataset_is_rejected():
    with pytest.raises(ValueError):
        report([], [])


def test_acceptance_sample_target_is_not_a_maximum_dataset_size():
    # The plan requires at least 10k questions and expansion when errors occur.
    golds = [gold(i, student_key=f's{i // 100}', page_key=f'p{i // 10}')
             for i in range(10001)]
    result = report(golds, [prediction(i) for i in range(10001)])
    assert result['total_questions'] == 10001
    assert result['metrics']['coverage']['denominator'] == 10001
    assert result['sample_targets_met'] is True
    assert result['production_eligible'] is False


def test_page_cannot_belong_to_two_student_clusters():
    with pytest.raises(ValueError):
        report([gold(1, page_key='same'), gold(2, page_key='same')], [prediction(1), prediction(2)])


@pytest.mark.parametrize('changes', [
    {'verdict': 'maybe'}, {'automatic_release': 'true'}, {'automatic_release': 1},
    {'verdict': 'pending_review'}, {'verdict': 'unsupported'}, {'accuracy': 1},
    {'question_id': ''}, {'question_id': 'q\nprivate'},
])
def test_prediction_rejects_invalid_or_client_scored_payloads(changes):
    with pytest.raises(ValidationError):
        prediction(0, **changes)


@pytest.mark.parametrize('changes', [
    {'grade': 0}, {'grade': 7}, {'grade': True}, {'grade': '3'}, {'verdict': 'maybe'},
    {'difficulty': 'unknown'}, {'student_key': ''}, {'page_key': ''},
    {'question_type': ' '}, {'question_type': 'x\nsecret'},
    {'requires_abstention': 'false'}, {'model_accuracy': 1},
])
def test_gold_rejects_invalid_metadata(changes):
    with pytest.raises(ValidationError):
        gold(0, **changes)


def test_input_models_are_immutable():
    item = gold(1)
    with pytest.raises(ValidationError):
        item.verdict = 'correct'
    assert math.isfinite(report([item], [prediction(1)])['metrics']['coverage']['estimate'])


def test_sample_and_numeric_targets_do_not_grant_production_access():
    golds = [gold(i, student_key=f's{i // 100}', page_key=f'p{i // 10}',
                  verdict='incorrect' if i % 2 else 'correct') for i in range(10000)]
    predictions = [prediction(i, verdict=item.verdict) for i, item in enumerate(golds)]
    result = report(golds, predictions)
    assert result['sample_targets_met'] is True
    assert result['numeric_thresholds_met'] is True
    assert result['metrics']['selective_accuracy']['estimate'] == 1
    assert result['metrics']['incorrect_precision']['estimate'] == 1
    assert result['metrics']['incorrect_recall']['estimate'] == 1
    assert result['evidence_issues'] == []
    assert result['production_eligible'] is False
    json.dumps(result, allow_nan=False)


def test_small_required_stratum_blocks_otherwise_passing_overall_result():
    result = build_evaluation_report(
        [gold(i, grade=3 if i < 990 else 4) for i in range(1000)],
        [prediction(i) for i in range(1000)], required_strata=[
            EvaluationStratum(grade=3, question_type='integer_arithmetic'),
            EvaluationStratum(grade=4, question_type='integer_arithmetic'),
        ],
    )
    assert result['metrics']['selective_accuracy']['conservative_one_sided_95']['lower'] > .99
    strata = {entry['grade']: entry for entry in result['by_grade_question_type']}
    assert strata[3]['numeric_thresholds_met'] is True
    assert strata[4]['numeric_thresholds_met'] is False
    assert result['numeric_thresholds_met'] is False
    assert 'required_stratum_thresholds_not_met' in result['evidence_issues']


def test_low_coverage_fails_even_with_sufficient_and_accurate_automatic_results():
    result = report([gold(i) for i in range(1000)], [
        prediction(i, automatic_release=i < 500) for i in range(1000)
    ])
    assert result['metrics']['selective_accuracy']['conservative_one_sided_95']['lower'] > .99
    assert result['metrics']['coverage']['estimate'] == .5
    assert result['numeric_thresholds_met'] is False


def test_required_abstention_is_a_violation_even_when_verdict_matches_gold():
    result = report([gold(i, requires_abstention=i == 0) for i in range(1000)], [
        prediction(i) for i in range(1000)
    ])
    assert result['conflicts'] == 0
    assert result['metrics']['selective_accuracy']['estimate'] == 1
    assert result['safety_violations']['required_abstention_released'] == 1
    assert result['numeric_thresholds_met'] is False


def test_false_incorrect_denominator_keeps_human_review_and_abstentions():
    result = report([gold(i, verdict='correct') for i in range(10)], [
        prediction(i, verdict='incorrect' if i == 0 else 'pending_review',
                   automatic_release=i == 0) for i in range(10)
    ])
    metric = result['metrics']['false_incorrect_rate']
    assert metric['numerator'] == 1 and metric['denominator'] == 10
    assert metric['estimate'] == .1


def test_cluster_interval_matches_independent_whole_cluster_resampling():
    # Unequal cluster sizes distinguish resampling clusters from resampling questions
    # or averaging cluster-level rates. Independent implementation and random stream.
    import numpy as np

    golds, predictions = [], []
    for cluster in range(10):
        for question in range(cluster + 5):
            index = len(golds)
            golds.append(gold(index, student_key=f's{cluster}', page_key=f'p{cluster}'))
            predictions.append(prediction(index, verdict=(
                'correct' if question < cluster else 'incorrect')))
    result = report(golds, predictions)
    indices = np.random.default_rng(17).integers(0, 10, size=(50000, 10))
    rates = indices.sum(axis=1) / (indices + 5).sum(axis=1)
    metric = result['metrics']['false_correct_rate']
    for cluster in ('student', 'page'):
        actual = metric['clusters'][cluster]
        assert actual['lower'] == pytest.approx(np.quantile(rates, .05), abs=.015)
        assert actual['upper'] == pytest.approx(np.quantile(rates, .95), abs=.015)


def test_reports_do_not_share_mutable_exact_bound_cache():
    golds, predictions = [gold(i) for i in range(300)], [prediction(i) for i in range(300)]
    first = report(golds, predictions)
    first['metrics']['selective_accuracy']['one_sided_95']['lower'] = 123
    assert report(golds, predictions)['metrics']['selective_accuracy']['one_sided_95']['lower'] < 1
