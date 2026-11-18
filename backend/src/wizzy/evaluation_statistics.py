"""Offline model-quality statistics; never a production-admission authority."""
from collections.abc import Sequence
from functools import lru_cache
from typing import Annotated, Literal

import numpy as np
import scipy
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)
from scipy.stats import binomtest

Verdict = Literal['correct', 'incorrect', 'unanswered', 'pending_review', 'unsupported']
Identifier = Annotated[str, Field(min_length=1, max_length=120, pattern=r'^[a-zA-Z0-9][a-zA-Z0-9._/-]*$')]
Grade = Annotated[StrictInt, Field(ge=1, le=6)]


class EvaluationStratum(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    grade: Grade
    question_type: Identifier


class EvaluationGold(EvaluationStratum):
    question_id: Identifier
    student_key: Identifier
    page_key: Identifier
    difficulty: Literal['easy', 'medium', 'hard']
    verdict: Verdict
    requires_abstention: StrictBool = False


class EvaluationPrediction(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    question_id: Identifier
    verdict: Verdict
    automatic_release: StrictBool

    @field_validator('question_id')
    @classmethod
    def no_control_characters(cls, value):
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError('Control characters are not identifiers')
        return value

    @model_validator(mode='after')
    def abstentions_are_not_automatic(self):
        if self.automatic_release and self.verdict in ('pending_review', 'unsupported'):
            raise ValueError('An abstention cannot be automatically released')
        return self


METRICS = (
    'coverage', 'selective_accuracy', 'false_correct_rate', 'false_incorrect_rate',
    'incorrect_precision', 'incorrect_recall',
)
VERDICTS = ('correct', 'incorrect', 'unanswered', 'pending_review', 'unsupported')
RESAMPLES = 9999
SEED = 20261026


@lru_cache(maxsize=256)
def _exact_bounds(numerator: int, denominator: int) -> dict:
    # These are two separate one-sided 95% bounds, not a two-sided 95% interval.
    return {
        'lower': float(binomtest(numerator, denominator, alternative='greater')
                       .proportion_ci(.95, method='exact').low),
        'upper': float(binomtest(numerator, denominator, alternative='less')
                       .proportion_ci(.95, method='exact').high),
    }


def _cluster_bounds(rows: np.ndarray, keys: list[str]) -> list[dict]:
    """Resample whole clusters; never discard a draw with an undefined ratio."""
    cluster_keys = sorted(set(keys))
    cluster_count = len(cluster_keys)
    positions = {key: index for index, key in enumerate(cluster_keys)}
    totals = np.zeros((cluster_count, len(METRICS), 2), dtype=np.int64)
    for row, key in zip(rows, keys, strict=True):
        totals[positions[key]] += row
    denominators = rows[:, :, 1].sum(axis=0)
    results = [{
        'lower': None, 'upper': None, 'cluster_count': cluster_count,
        'undefined_resamples': 0, 'status': 'undefined_denominator',
    } for _ in METRICS]
    if cluster_count < 2:
        for result, denominator in zip(results, denominators, strict=True):
            if denominator:
                result.update(lower=0.0, upper=1.0, status='insufficient_clusters')
        return results

    # Identical cluster contribution vectors can share a multinomial category.
    # Each draw still selects cluster_count WHOLE clusters with replacement;
    # this only avoids expanding identical vectors, not question-level sampling.
    vectors, multiplicities = np.unique(
        totals.reshape(cluster_count, -1), axis=0, return_counts=True,
    )
    probabilities = multiplicities / cluster_count
    rng = np.random.Generator(np.random.PCG64(SEED))
    draws = np.empty((RESAMPLES, len(METRICS), 2), dtype=np.int64)
    for start in range(0, RESAMPLES, 64):
        size = min(64, RESAMPLES - start)
        weights = rng.multinomial(cluster_count, probabilities, size=size)
        draws[start:start + size] = (weights @ vectors).reshape(size, len(METRICS), 2)
    for index, denominator in enumerate(denominators):
        if not denominator:
            continue
        undefined = int(np.count_nonzero(draws[:, index, 1] == 0))
        result = results[index]
        result['undefined_resamples'] = undefined
        if undefined:
            # A missing class in even one draw is evidence of fragile support.
            result.update(lower=0.0, upper=1.0, status='undefined_resamples')
        else:
            ratios = draws[:, index, 0] / draws[:, index, 1]
            result.update(
                lower=float(np.quantile(ratios, .05, method='lower')),
                upper=float(np.quantile(ratios, .95, method='higher')),
                status='computed',
            )
    return results


def _summary(pairs: list[tuple[EvaluationGold, EvaluationPrediction]]) -> dict:
    rows = []
    counts = dict.fromkeys(VERDICTS, 0)
    safety = {'unanswered_as_correct': 0, 'unjudgeable_released': 0,
              'required_abstention_released': 0}
    conflicts = 0
    for gold, prediction in pairs:
        automatic = prediction.automatic_release
        matches = gold.verdict == prediction.verdict
        gold_error, gold_correct = gold.verdict == 'incorrect', gold.verdict == 'correct'
        predicted_error = automatic and prediction.verdict == 'incorrect'
        true_positive = predicted_error and gold_error
        counts[prediction.verdict] += 1
        conflicts += int(automatic and not matches)
        safety['unanswered_as_correct'] += int(
            automatic and gold.verdict == 'unanswered' and prediction.verdict == 'correct')
        safety['unjudgeable_released'] += int(
            automatic and gold.verdict in ('pending_review', 'unsupported'))
        safety['required_abstention_released'] += int(automatic and gold.requires_abstention)
        rows.append([
            (automatic, True), (automatic and matches, automatic),
            (automatic and gold_error and prediction.verdict == 'correct', gold_error),
            (predicted_error and gold_correct, gold_correct),
            (true_positive, predicted_error), (true_positive, gold_error),
        ])
    values = np.asarray(rows, dtype=np.int64).reshape(len(pairs), len(METRICS), 2)
    clusters = {
        name: _cluster_bounds(values, [getattr(gold, f'{name}_key') for gold, _ in pairs])
        for name in ('student', 'page')
    }
    metrics = {}
    for index, name in enumerate(METRICS):
        numerator, denominator = map(int, values[:, index, :].sum(axis=0))
        exact = _exact_bounds(numerator, denominator) if denominator else {
            'lower': None, 'upper': None,
        }
        bounds = {name: entries[index] for name, entries in clusters.items()}
        conservative = {
            'lower': min(exact['lower'], *(entry['lower'] for entry in bounds.values())),
            'upper': max(exact['upper'], *(entry['upper'] for entry in bounds.values())),
        } if denominator else {'lower': None, 'upper': None}
        metrics[name] = {
            'numerator': numerator, 'denominator': denominator,
            'estimate': numerator / denominator if denominator else None,
            'one_sided_95': dict(exact), 'clusters': bounds,
            'conservative_one_sided_95': conservative,
        }
    coverage, accuracy, risk = (metrics[name] for name in METRICS[:3])
    passes = (
        accuracy['estimate'] is not None and accuracy['estimate'] >= .99
        and accuracy['conservative_one_sided_95']['lower'] >= .99
        and coverage['estimate'] >= .6
        and risk['estimate'] is not None
        and risk['conservative_one_sided_95']['upper'] <= .01
        and not any(safety.values())
    )
    return {
        'total_questions': len(pairs), 'automatic_questions': coverage['numerator'],
        'conflicts': conflicts, 'prediction_counts': counts, 'metrics': metrics,
        'safety_violations': safety, 'numeric_thresholds_met': passes,
    }


def build_evaluation_report(
    golds: Sequence[EvaluationGold], predictions: Sequence[EvaluationPrediction],
    *, required_strata: Sequence[EvaluationStratum],
) -> dict:
    """Compute evidence only. No dataset authorization, model call or release grant."""
    if not golds:
        raise ValueError('A report requires at least one question')
    gold_by_id = {gold.question_id: gold for gold in golds}
    prediction_by_id = {prediction.question_id: prediction for prediction in predictions}
    if len(gold_by_id) != len(golds) or len(prediction_by_id) != len(predictions):
        raise ValueError('Question identifiers must be unique')
    if gold_by_id.keys() != prediction_by_id.keys():
        raise ValueError('Predictions must exactly match the gold question set')
    page_owners = {}
    for gold in golds:
        if page_owners.setdefault(gold.page_key, gold.student_key) != gold.student_key:
            raise ValueError('A page cannot belong to more than one student')
    pairs = [(gold_by_id[key], prediction_by_id[key]) for key in sorted(gold_by_id)]
    result = _summary(pairs)
    for attribute in ('grade', 'question_type', 'difficulty'):
        result[f'by_{attribute}'] = [
            {attribute: value, **_summary([
                pair for pair in pairs if getattr(pair[0], attribute) == value
            ])} for value in sorted({getattr(gold, attribute) for gold in golds})
        ]
    required = {(stratum.grade, stratum.question_type) for stratum in required_strata}
    observed = {(gold.grade, gold.question_type) for gold in golds}
    result['by_grade_question_type'] = [
        {'grade': grade, 'question_type': question_type,
         'required': (grade, question_type) in required, **_summary([
             pair for pair in pairs
             if (pair[0].grade, pair[0].question_type) == (grade, question_type)
         ])} for grade, question_type in sorted(required | observed)
    ]
    issues = []
    if not required:
        issues.append('critical_scope_not_defined')
    if required - observed:
        issues.append('missing_required_strata')
    if not result['automatic_questions']:
        issues.append('no_automatic_questions')
    if not result['metrics']['false_correct_rate']['denominator']:
        issues.append('no_gold_errors')
    for cluster in ('student', 'page'):
        if any(metric['clusters'][cluster]['status'] == 'insufficient_clusters'
               for metric in result['metrics'].values()):
            issues.append(f'insufficient_{cluster}_clusters')
        if any(metric['clusters'][cluster]['undefined_resamples']
               for metric in result['metrics'].values()):
            issues.append(f'undefined_{cluster}_resamples')
    required_reports = [entry for entry in result['by_grade_question_type'] if entry['required']]
    if any(not entry['numeric_thresholds_met'] for entry in required_reports):
        issues.append('required_stratum_thresholds_not_met')
    result['numeric_thresholds_met'] = (
        result['numeric_thresholds_met'] and bool(required)
        and all(entry['numeric_thresholds_met'] for entry in required_reports)
    )
    result.update(
        sample_targets_met=(len(golds) >= 10000 and len(page_owners) >= 1000
                            and len({gold.student_key for gold in golds}) >= 100),
        evidence_issues=issues, production_eligible=False,
        method={
            'confidence_level': .95, 'bootstrap_resamples': RESAMPLES,
            'algorithm_version': 'grading-statistics-v1', 'seed': SEED,
            'bit_generator': 'PCG64', 'numpy_version': np.__version__,
            'scipy_version': scipy.__version__,
            'exact_bounds': 'separate-one-sided-clopper-pearson',
            'cluster_sampling': 'whole-cluster-multinomial-percentile',
            'quantile_lower': 'lower', 'quantile_upper': 'higher',
            'undefined_resample_policy': 'any-undefined-forces-0-1',
            'conservative_bounds': 'min-lower-max-upper-across-iid-student-page',
        },
    )
    return result
