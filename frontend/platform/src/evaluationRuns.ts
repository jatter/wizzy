import type { EvaluationCommand } from './evaluation.ts';
export const metricNames = ['coverage','selective_accuracy','false_correct_rate','false_incorrect_rate','incorrect_precision','incorrect_recall'] as const;
export const verdicts = ['correct','incorrect','unanswered','pending_review','unsupported'] as const;
export type MetricName = typeof metricNames[number];
export interface Stratum { grade: number; question_type: string }
export interface EvaluationRun {
  id: string; revision: number; state: 'awaiting_predictions' | 'completed';
  dataset: {id: string; name: string; revision: number; split: 'test' | 'validation'; locked_digest: string};
  model: {id: string; revision: number; provider: string; model_key: string; fixed_version: string; region: string; display_name: string};
  prompt_hash: string; schema_version: string; rule_version: string; image_pipeline_version: string;
  required_strata: Stratum[]; algorithm_version: 'grading-statistics-v1'; execution_mode: 'offline_import';
  manifest_digest: string; source_reference: string | null; created_at: string; completed_at: string | null;
  usable: boolean; production_eligible: false; prediction_count: number;
}
export interface EvaluationRunCreate extends EvaluationCommand {
  dataset_id: string; dataset_revision: number; model_id: string; model_revision: number;
  prompt_hash: string; schema_version: string; rule_version: string; image_pipeline_version: string; required_strata: Stratum[];
}
export interface RunPrediction { question_id: string; verdict: typeof verdicts[number]; automatic_release: boolean }
export interface EvaluationPredictionImport extends EvaluationCommand { manifest_digest: string; source_reference: string; predictions: RunPrediction[] }
export interface Bounds { lower: number | null; upper: number | null }
export interface ClusterBounds extends Bounds { cluster_count: number; undefined_resamples: number; status: 'undefined_denominator' | 'insufficient_clusters' | 'undefined_resamples' | 'computed' }
export interface EvaluationMetric {
  numerator: number; denominator: number; estimate: number | null; one_sided_95: Bounds;
  clusters: {student: ClusterBounds; page: ClusterBounds}; conservative_one_sided_95: Bounds;
}
export interface EvaluationSummary {
  total_questions: number; automatic_questions: number; conflicts: number; prediction_counts: Record<typeof verdicts[number],number>;
  safety_violations: {unanswered_as_correct: number; unjudgeable_released: number; required_abstention_released: number};
  numeric_thresholds_met: boolean; metrics: Record<MetricName,EvaluationMetric>;
}
export interface EvaluationReport extends EvaluationSummary {
  by_grade: (EvaluationSummary & {grade: number})[];
  by_question_type: (EvaluationSummary & {question_type: string})[];
  by_difficulty: (EvaluationSummary & {difficulty: 'easy' | 'medium' | 'hard'})[];
  by_grade_question_type: (EvaluationSummary & Stratum & {required: boolean})[];
  sample_targets_met: boolean; evidence_issues: string[]; production_eligible: false;
  method: {confidence_level: number; bootstrap_resamples: number; seed: number; algorithm_version: string; bit_generator: string;
    numpy_version: string; scipy_version: string; exact_bounds: string; cluster_sampling: string; quantile_lower: string;
    quantile_upper: string; undefined_resample_policy: string; conservative_bounds: string};
}
export interface RunReport { run_id: string; manifest_digest: string; report: EvaluationReport }
const uuid = (v: unknown) => typeof v === 'string' && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(v);
const hash = (v: unknown) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
const integer = (v: unknown, min = 0): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= min;
const text = (v: unknown, max = 120): v is string => typeof v === 'string' && v.length > 0 && v.length <= max;
const version = (v: unknown) => text(v) && /^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,119}$/.test(v);
const timestamp = (v: unknown) => typeof v === 'string' && Number.isFinite(Date.parse(v));
const stratum = (v: Stratum) => !!v && integer(v.grade,1) && v.grade <= 6 && version(v.question_type);
export function validEvaluationRun(v: EvaluationRun, id?: string): boolean {
  return !!v && uuid(v.id) && (id === undefined || v.id === id) && !!v.dataset && !!v.model &&
    uuid(v.dataset.id) && text(v.dataset.name) && integer(v.dataset.revision,1) && ['test','validation'].includes(v.dataset.split) && hash(v.dataset.locked_digest) &&
    uuid(v.model.id) && integer(v.model.revision,1) && [v.model.provider,v.model.model_key,v.model.fixed_version,v.model.region,v.model.display_name].every(x => text(x,200)) &&
    [v.prompt_hash,v.manifest_digest].every(hash) && [v.schema_version,v.rule_version,v.image_pipeline_version].every(version) &&
    Array.isArray(v.required_strata) && v.required_strata.length > 0 && v.required_strata.length <= 100 && v.required_strata.every(stratum) &&
    new Set(v.required_strata.map(s => `${s.grade}/${s.question_type}`)).size === v.required_strata.length &&
    v.algorithm_version === 'grading-statistics-v1' && v.execution_mode === 'offline_import' && typeof v.usable === 'boolean' &&
    v.production_eligible === false && timestamp(v.created_at) && integer(v.prediction_count) && v.prediction_count <= 50000 &&
    (v.state === 'awaiting_predictions' ? v.revision === 1 && v.prediction_count === 0 && v.source_reference === null && v.completed_at === null :
      v.state === 'completed' && v.revision === 2 && v.prediction_count > 0 && text(v.source_reference,200) && timestamp(v.completed_at));
}
export function validPredictions(v: unknown): v is RunPrediction[] {
  return Array.isArray(v) && v.length > 0 && v.length <= 50000 && v.every(p => p && typeof p === 'object' &&
    Object.keys(p).length === 3 && uuid(p.question_id) && verdicts.includes(p.verdict) && typeof p.automatic_release === 'boolean' &&
    (!['pending_review','unsupported'].includes(p.verdict) || !p.automatic_release)) &&
    new Set(v.map(p => p.question_id.toLowerCase())).size === v.length;
}
export function evaluationRun(v: EvaluationRun): EvaluationRun {
  const {id,revision,state,prompt_hash,schema_version,rule_version,image_pipeline_version,algorithm_version,execution_mode,
    manifest_digest,source_reference,created_at,completed_at,usable,production_eligible,prediction_count} = v;
  const d=v.dataset, m=v.model;
  return {id,revision,state,prompt_hash,schema_version,rule_version,image_pipeline_version,algorithm_version,execution_mode,
    manifest_digest,source_reference,created_at,completed_at,usable,production_eligible,prediction_count,
    dataset:{id:d.id,name:d.name,revision:d.revision,split:d.split,locked_digest:d.locked_digest},
    model:{id:m.id,revision:m.revision,provider:m.provider,model_key:m.model_key,fixed_version:m.fixed_version,region:m.region,display_name:m.display_name},
    required_strata:v.required_strata.map(s => ({grade:s.grade,question_type:s.question_type}))};
}
const bounds = (v: Bounds, defined: boolean) => !!v && (defined ? typeof v.lower === 'number' && typeof v.upper === 'number' &&
  Number.isFinite(v.lower) && Number.isFinite(v.upper) && v.lower >= 0 && v.upper <= 1 && v.lower <= v.upper : v.lower === null && v.upper === null);
