/** Wire contracts for authorized, independently annotated offline corpora. */
export interface EvaluationLabel {
  stem: string; student_answer: string; correct_answer: string;
  verdict: 'correct' | 'incorrect' | 'unanswered' | 'pending_review' | 'unsupported';
  question_type: string; difficulty: 'easy' | 'medium' | 'hard';
  requires_abstention: boolean; redaction_safe: boolean; box: [number,number,number,number];
}
export interface EvaluationCommand { operation_id: string; expected_revision: number }
export interface EvaluationCreate extends EvaluationCommand { name: string; split: 'train' | 'validation' | 'test'; handbook_version: string }
export interface EvaluationIntake extends EvaluationCommand {
  question_id: string; source_revision: number; authorization_id: string; near_duplicate_group: string;
  redacted_stem: string; redacted_answer: string; redaction_note: string;
}
export interface EvaluationAnnotation extends EvaluationCommand { label: EvaluationLabel }
export interface EvaluationAdjudication extends EvaluationAnnotation { reason: string }
export interface EvaluationEntry {
  id: string; name: string; split: 'train' | 'validation' | 'test'; revision: number;
  state: 'draft' | 'locked'; handbook_version: string; created_at: string; production_eligible: false;
}
export interface EvaluationSample {
  id: string; grade: number; state: 'authorization_inactive' | 'adjudicated' | 'agreed' | 'disputed' | 'pending';
  label_count: number; student_key: string; page_key: string; redacted_stem: string | null; redacted_answer: string | null;
  own_label: EvaluationLabel | null; gold: EvaluationLabel | null; disagreement_labels: EvaluationLabel[] | null;
}
export interface EvaluationDataset extends EvaluationEntry {
  locked_at: string | null; locked_digest: string | null; usable: boolean; sample_count: number;
  samples: EvaluationSample[]; offset: number; next_offset: number | null;
  agreement: { double_labeled: number; agreed: number; disputed: number; adjudicated: number; rate: number | null };
}
const identifier = (v: unknown) => typeof v === 'string' && /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(v);
const integer = (v: unknown, min = 0) => Number.isSafeInteger(v) && (v as number) >= min;
const timestamp = (v: unknown) => typeof v === 'string' && Number.isFinite(Date.parse(v));
const text = (v: unknown, max: number, min = 0) => typeof v === 'string' && v.length >= min && v.length <= max;
const version = (v: unknown) => typeof v === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,119}$/.test(v);
export function validEvaluationLabel(v: EvaluationLabel): boolean {
  return !!v && text(v.stem,8000,1) && text(v.student_answer,8000) && text(v.correct_answer,8000) &&
    ['correct','incorrect','unanswered','pending_review','unsupported'].includes(v.verdict) &&
    version(v.question_type) && ['easy','medium','hard'].includes(v.difficulty) &&
    typeof v.requires_abstention === 'boolean' && typeof v.redaction_safe === 'boolean' &&
    Array.isArray(v.box) && v.box.length === 4 && v.box.every(n => integer(n) && n <= 10000) &&
    v.box[0] < v.box[2] && v.box[1] < v.box[3] &&
    (!['pending_review','unsupported'].includes(v.verdict) || v.requires_abstention) &&
    (v.verdict !== 'unanswered' || !v.student_answer.trim()) &&
    (!['correct','incorrect'].includes(v.verdict) || !!v.correct_answer.trim());
}
export function validEvaluationEntry(v: EvaluationEntry): boolean {
  return !!v && identifier(v.id) && text(v.name,120,1) && ['train','validation','test'].includes(v.split) &&
    integer(v.revision,1) && ['draft','locked'].includes(v.state) && version(v.handbook_version) &&
    timestamp(v.created_at) && v.production_eligible === false;
}
export function validEvaluationDataset(v: EvaluationDataset, id?: string): boolean {
  if (!validEvaluationEntry(v) || (id !== undefined && v.id !== id) || typeof v.usable !== 'boolean' ||
    !integer(v.sample_count) || !integer(v.offset) || !(v.next_offset === null || integer(v.next_offset,v.offset+1)) ||
    !Array.isArray(v.samples) || v.samples.length > 100 || v.samples.length > v.sample_count ||
    !(v.state === 'draft' ? v.locked_at === null && v.locked_digest === null :
      timestamp(v.locked_at) && typeof v.locked_digest === 'string' && /^[a-f0-9]{64}$/.test(v.locked_digest))) return false;
  const a = v.agreement;
  if (!a || ![a.double_labeled,a.agreed,a.disputed,a.adjudicated].every(n => integer(n)) ||
    a.double_labeled > v.sample_count || a.agreed+a.disputed !== a.double_labeled || a.adjudicated > a.disputed ||
    !(a.rate === null ? a.double_labeled === 0 : Number.isFinite(a.rate) && a.rate >= 0 && a.rate <= 1 && a.double_labeled > 0)) return false;
  return new Set(v.samples.map(s => s?.id)).size === v.samples.length && v.samples.every(s => {
    if (!s || !identifier(s.id) || !integer(s.grade,1) || s.grade > 6 || !integer(s.label_count) || s.label_count > 2 ||
      !['authorization_inactive','adjudicated','agreed','disputed','pending'].includes(s.state) ||
      ![s.student_key,s.page_key].every(k => typeof k === 'string' && /^[a-f0-9]{32}$/.test(k)) ||
      !(s.own_label === null || validEvaluationLabel(s.own_label)) || !(s.gold === null || validEvaluationLabel(s.gold)) ||
      !(s.disagreement_labels === null || Array.isArray(s.disagreement_labels) && s.disagreement_labels.length === 2 && s.disagreement_labels.every(validEvaluationLabel))) return false;
    if (s.state === 'authorization_inactive') return [s.redacted_stem,s.redacted_answer,s.own_label,s.gold,s.disagreement_labels].every(x => x === null);
    return text(s.redacted_stem,8000,1) && text(s.redacted_answer,8000) &&
      (v.state === 'locked' ? s.gold !== null : s.gold === null) &&
      (s.disagreement_labels === null || s.state === 'disputed' && s.own_label === null);
  });
}
export function evaluationEntry(v: EvaluationEntry): EvaluationEntry {
  const {id,name,split,revision,state,handbook_version,created_at,production_eligible} = v;
  return {id,name,split,revision,state,handbook_version,created_at,production_eligible};
}
function label(v: EvaluationLabel | null): EvaluationLabel | null {
  if (v === null) return null;
  const {stem,student_answer,correct_answer,verdict,question_type,difficulty,requires_abstention,redaction_safe,box} = v;
  return {stem,student_answer,correct_answer,verdict,question_type,difficulty,requires_abstention,redaction_safe,box:[...box]};
}
export function evaluationDataset(v: EvaluationDataset): EvaluationDataset {
  return {...evaluationEntry(v),locked_at:v.locked_at,locked_digest:v.locked_digest,usable:v.usable,sample_count:v.sample_count,
    offset:v.offset,next_offset:v.next_offset,agreement:{double_labeled:v.agreement.double_labeled,agreed:v.agreement.agreed,
      disputed:v.agreement.disputed,adjudicated:v.agreement.adjudicated,rate:v.agreement.rate},
    samples:v.samples.map(s => ({id:s.id,grade:s.grade,state:s.state,label_count:s.label_count,student_key:s.student_key,page_key:s.page_key,
      redacted_stem:s.redacted_stem,redacted_answer:s.redacted_answer,own_label:label(s.own_label),gold:label(s.gold),
      disagreement_labels:s.disagreement_labels?.map(v => label(v)!) ?? null}))};
}