function validMetric(v: EvaluationMetric, total: number): boolean {
  if (!v || !integer(v.numerator) || !integer(v.denominator) || v.numerator > v.denominator || v.denominator > total ||
    !(v.denominator ? v.estimate === v.numerator/v.denominator : v.estimate === null) ||
    !bounds(v.one_sided_95,!!v.denominator) || !bounds(v.conservative_one_sided_95,!!v.denominator) || !v.clusters) return false;
  return ['student','page'].every(key => {
    const c=v.clusters[key as 'student' | 'page'];
    if (!c || !integer(c.cluster_count) || c.cluster_count > total || (total > 0 && !c.cluster_count) ||
      !integer(c.undefined_resamples) || c.undefined_resamples > 9999 || !bounds(c,!!v.denominator)) return false;
    return !v.denominator ? c.status === 'undefined_denominator' && c.undefined_resamples === 0 :
      c.cluster_count < 2 ? c.status === 'insufficient_clusters' && c.lower === 0 && c.upper === 1 && c.undefined_resamples === 0 :
        c.undefined_resamples > 0 ? c.status === 'undefined_resamples' && c.lower === 0 && c.upper === 1 : c.status === 'computed';
  });
}
function validSummary(v: EvaluationSummary): boolean {
  return !!v && integer(v.total_questions) && v.total_questions <= 50000 && integer(v.automatic_questions) && v.automatic_questions <= v.total_questions &&
    integer(v.conflicts) && v.conflicts <= v.automatic_questions && typeof v.numeric_thresholds_met === 'boolean' && !!v.prediction_counts &&
    verdicts.every(k => integer(v.prediction_counts[k])) && verdicts.reduce((n,k) => n+v.prediction_counts[k],0) === v.total_questions &&
    !!v.safety_violations && ['unanswered_as_correct','unjudgeable_released','required_abstention_released'].every(k => {
      const n=v.safety_violations[k as keyof EvaluationSummary['safety_violations']]; return integer(n) && n <= v.automatic_questions;
    }) && !!v.metrics && metricNames.every(k => validMetric(v.metrics[k],v.total_questions)) &&
    v.metrics.coverage.denominator === v.total_questions && v.metrics.coverage.numerator === v.automatic_questions &&
    v.metrics.selective_accuracy.denominator === v.automatic_questions && v.metrics.selective_accuracy.numerator === v.automatic_questions-v.conflicts;
}
const issueNames = ['critical_scope_not_defined','missing_required_strata','no_automatic_questions','no_gold_errors','insufficient_student_clusters',
  'insufficient_page_clusters','undefined_student_resamples','undefined_page_resamples','required_stratum_thresholds_not_met'];
export function validRunReport(v: RunReport, id: string, digest: string): boolean {
  const r=v?.report, m=r?.method;
  return !!v && v.run_id === id && v.manifest_digest === digest && validSummary(r) && r.total_questions > 0 &&
    r.production_eligible === false && typeof r.sample_targets_met === 'boolean' && Array.isArray(r.evidence_issues) &&
    r.evidence_issues.every(x => issueNames.includes(x)) && !!m && m.algorithm_version === 'grading-statistics-v1' && m.confidence_level === .95 &&
    m.bootstrap_resamples === 9999 && m.seed === 20261026 && m.bit_generator === 'PCG64' && version(m.numpy_version) && version(m.scipy_version) &&
    m.exact_bounds === 'separate-one-sided-clopper-pearson' && m.cluster_sampling === 'whole-cluster-multinomial-percentile' &&
    m.quantile_lower === 'lower' && m.quantile_upper === 'higher' && m.undefined_resample_policy === 'any-undefined-forces-0-1' &&
    m.conservative_bounds === 'min-lower-max-upper-across-iid-student-page' &&
    Array.isArray(r.by_grade) && r.by_grade.every(s => validSummary(s) && integer(s.grade,1) && s.grade <= 6) &&
    Array.isArray(r.by_question_type) && r.by_question_type.every(s => validSummary(s) && version(s.question_type)) &&
    Array.isArray(r.by_difficulty) && r.by_difficulty.every(s => validSummary(s) && ['easy','medium','hard'].includes(s.difficulty)) &&
    Array.isArray(r.by_grade_question_type) && r.by_grade_question_type.every(s => validSummary(s) && stratum(s) && typeof s.required === 'boolean');
}
function copyBounds(v: Bounds): Bounds { return {lower:v.lower,upper:v.upper}; }
function copyCluster(v: ClusterBounds): ClusterBounds { return {...copyBounds(v),cluster_count:v.cluster_count,undefined_resamples:v.undefined_resamples,status:v.status}; }
function copySummary(v: EvaluationSummary): EvaluationSummary {
  return {total_questions:v.total_questions,automatic_questions:v.automatic_questions,conflicts:v.conflicts,numeric_thresholds_met:v.numeric_thresholds_met,
    prediction_counts:Object.fromEntries(verdicts.map(k => [k,v.prediction_counts[k]])) as EvaluationSummary['prediction_counts'],
    safety_violations:{unanswered_as_correct:v.safety_violations.unanswered_as_correct,unjudgeable_released:v.safety_violations.unjudgeable_released,
      required_abstention_released:v.safety_violations.required_abstention_released},
    metrics:Object.fromEntries(metricNames.map(k => {const m=v.metrics[k]; return [k,{numerator:m.numerator,denominator:m.denominator,estimate:m.estimate,
      one_sided_95:copyBounds(m.one_sided_95),conservative_one_sided_95:copyBounds(m.conservative_one_sided_95),
      clusters:{student:copyCluster(m.clusters.student),page:copyCluster(m.clusters.page)}}];})) as EvaluationSummary['metrics']};
}
export function runReport(v: RunReport): RunReport {
  const r=v.report, m=r.method;
  return {run_id:v.run_id,manifest_digest:v.manifest_digest,report:{...copySummary(r),production_eligible:false,sample_targets_met:r.sample_targets_met,
    evidence_issues:[...r.evidence_issues],by_grade:r.by_grade.map(s => ({...copySummary(s),grade:s.grade})),
    by_question_type:r.by_question_type.map(s => ({...copySummary(s),question_type:s.question_type})),
    by_difficulty:r.by_difficulty.map(s => ({...copySummary(s),difficulty:s.difficulty})),
    by_grade_question_type:r.by_grade_question_type.map(s => ({...copySummary(s),grade:s.grade,question_type:s.question_type,required:s.required})),
    method:{confidence_level:m.confidence_level,bootstrap_resamples:m.bootstrap_resamples,seed:m.seed,algorithm_version:m.algorithm_version,
      bit_generator:m.bit_generator,numpy_version:m.numpy_version,scipy_version:m.scipy_version,exact_bounds:m.exact_bounds,cluster_sampling:m.cluster_sampling,
      quantile_lower:m.quantile_lower,quantile_upper:m.quantile_upper,undefined_resample_policy:m.undefined_resample_policy,conservative_bounds:m.conservative_bounds}}};
}
