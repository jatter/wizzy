import { validEvaluationRun, evaluationRun, validRunReport, runReport, type EvaluationRun, type EvaluationRunCreate, type EvaluationPredictionImport, type RunReport } from './evaluationRuns.ts';
import { evaluationDataset, evaluationEntry, validEvaluationDataset, validEvaluationEntry,
  type EvaluationEntry, type EvaluationDataset, type EvaluationCreate, type EvaluationIntake,
  type EvaluationAnnotation, type EvaluationAdjudication, type EvaluationCommand } from './evaluation.ts';
export interface Identity {
  id: string; email: string; role: string; audience: 'platform'; permissions: string[];
  session_id: string; expires_at: string;
}
export interface Account { id: string; email: string; role: string; status: string; created_at: string }
export type AccountRole = 'operator' | 'security_admin' | 'evaluation_annotator' | 'evaluation_adjudicator';
export type AccountStatus = 'active' | 'disabled';
export interface AccountDetail extends Account { role: AccountRole; status: AccountStatus; revision: number }
export interface AccountDecision { operation_id: string; reason: string }
export interface SessionRevocation extends AccountDecision {
  id: string; account_id: string; actor_id: string; revoked_count: number; created_at: string;
}
export interface AccountWrite extends AccountDecision { expected_revision: number; role: AccountRole; status: AccountStatus }
export interface AccountChange {
  id: string; account_id: string; actor_id: string; expected_revision: number;
  previous_role: AccountRole; previous_status: AccountStatus; role: AccountRole; status: AccountStatus;
  reason: string; requires_approval: boolean; created_at: string; state: 'pending' | 'applied' | 'approved' | 'rejected';
  decided_by: string | null; decision_reason: string | null; decided_at: string | null;
  resulting_revision: number | null; stale: boolean;
}
const uuid = (v: unknown) => typeof v === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v);
const timestamp = (v: unknown) => typeof v === 'string' && Number.isFinite(Date.parse(v));
const accountRole = (v: unknown) => v === 'operator' || v === 'security_admin' || v === 'evaluation_annotator' || v === 'evaluation_adjudicator';
const accountStatus = (v: unknown) => v === 'active' || v === 'disabled';
const revision = (v: unknown) => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
function validAccount(value: AccountDetail) {
  return value && uuid(value.id) && typeof value.email === 'string' && accountRole(value.role) &&
    accountStatus(value.status) && timestamp(value.created_at) && revision(value.revision);
}
function validChange(value: AccountChange) {
  if (!value || !['id','account_id','actor_id'].every(k => uuid(value[k as keyof AccountChange])) ||
    !revision(value.expected_revision) || !accountRole(value.previous_role) || !accountRole(value.role) ||
    !accountStatus(value.previous_status) || !accountStatus(value.status) ||
    typeof value.reason !== 'string' || !timestamp(value.created_at) ||
    typeof value.requires_approval !== 'boolean' || typeof value.stale !== 'boolean') return false;
  if (value.state === 'pending') return value.requires_approval && value.decided_by === null &&
    value.decision_reason === null && value.decided_at === null && value.resulting_revision === null;
  return ['applied','approved','rejected'].includes(value.state) && !value.stale && uuid(value.decided_by) &&
    typeof value.decision_reason === 'string' && timestamp(value.decided_at) &&
    (value.state === 'rejected' ? value.resulting_revision === null : revision(value.resulting_revision) && value.resulting_revision! > 0);
}
function validSessionRevocation(value: SessionRevocation, accountId: string) {
  return value && ['id','account_id','actor_id','operation_id'].every(k => uuid(value[k as keyof SessionRevocation])) &&
    value.account_id === accountId && value.account_id !== value.actor_id && revision(value.revoked_count) &&
    typeof value.reason === 'string' && value.reason.trim() === value.reason && value.reason.length > 0 &&
    value.reason.length <= 500 && !/[\x00-\x1f\x7f]/.test(value.reason) && timestamp(value.created_at);
}
export interface AuditEvent { id: string; actor_id: string | null; action: string; request_id: string; created_at: string }
export interface Role { name: string; permissions: string[] }
export interface Page<T> { items: T[]; next_cursor: string | null }
export interface Login { email: string; password: string; captcha_id: string; captcha: string }
export interface CaptchaChallenge { captcha_id: string; image: string }

export type ModelPurpose = 'ocr' | 'structure' | 'verify' | 'tutor' | 'report';
export type CatalogStatus = 'disabled' | 'contract_unapproved' | 'contract_expired' | 'credential_missing' | 'available';
export type ContractStatus = 'pending' | 'approved' | 'rejected' | 'expired';
export interface ModelMetadata {
  display_name: string; purposes: ModelPurpose[]; contract_status: ContractStatus;
  contract_reference: string | null; contract_expires_on: string | null;
  input_price_micros: number | null; output_price_micros: number | null; enabled: boolean;
}
export interface ModelIdentity { provider: string; model_key: string; fixed_version: string; region: string }
export interface ModelEntry extends ModelMetadata, ModelIdentity {
  id: string; revision: number; revision_id: string; reason: string; created_at: string; registered_at: string;
  credential_configured: boolean; catalog_status: CatalogStatus; accuracy: null; evaluation_status: 'not_evaluated'; production_eligible: false;
}
export interface ModelWrite extends ModelMetadata { operation_id: string; secret_ref?: string | null; reason: string }
export type ModelCreate = ModelWrite & ModelIdentity;
export type ModelRevision = ModelWrite & { expected_revision: number };
export interface ModelFilter { q?: string; enabled?: boolean; cursor?: string }
const modelPurposes = ['ocr','structure','verify','tutor','report'];
function validModel(item: ModelEntry) {
  const date = (v: unknown) => typeof v === 'string' && Number.isFinite(Date.parse(v));
  const price = (v: unknown) => v === null || (typeof v === 'number' && Number.isSafeInteger(v) && v >= 0);
  return item && !Object.hasOwn(item,'secret_ref') &&
    ['id','provider','model_key','fixed_version','region','display_name','revision_id','reason'].every(k =>
      typeof item[k as keyof ModelEntry] === 'string') &&
    Number.isSafeInteger(item.revision) && item.revision > 0 && date(item.created_at) && date(item.registered_at) &&
    Array.isArray(item.purposes) && item.purposes.length > 0 && item.purposes.length <= 5 &&
    item.purposes.every(p => modelPurposes.includes(p)) && new Set(item.purposes).size === item.purposes.length &&
    ['pending','approved','rejected','expired'].includes(item.contract_status) &&
    (item.contract_reference === null || typeof item.contract_reference === 'string') &&
    (item.contract_expires_on === null || (/^\d{4}-\d{2}-\d{2}$/.test(item.contract_expires_on) && date(item.contract_expires_on))) &&
    price(item.input_price_micros) && price(item.output_price_micros) &&
    typeof item.enabled === 'boolean' && typeof item.credential_configured === 'boolean' &&
    ['disabled','contract_unapproved','contract_expired','credential_missing','available'].includes(item.catalog_status) &&
    item.accuracy === null && item.evaluation_status === 'not_evaluated' && item.production_eligible === false;
}

export interface ModelConfig {
  id: string; name: string; base_url: string; priority: number; api_key_configured: boolean; api_key_hint: string | null;
  last_test_ok: boolean | null; last_test_at: string | null; last_test_message: string | null; created_at: string; updated_at: string;
}
export interface ModelConfigWrite { name: string; base_url: string; api_key: string | null; priority: number }
export interface ModelConfigTest { ok: boolean; message: string; elapsed_ms: number; config: ModelConfig }
export interface GradingPrompt {
  id: string; name: string; content: string; priority: number; enabled: boolean; created_at: string; updated_at: string;
}
export interface GradingPromptWrite { name: string; content: string; priority: number; enabled: boolean }
function validGradingPrompt(item: GradingPrompt) {
  return !!item && uuid(item.id) && typeof item.name === 'string' && item.name.length >= 1 && item.name.length <= 80 &&
    typeof item.content === 'string' && item.content.length >= 1 && item.content.length <= 4000 &&
    Number.isSafeInteger(item.priority) && item.priority >= 1 && item.priority <= 9999 &&
    typeof item.enabled === 'boolean' && timestamp(item.created_at) && timestamp(item.updated_at);
}
function validModelConfig(item: ModelConfig) {
  return item && uuid(item.id) && !Object.hasOwn(item,'api_key') && !Object.hasOwn(item,'api_key_cipher') &&
    typeof item.name === 'string' && typeof item.base_url === 'string' &&
    Number.isSafeInteger(item.priority) && typeof item.api_key_configured === 'boolean' &&
    (item.api_key_hint === null || typeof item.api_key_hint === 'string') &&
    (item.last_test_ok === null || typeof item.last_test_ok === 'boolean') &&
    (item.last_test_at === null || timestamp(item.last_test_at)) &&
    (item.last_test_message === null || typeof item.last_test_message === 'string') &&
    timestamp(item.created_at) && timestamp(item.updated_at);
}

export interface SchoolMetadata { name: string; region: string; academic_year: string; status: AccountStatus }
export interface SchoolEntry extends SchoolMetadata {
  id: string; code: string; revision: number; revision_id: string; reason: string;
  actor_id: string; created_at: string; registered_at: string;
}
export interface SchoolWrite extends SchoolMetadata { operation_id: string; reason: string }
export interface SchoolCreate extends SchoolWrite { code: string }
export interface SchoolRevision extends SchoolWrite { expected_revision: number }
export interface SchoolFilter { q?: string; status?: AccountStatus; cursor?: string }
function validSchool(value: SchoolEntry) {
  return value && ['id','revision_id','actor_id'].every(key => uuid(value[key as keyof SchoolEntry])) &&
    typeof value.code === 'string' && /^[A-Z0-9][A-Z0-9_-]{1,39}$/.test(value.code) &&
    ['name','region','reason'].every(key => typeof value[key as keyof SchoolEntry] === 'string' &&
      (value[key as keyof SchoolEntry] as string).trim().length > 0 &&
      (value[key as keyof SchoolEntry] as string).length <= (key === 'reason' ? 500 : 120) &&
      !/[\x00-\x1f\x7f]/.test(value[key as keyof SchoolEntry] as string)) &&
    typeof value.academic_year === 'string' && /^[0-9]{4}-[0-9]{4}$/.test(value.academic_year) &&
    Number(value.academic_year.slice(0,4)) >= 2000 && Number(value.academic_year.slice(0,4)) <= 9998 &&
    Number(value.academic_year.slice(5)) === Number(value.academic_year.slice(0,4)) + 1 &&
    accountStatus(value.status) && revision(value.revision) && value.revision > 0 && value.revision <= 999999999 &&
    timestamp(value.created_at) && timestamp(value.registered_at);
}

export type TextbookState = 'draft' | 'pending_review' | 'approved' | 'withdrawn';
export type TextbookAction = 'submit' | 'approve' | 'reject' | 'withdraw';
export type TextbookScope = 'storage' | 'ocr' | 'retrieval' | 'preview';
export interface TextbookRights {
  basis: 'publisher_contract' | 'open_license' | 'public_domain'; holder: string; reference: string;
  scopes: TextbookScope[]; regions: string[]; valid_from: string; valid_until: string;
}
export interface TextbookMetadata {
  title: string; stage: 'primary' | 'junior' | 'senior'; grade: number; subject: string; publisher: string;
  edition_label: string; academic_year: string; volume: 'first' | 'second' | 'full'; rights: TextbookRights | null;
}
export interface TextbookEntry extends TextbookMetadata {
  id: string; code: string; revision: number; revision_id: string; state: TextbookState;
  action: 'create' | 'revise' | TextbookAction; creator_id: string; editor_id: string; submitted_by: string | null;
  actor_id: string; reason: string; created_at: string; registered_at: string;
  availability: 'metadata_only' | 'pending_review' | 'expired' | 'scheduled' | 'rights_approved' | 'withdrawn';
  content_available: false;
}
export interface TextbookDecision { operation_id: string; expected_revision: number; reason: string }
export interface TextbookCreate extends TextbookMetadata { operation_id: string; code: string; reason: string }
export type TextbookRevision = TextbookMetadata & TextbookDecision;
export interface TextbookDocument {
  id: string; edition_id: string; edition_revision: number; filename: string; size_bytes: number; sha256: string;
  status: 'awaiting_upload' | 'quarantined'; scan_status: 'not_scanned'; content_available: false;
  created_by: string; operation_id: string; reason: string; created_at: string; expires_at: string; received_at: string | null;
}
export type ProcessingState = 'queued' | 'running' | 'draft_ready' | 'no_text' | 'rejected' | 'unavailable' | 'blocked';
export interface TextbookProcessingJob {
  id: string; document_id: string; edition_id: string; edition_revision: number; created_by: string; reason: string;
  state: ProcessingState; attempts: number; created_at: string; finished_at: string | null; code: string | null;
  scan: { engine: string; database_revision: number; database_date: string; checked_at: string } | null;
  page_count: number | null; character_count: number | null; pages_without_text: number[] | null;
  requires_review: true; ocr_performed: false; content_available: false;
}
export interface TextbookProcessingPage {
  job_id: string; page_count: number; page: {number: number; text: string};
  requires_review: true; ocr_performed: false; content_available: false;
}
export interface TextbookContentBody {
  pages: {number: number; text: string; verified: boolean}[];
  chapters: {code: string; title: string; parent_code: string | null; first_page: number; last_page: number}[];
}
export type ContentAction = 'create' | 'revise' | 'submit' | 'approve' | 'reject' | 'withdraw';
export interface TextbookContent {
  id: string; job_id: string; edition_id: string; edition_revision: number; document_id: string;
  revision: number; revision_id: string; current_revision: number; state: 'draft' | 'pending_review' | 'published' | 'withdrawn';
  created_by: string; submitted_by: string | null; actor_id: string; action: ContentAction; reason: string;
  created_at: string; source_created_at: string; page_count: number; chapter_count: number; verified_pages: number;
  sample_pages: number[]; required_sample_pages: number[]; contributors: string[]; ready: boolean;
  ocr_performed: false; content_available: boolean;
}
export type ContentWrite = TextbookDecision & {body?: TextbookContentBody; sample_pages?: number[]};
function validContentBody(value: TextbookContentBody) {
  if (!value || !Array.isArray(value.pages) || value.pages.length < 1 || value.pages.length > 500 ||
    !value.pages.every((p,i) => p && p.number === i+1 && typeof p.verified === 'boolean' && typeof p.text === 'string' &&
      [...p.text].length <= 100000 && !/[\x00\uD800-\uDFFF]/u.test(p.text)) ||
    value.pages.reduce((n,p) => n+[...p.text].length,0) > 1000000 || !Array.isArray(value.chapters) || value.chapters.length > 500) return false;
  const seen = new Map<string,TextbookContentBody['chapters'][number]>();
  return value.chapters.every(c => {
    if (!c || typeof c.code !== 'string' || !/^[A-Z0-9][A-Z0-9_-]{0,39}$/.test(c.code) || seen.has(c.code) || typeof c.title !== 'string' || !c.title.trim() ||
      c.title !== c.title.trim() || [...c.title].length > 200 || /[\x00-\x1f\x7f]/.test(c.title) ||
      !Number.isInteger(c.first_page) || !Number.isInteger(c.last_page) || c.first_page < 1 || c.last_page < c.first_page || c.last_page > value.pages.length) return false;
    const parent = c.parent_code === null ? null : seen.get(c.parent_code);
    if (c.parent_code !== null && (!parent || c.first_page < parent.first_page || c.last_page > parent.last_page)) return false;
    seen.set(c.code,c); return true;
  });
}
export interface TextbookUploadIntent extends TextbookDecision { filename: string; size_bytes: number; sha256: string }
export interface TextbookFilter { q?: string; state?: TextbookState; cursor?: string }
const textbookStates = ['draft','pending_review','approved','withdrawn'];
function validTextbook(value: TextbookEntry) {
  const clean = (v: unknown, max: number) => typeof v === 'string' && v.length > 0 && v.length <= max && v.trim() === v && !/[\x00-\x1f\x7f]/.test(v);
  const day = (v: unknown) => typeof v === 'string' && /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(v) &&
    v.slice(0,4) !== '0000' && Number.isFinite(Date.parse(v)) && new Date(v).toISOString().slice(0,10) === v;
  if (!value || !['id','revision_id','creator_id','editor_id','actor_id'].every(k => uuid(value[k as keyof TextbookEntry])) ||
    !/^[A-Z0-9][A-Z0-9_-]{1,39}$/.test(value.code) || !clean(value.reason,500) ||
    !['title','subject','publisher','edition_label'].every(k => clean(value[k as keyof TextbookEntry],120)) ||
    !Number.isSafeInteger(value.grade) || !(['primary','junior','senior'].includes(value.stage)) ||
    value.grade < ({primary:1,junior:7,senior:10}[value.stage]) || value.grade > ({primary:6,junior:9,senior:12}[value.stage]) ||
    !/^[0-9]{4}-[0-9]{4}$/.test(value.academic_year) || Number(value.academic_year.slice(0,4)) < 2000 ||
    Number(value.academic_year.slice(0,4)) > 9998 || Number(value.academic_year.slice(5)) !== Number(value.academic_year.slice(0,4))+1 ||
    !['first','second','full'].includes(value.volume) || !revision(value.revision) || value.revision < 1 || value.revision > 999999999 ||
    !textbookStates.includes(value.state) || !['create','revise','submit','approve','reject','withdraw'].includes(value.action) ||
    !(value.submitted_by === null || uuid(value.submitted_by)) || !timestamp(value.created_at) || !timestamp(value.registered_at) ||
    !['metadata_only','pending_review','expired','scheduled','rights_approved','withdrawn'].includes(value.availability) || value.content_available !== false) return false;
  const rights = value.rights;
  if (rights !== null && (!rights || !['publisher_contract','open_license','public_domain'].includes(rights.basis) ||
    !clean(rights.holder,200) || !clean(rights.reference,200) || !Array.isArray(rights.scopes) || rights.scopes.length < 1 || rights.scopes.length > 4 ||
    !rights.scopes.every(v => ['storage','ocr','retrieval','preview'].includes(v)) || !rights.scopes.includes('storage') || new Set(rights.scopes).size !== rights.scopes.length ||
    !Array.isArray(rights.regions) || rights.regions.length < 1 || rights.regions.length > 20 ||
    !rights.regions.every(v => typeof v === 'string' && /^[A-Z]{2}(-[A-Z0-9]{1,8})?$/.test(v)) || new Set(rights.regions).size !== rights.regions.length ||
    !day(rights.valid_from) || !day(rights.valid_until) || rights.valid_from > rights.valid_until)) return false;
  return !['pending_review','approved'].includes(value.state) || rights !== null && uuid(value.submitted_by);
}

export type StructureKind = 'campus' | 'department' | 'grade' | 'class';
export interface StructureEntry extends Omit<SchoolEntry, 'region'> {
  school_id: string; kind: StructureKind; parent_id: string | null;
}
export interface StructureTree { items: StructureEntry[]; academic_year: string; school_status: AccountStatus; school_revision: number }
export interface StructureWrite { operation_id: string; name: string; status: AccountStatus; parent_id: string | null; reason: string }
export interface StructureCreate extends StructureWrite { code: string; kind: StructureKind; academic_year: string }
export interface StructureRevision extends StructureWrite { expected_revision: number }
function validStructure(value: StructureEntry) {
  return value && validSchool({...value,region:'组织'}) && uuid(value.school_id) &&
    ['campus','department','grade','class'].includes(value.kind) && (value.parent_id === null || uuid(value.parent_id));
}
function validTree(value: StructureTree, schoolId: string, year: string) {
  if (!value || !Array.isArray(value.items) || value.items.length > 2000 || value.academic_year !== year ||
    !accountStatus(value.school_status) || !revision(value.school_revision) || value.school_revision < 1 || value.school_revision > 999999999 ||
    !value.items.every(item => validStructure(item) && item.school_id === schoolId && item.academic_year === year)) return false;
  const nodes = new Map(value.items.map(item => [item.id,item]));
  if (nodes.size !== value.items.length || new Set(value.items.map(item => item.code)).size !== nodes.size) return false;
  return value.items.every(item => {
    const parent = item.parent_id === null ? undefined : nodes.get(item.parent_id);
    if ((item.parent_id !== null && !parent) || (item.kind === 'campus' && parent) ||
      (item.kind === 'class' && parent?.kind !== 'grade') ||
      (['department','grade'].includes(item.kind) && parent && !['campus','department'].includes(parent.kind))) return false;
    let ancestor: StructureEntry | undefined = item, depth = 0;
    const seen = new Set<string>();
    while (ancestor) {
      if (seen.has(ancestor.id) || ++depth > 8) return false;
      seen.add(ancestor.id); ancestor = ancestor.parent_id === null ? undefined : nodes.get(ancestor.parent_id);
    }
    return true;
  });
}

export type EnrollmentStatus = 'enrolled' | 'paused' | 'left' | 'graduated';
export interface RosterEntry extends Omit<SchoolEntry, 'name' | 'region' | 'status'> {
  school_id: string; student_id: string; display_name: string; class_id: string | null; status: EnrollmentStatus;
}
export interface EnrollmentWrite { operation_id: string; display_name: string; class_id: string | null; status: EnrollmentStatus; reason: string }
export interface EnrollmentCreate extends EnrollmentWrite { code: string; academic_year: string }
export interface EnrollmentRevision extends EnrollmentWrite { expected_revision: number }
export interface RosterFilter { academic_year: string; q?: string; status?: EnrollmentStatus; cursor?: string }
function validEnrollment(value: RosterEntry) {
  return value && validSchool({...value,name:value.display_name,region:'学生',status:'active'}) &&
    uuid(value.school_id) && uuid(value.student_id) && value.display_name.length <= 80 &&
    (value.class_id === null || uuid(value.class_id)) && ['enrolled','paused','left','graduated'].includes(value.status);
}

const messages: Record<number, string> = {
  0: '无法连接服务，请检查网络后重试。',
  401: '登录未通过验证或会话已失效，请重新登录。',
  403: '当前账号没有此项权限，请联系安全管理员。',
  404: '记录不存在或已不可访问，请刷新列表。',
  409: '记录版本或操作键冲突，请刷新详情、核对最新版本后重试。',
  422: '提交内容未通过校验，请检查后重试。',
  429: '请求过于频繁，请稍后重试。',
};
const conflictMessages: Record<string, string> = {
  self_session_revoke: '本人请使用退出全部会话功能。',
  model_config_conflict: '相同模型名和调用地址已存在，请修改后保存。',
  grading_prompt_conflict: '相同名称的提示词已存在，请修改后保存。',
  draft_rights_unavailable: '来源版本或预览授权已变化，请刷新教材；正文与预览已清除。',
  preview_source_mismatch: '原 PDF 与上传记录不一致，已停止正文校核，请联系管理员。',
  content_rights_unavailable: '来源授权已变化或失效，请刷新教材；正文已清除。',
  content_source_unavailable: '仅已完成文本提取的草稿可以创建校核内容。',
  content_already_exists: '该任务已有校核内容，请刷新并打开。',
  content_revision_conflict: '正文已被修改，请刷新最新版本后重新校核。',
  content_state_conflict: '内容状态已变化，请刷新后操作。',
  content_review_incomplete: '提交前须校核全部页面，并用章节覆盖全部源页。',
  content_sample_incomplete: '抽检须覆盖首末页、提取空页及全部修订页。',
  content_retrieval_unavailable: '发布前须取得检索用途授权。',
  content_publication_exists: '本教材已有发布内容，请先撤回旧内容。',
  content_not_published: '正文尚未发布或已撤回。',
  enrollment_exists: '本校学生代码在该学年已登记，请查询现有名册。',
  enrollment_revision_conflict: '在读记录已被修改，请载入最新版本并核对变更。',
  invalid_enrollment_class: '在读学生须选择同校同学年且组织路径已启用的班级。',
  structure_code_conflict: '组织代码在该学校和学年内已存在，请核对后重新登记。',
  structure_revision_conflict: '组织已被修改，请载入最新版本并核对变更。',
  structure_capacity: '该学年已达到 2000 个组织节点上限。',
  school_disabled: '学校已停用，请刷新并核对学校状态。',
  school_code_conflict: '学校代码已存在，请核对目录后重新登记。',
  textbook_code_conflict: '教材代码已存在，请核对已有档案。',
  document_rights_unavailable: '当前教材授权不可用于文件存储，请刷新详情核对授权。',
  upload_expired: '上传凭证已过期，请重新申请上传。',
  textbook_revision_conflict: '教材已被修改，请载入最新版本并核对变更。',
  textbook_state_conflict: '教材状态已变化，请刷新详情后重新操作。',
  independent_reviewer_required: '请由另一位有审核权限的人员处理。',
  textbook_rights_incomplete: '提交审核前须补齐完整有效的权利登记。',
  school_revision_conflict: '学校已被修改，请载入最新版本并核对变更。',
  operation_conflict: '操作编号已用于其他请求，请核对历史记录。',
  revision_conflict: '账号已被修改，请刷新并核对最新版本。',
  already_decided: '申请已有审批结果，请刷新历史记录。',
  separate_approver_required: '申请人不能审批自己的申请。',
  proposer_ineligible: '申请人已失去权限，无法批准此申请。',
  last_security_admin: '必须保留至少一个启用的安全管理员。',
};
export class ApiError extends Error {
  status: number;
  requestId: string;
  code: string;
  constructor(status: number, requestId = '', code = '') {
    super(code === 'stale' ? '请求所属会话已结束。' : (status === 409 && Object.hasOwn(conflictMessages,code) ? conflictMessages[code] : messages[status]) ?? '服务暂时不可用，请稍后重试。');
    this.name = 'ApiError';
    this.status = status;
    this.requestId = requestId;
    this.code = code;
  }
}

/** Same-origin platform API. Credentials live only in this instance's private memory. */
export class PlatformApi {
  #token = '';
  #revision = 0;
  #fetch: typeof fetch;
  constructor(transport: typeof fetch = globalThis.fetch.bind(globalThis)) { this.#fetch = transport; }
  clear() { this.#token = ''; this.#revision++; }
  #current(revision: number) {
    if (revision !== this.#revision) throw new ApiError(0, '', 'stale');
  }
  async #request<T>(path: string, body?: object | Blob, anonymous = false): Promise<T> {
    const revision = this.#revision;
    if (!anonymous && !this.#token) throw new ApiError(401);
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (!anonymous) headers.Authorization = `Bearer ${this.#token}`;
    const binary = body instanceof Blob;
    if (body) headers['Content-Type'] = binary ? 'application/pdf' : 'application/json';
    let response: Response;
    try {
      response = await this.#fetch(`/api/v1/admin${path}`, {
        method: binary ? 'PUT' : body ? 'POST' : 'GET', headers, body: binary ? body : body ? JSON.stringify(body) : undefined,
        credentials: 'omit', cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(binary ? 135000 : 15000),
      });
    } catch {
      this.#current(revision);
      throw new ApiError(0);
    }
    this.#current(revision);
    let envelope;
    try { envelope = await response.json(); } catch { /* Never render raw proxy responses. */ }
    this.#current(revision);
    const requestId = typeof envelope?.request_id === 'string' && /^req_[0-9a-f]{32}$/.test(envelope.request_id) ? envelope.request_id : '';
    if (!response.ok) {
      if (response.status === 401) this.clear();
      const code = response.status === 409 && typeof envelope?.error?.code === 'string' && Object.hasOwn(conflictMessages,envelope.error.code) ? envelope.error.code : '';
      throw new ApiError(response.status, requestId, code);
    }
    if (!envelope || typeof envelope !== 'object' || !('data' in envelope)) throw new ApiError(502, requestId);
    return envelope.data as T;
  }
  async captcha(): Promise<CaptchaChallenge> {
    const result = await this.#request<CaptchaChallenge>('/auth/captcha', undefined, true);
    if (!result || !uuid(result.captcha_id) || typeof result.image !== 'string' ||
        !result.image.startsWith('data:image/png;base64,')) throw new ApiError(502);
    return result;
  }
  async login(credentials: Login): Promise<Identity> {
    this.clear();
    const revision = this.#revision;
    try {
      const result = await this.#request<{access_token: string; audience: string}>('/auth/login', credentials, true);
      if (result?.audience !== 'platform' || typeof result.access_token !== 'string' || !result.access_token) throw new ApiError(502);
      this.#token = result.access_token;
      return await this.me();
    } catch (error) {
      if (revision === this.#revision) this.clear();
      throw error;
    }
  }
  async me(): Promise<Identity> {
    const result = await this.#request<Identity>('/auth/me');
    if (result?.audience !== 'platform' || !Array.isArray(result.permissions) ||
        !result.permissions.every(p => typeof p === 'string') ||
        !['id', 'email', 'role', 'session_id', 'expires_at'].every(k => typeof result[k as keyof Identity] === 'string') ||
        !Number.isFinite(Date.parse(result.expires_at))) {
      this.clear();
      throw new ApiError(502, '', 'invalid_session');
    }
    return result;
  }
  async evaluationDatasets(offset = 0) {
    const result = await this.#request<{items: EvaluationEntry[]; next_offset: number | null}>(`/evaluation-datasets?limit=50&offset=${offset}`);
    if (!Array.isArray(result?.items) || result.items.length > 50 || !result.items.every(validEvaluationEntry) ||
      !(result.next_offset === null || Number.isSafeInteger(result.next_offset) && result.next_offset > offset)) throw new ApiError(502);
    return {items:result.items.map(evaluationEntry),next_offset:result.next_offset};
  }
  async evaluationDataset(id: string, offset = 0) {
    return this.#evaluation(await this.#request<EvaluationDataset>(`/evaluation-datasets/${encodeURIComponent(id)}?limit=50&offset=${offset}`),id);
  }
  async createEvaluationDataset(payload: EvaluationCreate) {
    return this.#evaluation(await this.#request<EvaluationDataset>('/evaluation-datasets',payload));
  }
  async intakeEvaluationSample(id: string, payload: EvaluationIntake) {
    return this.#evaluation(await this.#request<EvaluationDataset>(`/evaluation-datasets/${encodeURIComponent(id)}/samples`,payload),id);
  }
  async annotateEvaluationSample(id: string, sample: string, payload: EvaluationAnnotation) {
    return this.#evaluation(await this.#request<EvaluationDataset>(`/evaluation-datasets/${encodeURIComponent(id)}/samples/${encodeURIComponent(sample)}/annotations`,payload),id);
  }
  async adjudicateEvaluationSample(id: string, sample: string, payload: EvaluationAdjudication) {
    return this.#evaluation(await this.#request<EvaluationDataset>(`/evaluation-datasets/${encodeURIComponent(id)}/samples/${encodeURIComponent(sample)}/adjudications`,payload),id);
  }
  async lockEvaluationDataset(id: string, payload: EvaluationCommand) {
    return this.#evaluation(await this.#request<EvaluationDataset>(`/evaluation-datasets/${encodeURIComponent(id)}/lock`,payload),id);
  }
  #evaluation(value: EvaluationDataset, id?: string) {
    if (!validEvaluationDataset(value,id)) throw new ApiError(502);
    return evaluationDataset(value);
  }
  async evaluationRuns(offset = 0) {
    const value = await this.#request<{items: EvaluationRun[]; next_offset: number | null}>(`/evaluation-runs?limit=50&offset=${offset}`);
    if (!Array.isArray(value?.items) || value.items.length > 50 || !value.items.every(v => validEvaluationRun(v)) ||
      new Set(value.items.map(v => v.id)).size !== value.items.length ||
      !(value.next_offset === null || Number.isSafeInteger(value.next_offset) && value.next_offset > offset)) throw new ApiError(502);
    return {items:value.items.map(evaluationRun),next_offset:value.next_offset};
  }
  async evaluationRun(id: string) {
    return this.#run(await this.#request<EvaluationRun>(`/evaluation-runs/${encodeURIComponent(id)}`),id);
  }
  async createEvaluationRun(payload: EvaluationRunCreate) {
    return this.#run(await this.#request<EvaluationRun>('/evaluation-runs',payload));
  }
  async importEvaluationPredictions(id: string, payload: EvaluationPredictionImport) {
    return this.#run(await this.#request<EvaluationRun>(`/evaluation-runs/${encodeURIComponent(id)}/predictions`,payload),id);
  }
  async evaluationRunReport(id: string, digest: string) {
    const value = await this.#request<RunReport>(`/evaluation-runs/${encodeURIComponent(id)}/report`);
    if (!validRunReport(value,id,digest)) throw new ApiError(502);
    return runReport(value);
  }
  #run(value: EvaluationRun, id?: string) {
    if (!validEvaluationRun(value,id)) throw new ApiError(502);
    return evaluationRun(value);
  }
  async roles() {
    const result = await this.#request<{items: Role[]}>('/roles');
    if (!Array.isArray(result?.items) || !result.items.every(item =>
      typeof item?.name === 'string' && Array.isArray(item.permissions) &&
      item.permissions.every(p => typeof p === 'string'))) throw new ApiError(502);
    return result;
  }
  async accounts(cursor?: string) {
    const result = await this.#request<Page<Account>>(`/accounts?${this.#query(cursor)}`);
    if (!this.#page(result) || !result.items.every(item => item &&
      ['id','email','role','status','created_at'].every(key => typeof item[key as keyof Account] === 'string') &&
      Number.isFinite(Date.parse(item.created_at)))) throw new ApiError(502);
    return result;
  }
  async account(id: string) {
    const value = await this.#request<AccountDetail>(`/accounts/${encodeURIComponent(id)}`);
    if (!validAccount(value)) throw new ApiError(502);
    return value;
  }
  async accountHistory(id: string, cursor?: string) {
    const value = await this.#request<Page<AccountChange>>(`/accounts/${encodeURIComponent(id)}/changes?${this.#query(cursor)}`);
    if (!this.#page(value) || !value.items.every(validChange)) throw new ApiError(502);
    return value;
  }
  async accountSessionRevocations(id: string, cursor?: string) {
    const value = await this.#request<Page<SessionRevocation>>(`/accounts/${encodeURIComponent(id)}/session-revocations?${this.#query(cursor)}`);
    if (!this.#page(value) || !value.items.every(item => validSessionRevocation(item,id))) throw new ApiError(502);
    return value;
  }
  async revokeAccountSessions(id: string, payload: AccountDecision) {
    const value = await this.#request<SessionRevocation>(`/accounts/${encodeURIComponent(id)}/session-revocations`,payload);
    if (!validSessionRevocation(value,id) || value.operation_id !== payload.operation_id || value.reason !== payload.reason) throw new ApiError(502);
    return value;
  }
  async changeAccount(id: string, payload: AccountWrite) {
    return this.#accountChange(await this.#request<AccountChange>(`/accounts/${encodeURIComponent(id)}/changes`,payload));
  }
  async decideAccount(id: string, decision: 'approve' | 'reject', payload: AccountDecision) {
    return this.#accountChange(await this.#request<AccountChange>(`/account-changes/${encodeURIComponent(id)}/${decision}`,payload));
  }
  #accountChange(value: AccountChange) { if (!validChange(value)) throw new ApiError(502); return value; }
  async audit(cursor?: string) {
    const result = await this.#request<Page<AuditEvent>>(`/audit-events?${this.#query(cursor)}`);
    if (!this.#page(result) || !result.items.every(item => item &&
      (item.actor_id === null || typeof item.actor_id === 'string') &&
      ['id','action','request_id','created_at'].every(key => typeof item[key as keyof AuditEvent] === 'string') &&
      Number.isFinite(Date.parse(item.created_at)))) throw new ApiError(502);
    return result;
  }
  async models(filter: ModelFilter = {}) {
    const params = this.#query(filter.cursor);
    if (filter.q) params.set('q',filter.q);
    if (filter.enabled !== undefined) params.set('enabled',String(filter.enabled));
    return this.#modelsPage(await this.#request<Page<ModelEntry>>(`/models?${params}`));
  }
  async model(id: string) {
    return this.#model(await this.#request<ModelEntry>(`/models/${encodeURIComponent(id)}`));
  }
  async modelHistory(id: string, cursor?: string) {
    return this.#modelsPage(await this.#request<Page<ModelEntry>>(`/models/${encodeURIComponent(id)}/revisions?${this.#query(cursor)}`));
  }
  async createModel(payload: ModelCreate) {
    return this.#model(await this.#request<ModelEntry>('/models',payload));
  }
  async reviseModel(id: string, payload: ModelRevision) {
    return this.#model(await this.#request<ModelEntry>(`/models/${encodeURIComponent(id)}/revisions`,payload));
  }
  async modelConfigs() {
    const value = await this.#request<{items: ModelConfig[]}>('/model-configs');
    if (!Array.isArray(value?.items) || !value.items.every(validModelConfig)) throw new ApiError(502);
    return value.items;
  }
  async createModelConfig(payload: ModelConfigWrite) {
    return this.#modelConfig(await this.#request<ModelConfig>('/model-configs',payload));
  }
  async updateModelConfig(id: string, payload: ModelConfigWrite) {
    return this.#modelConfig(await this.#request<ModelConfig>(`/model-configs/${encodeURIComponent(id)}`,payload));
  }
  async deleteModelConfig(id: string) {
    await this.#request<{id: string}>(`/model-configs/${encodeURIComponent(id)}/delete`,{});
  }
  async testModelConfig(id: string) {
    const value = await this.#request<ModelConfigTest>(`/model-configs/${encodeURIComponent(id)}/test`,{});
    if (!value || typeof value.ok !== 'boolean' || typeof value.message !== 'string' || !validModelConfig(value.config)) throw new ApiError(502);
    return value;
  }
  #modelConfig(value: ModelConfig) { if (!validModelConfig(value)) throw new ApiError(502); return value; }
  async gradingPrompts() {
    const value = await this.#request<{items: GradingPrompt[]}>('/grading-prompts');
    if (!Array.isArray(value?.items) || !value.items.every(validGradingPrompt)) throw new ApiError(502);
    return value.items;
  }
  async createGradingPrompt(payload: GradingPromptWrite) {
    return this.#gradingPrompt(await this.#request<GradingPrompt>('/grading-prompts',payload));
  }
  async updateGradingPrompt(id: string, payload: GradingPromptWrite) {
    return this.#gradingPrompt(await this.#request<GradingPrompt>(`/grading-prompts/${encodeURIComponent(id)}`,payload));
  }
  async deleteGradingPrompt(id: string) {
    await this.#request<{id: string}>(`/grading-prompts/${encodeURIComponent(id)}/delete`,{});
  }
  #gradingPrompt(value: GradingPrompt) { if (!validGradingPrompt(value)) throw new ApiError(502); return value; }
  #model(value: ModelEntry) { if (!validModel(value)) throw new ApiError(502); return value; }
  #modelsPage(value: Page<ModelEntry>) {
    if (!this.#page(value) || !value.items.every(validModel)) throw new ApiError(502);
    return value;
  }
  async schools(filter: SchoolFilter = {}) {
    const params = this.#query(filter.cursor);
    if (filter.q) params.set('q',filter.q);
    if (filter.status) params.set('status',filter.status);
    return this.#schoolsPage(await this.#request<Page<SchoolEntry>>(`/schools?${params}`));
  }
  async school(id: string) { return this.#school(await this.#request<SchoolEntry>(`/schools/${encodeURIComponent(id)}`)); }
  async schoolHistory(id: string, cursor?: string) {
    return this.#schoolsPage(await this.#request<Page<SchoolEntry>>(`/schools/${encodeURIComponent(id)}/revisions?${this.#query(cursor)}`));
  }
  async createSchool(payload: SchoolCreate) { return this.#school(await this.#request<SchoolEntry>('/schools',payload)); }
  async reviseSchool(id: string, payload: SchoolRevision) {
    return this.#school(await this.#request<SchoolEntry>(`/schools/${encodeURIComponent(id)}/revisions`,payload));
  }
  #school(value: SchoolEntry): SchoolEntry {
    if (!validSchool(value)) throw new ApiError(502);
    const {id,code,name,region,academic_year,status,revision,revision_id,reason,actor_id,created_at,registered_at} = value;
    return {id,code,name,region,academic_year,status,revision,revision_id,reason,actor_id,created_at,registered_at};
  }
  #schoolsPage(value: Page<SchoolEntry>) {
    if (!this.#page(value)) throw new ApiError(502);
    return {items:value.items.map(item => this.#school(item)),next_cursor:value.next_cursor};
  }
  async textbooks(filter: TextbookFilter = {}) {
    const params = this.#query(filter.cursor);
    if (filter.q) params.set('q',filter.q);
    if (filter.state) params.set('state',filter.state);
    return this.#textbooksPage(await this.#request<Page<TextbookEntry>>(`/textbook-editions?${params}`));
  }
  async textbook(id: string) { return this.#textbook(await this.#request<TextbookEntry>(`/textbook-editions/${encodeURIComponent(id)}`),id); }
  async textbookHistory(id: string, cursor?: string) {
    return this.#textbooksPage(await this.#request<Page<TextbookEntry>>(`/textbook-editions/${encodeURIComponent(id)}/revisions?${this.#query(cursor)}`),id);
  }
  async createTextbook(payload: TextbookCreate) { return this.#textbook(await this.#request<TextbookEntry>('/textbook-editions',payload)); }
  async reviseTextbook(id: string, payload: TextbookRevision) {
    return this.#textbook(await this.#request<TextbookEntry>(`/textbook-editions/${encodeURIComponent(id)}/revisions`,payload),id);
  }
  async decideTextbook(id: string, action: TextbookAction, payload: TextbookDecision) {
    return this.#textbook(await this.#request<TextbookEntry>(`/textbook-editions/${encodeURIComponent(id)}/${action}`,payload),id);
  }
  async textbookDocuments(edition: string, cursor?: string) {
    const value = await this.#request<Page<TextbookDocument>>(`/textbook-editions/${encodeURIComponent(edition)}/documents?${this.#query(cursor)}`);
    if (!this.#page(value)) throw new ApiError(502);
    return {items:value.items.map(item => this.#document(item,edition)),next_cursor:value.next_cursor};
  }
  async requestTextbookUpload(edition: string, payload: TextbookUploadIntent) {
    const result = this.#document(await this.#request<TextbookDocument>(`/textbook-editions/${encodeURIComponent(edition)}/documents`,payload),edition);
    if (result.operation_id !== payload.operation_id || result.edition_revision !== payload.expected_revision ||
      result.filename !== payload.filename || result.size_bytes !== payload.size_bytes || result.sha256 !== payload.sha256 || result.reason !== payload.reason) throw new ApiError(502);
    return result;
  }
  async uploadTextbook(edition: string, document: string, file: Blob) {
    const result = this.#document(await this.#request<TextbookDocument>(`/textbook-editions/${encodeURIComponent(edition)}/documents/${encodeURIComponent(document)}/content`,file),edition,document);
    if (result.status !== 'quarantined') throw new ApiError(502);
    return result;
  }
  async textbookProcessingJobs(edition: string, document: string, cursor?: string) {
    const value = await this.#request<Page<TextbookProcessingJob>>(`${this.#processingPath(edition,document)}?${this.#query(cursor)}`);
    if (!this.#page(value)) throw new ApiError(502);
    return {items:value.items.map(item => this.#processingJob(item,edition,document)),next_cursor:value.next_cursor};
  }
  async requestTextbookProcessing(edition: string, document: string, payload: TextbookDecision) {
    const value = this.#processingJob(await this.#request<TextbookProcessingJob>(this.#processingPath(edition,document),payload),edition,document);
    if (value.edition_revision !== payload.expected_revision || value.reason !== payload.reason) throw new ApiError(502);
    return value;
  }
  async textbookProcessingJob(edition: string, document: string, job: string) {
    return this.#processingJob(await this.#request<TextbookProcessingJob>(`${this.#processingPath(edition,document)}/${encodeURIComponent(job)}`),edition,document,job);
  }
  async textbookProcessingPage(edition: string, document: string, job: string, page: number): Promise<TextbookProcessingPage> {
    if (!Number.isSafeInteger(page) || page < 1 || page > 500) throw new ApiError(422);
    const value = await this.#request<TextbookProcessingPage>(`${this.#processingPath(edition,document)}/${encodeURIComponent(job)}/pages/${page}`);
    if (!value || value.job_id !== job || !uuid(value.job_id) || !Number.isInteger(value.page_count) || value.page_count < page || value.page_count > 500 ||
      !value.page || value.page.number !== page || typeof value.page.text !== 'string' || [...value.page.text].length > 100000 ||
      /[\x00\uD800-\uDFFF]/u.test(value.page.text) || value.requires_review !== true || value.ocr_performed !== false || value.content_available !== false) throw new ApiError(502);
    return {job_id:value.job_id,page_count:value.page_count,page:{number:value.page.number,text:value.page.text},requires_review:true,ocr_performed:false,content_available:false};
  }
  async textbookProcessingPageImage(edition: string, document: string, job: string, page: number, signal?: AbortSignal): Promise<Blob> {
    if (!Number.isSafeInteger(page) || page < 1 || page > 500) throw new ApiError(422);
    if (!this.#token) throw new ApiError(401);
    const revision = this.#revision, maxBytes = 8 * 1024 * 1024;
    try {
      const response = await this.#fetch(`/api/v1/admin${this.#processingPath(edition,document)}/${encodeURIComponent(job)}/pages/${page}/image`, {
        method: 'GET', headers: { Accept: 'image/png', Authorization: `Bearer ${this.#token}` },
        credentials: 'omit', cache: 'no-store', redirect: 'error',
        signal: signal ? AbortSignal.any([signal,AbortSignal.timeout(45000)]) : AbortSignal.timeout(45000),
      });
      this.#current(revision);
      if (response.status === 401) { this.clear(); throw new ApiError(401); }
      if (response.ok && response.headers.get('content-type')?.split(';')[0]?.trim().toLowerCase() !== 'image/png') {
        await response.body?.cancel(); throw new ApiError(502);
      }
      const reader = response.body?.getReader();
      if (!reader) throw new ApiError(502);
      const chunks: Uint8Array<ArrayBuffer>[] = [];
      let size = 0, finished = false;
      try {
        while (true) {
          const part = await reader.read();
          this.#current(revision);
          if (part.done) { finished = true; break; }
          size += part.value.byteLength;
          if (size > maxBytes) throw new ApiError(502);
          // Own each chunk rather than retaining a possibly reused transport buffer.
          chunks.push(new Uint8Array(part.value));
        }
      } finally {
        if (!finished) await reader.cancel();
        reader.releaseLock();
      }
      const blob = new Blob(chunks, {type:'image/png'});
      if (!response.ok) {
        let envelope;
        try { envelope = JSON.parse(await blob.text()); } catch { /* Ignore proxy/error text. */ }
        this.#current(revision);
        const requestId = typeof envelope?.request_id === 'string' && /^req_[0-9a-f]{32}$/.test(envelope.request_id) ? envelope.request_id : '';
        const code = response.status === 409 && typeof envelope?.error?.code === 'string' && Object.hasOwn(conflictMessages,envelope.error.code) ? envelope.error.code : '';
        throw new ApiError(response.status,requestId,code);
      }
      const header = new Uint8Array(await blob.slice(0,24).arrayBuffer());
      this.#current(revision);
      const signature = [137,80,78,71,13,10,26,10];
      if (size < 33 || !signature.every((v,i) => header[i] === v) || String.fromCharCode(...header.slice(12,16)) !== 'IHDR') throw new ApiError(502);
      const dimensions = new DataView(header.buffer);
      if (![dimensions.getUint32(16),dimensions.getUint32(20)].every(v => v >= 1 && v <= 1600)) throw new ApiError(502);
      return blob;
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) throw error;
      this.#current(revision);
      if (error instanceof ApiError) throw error;
      throw new ApiError(0);
    }
  }
  #contentPath(edition: string, content?: string) {
    return `/textbook-editions/${encodeURIComponent(edition)}/contents${content ? '/'+encodeURIComponent(content) : ''}`;
  }
  #content(value: TextbookContent, edition: string, content?: string) {
    const count = (v: unknown, max: number) => revision(v) && (v as number) <= max;
    const pages = (v: unknown) => Array.isArray(v) && v.length <= value.page_count && new Set(v).size === v.length &&
      v.every(n => Number.isInteger(n) && n >= 1 && n <= value.page_count);
    if (!value || ![value.id,value.job_id,value.edition_id,value.document_id,value.revision_id,value.created_by,value.actor_id].every(uuid) ||
      value.edition_id !== edition || (content && value.id !== content) || !count(value.edition_revision,999999999) || value.edition_revision < 1 ||
      !count(value.revision,999999999) || value.revision < 1 || !count(value.current_revision,999999999) || value.current_revision < value.revision ||
      !['draft','pending_review','published','withdrawn'].includes(value.state) ||
      !['create','revise','submit','approve','reject','withdraw'].includes(value.action) ||
      ({create:'draft',revise:'draft',submit:'pending_review',approve:'published',reject:'draft',withdraw:'withdrawn'}[value.action] !== value.state) ||
      !(value.submitted_by === null || uuid(value.submitted_by)) || (['pending_review','published'].includes(value.state) && !uuid(value.submitted_by)) ||
      typeof value.reason !== 'string' || !value.reason.trim() || value.reason !== value.reason.trim() || [...value.reason].length > 500 || /[\x00-\x1f\x7f]/.test(value.reason) ||
      !timestamp(value.created_at) || !timestamp(value.source_created_at) || !count(value.page_count,500) || value.page_count < 1 ||
      !count(value.chapter_count,500) || !count(value.verified_pages,value.page_count) || !pages(value.sample_pages) || !pages(value.required_sample_pages) ||
      !value.required_sample_pages.includes(1) || !value.required_sample_pages.includes(value.page_count) ||
      !Array.isArray(value.contributors) || !value.contributors.length || !value.contributors.every(uuid) || new Set(value.contributors).size !== value.contributors.length ||
      !value.contributors.includes(value.created_by) || typeof value.ready !== 'boolean' ||
      (value.ready && (value.verified_pages !== value.page_count || value.chapter_count < 1)) || value.ocr_performed !== false ||
      typeof value.content_available !== 'boolean' || (value.content_available && (value.state !== 'published' || value.revision !== value.current_revision))) throw new ApiError(502);
    return value;
  }
  async textbookContents(edition: string, cursor?: string) {
    const value = await this.#request<Page<TextbookContent>>(`${this.#contentPath(edition)}?${this.#query(cursor)}`);
    if (!this.#page(value)) throw new ApiError(502);
    return {items:value.items.map(v => this.#content(v,edition)),next_cursor:value.next_cursor};
  }
  async textbookContent(edition: string, content: string) {
    return this.#content(await this.#request<TextbookContent>(this.#contentPath(edition,content)),edition,content);
  }
  async createTextbookContent(edition: string, job: string, payload: TextbookDecision) {
    const value = this.#content(await this.#request<TextbookContent>(`/textbook-editions/${encodeURIComponent(edition)}/processing-jobs/${encodeURIComponent(job)}/content`,payload),edition);
    if (value.job_id !== job || value.edition_revision !== payload.expected_revision || value.action !== 'create') throw new ApiError(502);
    return value;
  }
  async writeTextbookContent(edition: string, content: string, action: Exclude<ContentAction,'create'>, payload: ContentWrite) {
    const value = this.#content(await this.#request<TextbookContent>(`${this.#contentPath(edition,content)}/${action === 'revise' ? 'revisions' : action}`,payload),edition,content);
    if (value.revision !== payload.expected_revision+1 || value.action !== action) throw new ApiError(502);
    return value;
  }
  async textbookContentBody(edition: string, content: string, rev: number) {
    const value = await this.#request<{content_id:string; revision:number; body:TextbookContentBody}>(`${this.#contentPath(edition,content)}/revisions/${rev}/body`);
    if (!value || value.content_id !== content || value.revision !== rev || !validContentBody(value.body)) throw new ApiError(502);
    return value.body;
  }
  async textbookContentHistory(edition: string, content: string, before?: number) {
    const value = await this.#request<{items:TextbookContent[]; next_before:number|null}>(`${this.#contentPath(edition,content)}/history?limit=50${before === undefined ? '' : '&before='+before}`);
    if (!value || !Array.isArray(value.items) || value.items.length > 50 ||
      !(value.next_before === null || Number.isInteger(value.next_before) && value.next_before > 0 && value.next_before === value.items.at(-1)?.revision)) throw new ApiError(502);
    const items = value.items.map(v => this.#content(v,edition,content));
    if (items.some((v,i) => (before !== undefined && v.revision >= before) || (i > 0 && v.revision >= items[i-1]!.revision))) throw new ApiError(502);
    return {items,next_before:value.next_before};
  }
  async textbookContentTxt(edition: string, content: string, rev: number) {
    const value = await this.#request<{content_id:string; revision:number; text:string; chapters:TextbookContentBody['chapters']}>(`${this.#contentPath(edition,content)}/txt`);
    if (!value || value.content_id !== content || value.revision !== rev || typeof value.text !== 'string' || [...value.text].length > 1010000 ||
      /[\x00\uD800-\uDFFF]/u.test(value.text) || !Array.isArray(value.chapters) || value.chapters.length > 500 ||
      !validContentBody({pages:Array.from({length:500},(_,i) => ({number:i+1,text:'',verified:false})),chapters:value.chapters})) throw new ApiError(502);
    return value;
  }
  #processingPath(edition: string, document: string) {
    return `/textbook-editions/${encodeURIComponent(edition)}/documents/${encodeURIComponent(document)}/processing-jobs`;
  }
  #processingJob(value: TextbookProcessingJob, edition: string, document: string, job?: string): TextbookProcessingJob {
    const fail = () => { throw new ApiError(502); };
    const count = (v: unknown, max: number) => revision(v) && (v as number) <= max;
    const clean = (v: unknown, max: number) => typeof v === 'string' && v.length > 0 && v.length <= max && !/[\x00-\x1f\x7f]/.test(v);
    if (!value || ![value.id,value.document_id,value.edition_id,value.created_by].every(uuid) || value.edition_id !== edition || value.document_id !== document || (job && value.id !== job) ||
      !count(value.edition_revision,999999999) || value.edition_revision < 1 || !clean(value.reason,500) || value.reason.trim() !== value.reason ||
      !count(value.attempts,3) || !timestamp(value.created_at) || value.requires_review !== true || value.ocr_performed !== false || value.content_available !== false) return fail();
    const draft = value.state === 'draft_ready' || value.state === 'no_text';
    const active = value.state === 'queued' || value.state === 'running';
    const unscanned = ['source_mismatch','scanner_rejected','source_unavailable','scanner_unavailable','storage_unavailable','authorization_changed','attempts_exhausted'];
    const codes = {rejected:['source_mismatch','scanner_rejected','encrypted_pdf','page_limit_exceeded','unsupported_pdf_features','extraction_limit_exceeded','invalid_pdf'],
      unavailable:['source_unavailable','scanner_unavailable','storage_unavailable','extraction_failed','extraction_timeout','extraction_unavailable'],blocked:['authorization_changed','attempts_exhausted']};
    if (!(active || draft || (Object.hasOwn(codes,value.state) && codes[value.state as keyof typeof codes].includes(value.code!))) ||
      (active ? value.finished_at !== null : !timestamp(value.finished_at)) || ((active || draft) && value.code !== null)) return fail();
    if (active || unscanned.includes(value.code!)) { if (value.scan !== null) return fail(); }
    else if (!value.scan || !clean(value.scan.engine,100) || !count(value.scan.database_revision,999999999999) || value.scan.database_revision < 1 ||
      !clean(value.scan.database_date,100) || !timestamp(value.scan.checked_at)) return fail();
    if (draft) {
      if (!count(value.page_count,500) || value.page_count! < 1 || !count(value.character_count,1000000) || !Array.isArray(value.pages_without_text) ||
        !value.pages_without_text.every((n,i,a) => Number.isInteger(n) && n >= 1 && n <= value.page_count! && (i === 0 || a[i-1]! < n)) ||
        (value.state === 'no_text' ? value.pages_without_text.length !== value.page_count : value.pages_without_text.length >= value.page_count!)) return fail();
    } else if (value.page_count !== null || value.character_count !== null || value.pages_without_text !== null) return fail();
    return {id:value.id,document_id:value.document_id,edition_id:value.edition_id,edition_revision:value.edition_revision,created_by:value.created_by,reason:value.reason,
      state:value.state,attempts:value.attempts,created_at:value.created_at,finished_at:value.finished_at,code:value.code,
      scan:value.scan === null ? null : {engine:value.scan.engine,database_revision:value.scan.database_revision,database_date:value.scan.database_date,checked_at:value.scan.checked_at},
      page_count:value.page_count,character_count:value.character_count,pages_without_text:value.pages_without_text === null ? null : [...value.pages_without_text],requires_review:true,ocr_performed:false,content_available:false};
  }
  #document(value: TextbookDocument, edition: string, id?: string): TextbookDocument {
    if (!value || ![value.id,value.edition_id,value.created_by,value.operation_id].every(uuid) || value.edition_id !== edition || (id && value.id !== id) ||
      !revision(value.edition_revision) || value.edition_revision < 1 || value.edition_revision > 999999999 ||
      typeof value.filename !== 'string' || value.filename.length < 5 || value.filename.length > 160 || value.filename.trim() !== value.filename || /[\x00-\x1f\x7f/\\]/.test(value.filename) || !/\.pdf$/i.test(value.filename) ||
      !Number.isSafeInteger(value.size_bytes) || value.size_bytes < 1 || value.size_bytes > 52428800 || typeof value.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(value.sha256) ||
      !['awaiting_upload','quarantined'].includes(value.status) || value.scan_status !== 'not_scanned' || value.content_available !== false ||
      typeof value.reason !== 'string' || !value.reason || value.reason.length > 500 || value.reason.trim() !== value.reason || /[\x00-\x1f\x7f]/.test(value.reason) ||
      !timestamp(value.created_at) || !timestamp(value.expires_at) || Date.parse(value.expires_at) <= Date.parse(value.created_at) ||
      !(value.status === 'awaiting_upload' ? value.received_at === null : timestamp(value.received_at))) throw new ApiError(502);
    const {id: documentId,edition_id,edition_revision,filename,size_bytes,sha256,status,scan_status,content_available,created_by,operation_id,reason,created_at,expires_at,received_at} = value;
    return {id:documentId,edition_id,edition_revision,filename,size_bytes,sha256,status,scan_status,content_available,created_by,operation_id,reason,created_at,expires_at,received_at};
  }
  #textbook(value: TextbookEntry, id?: string): TextbookEntry {
    if (!validTextbook(value) || id && value.id !== id) throw new ApiError(502);
    const {title,stage,grade,subject,publisher,edition_label,academic_year,volume,rights,id:editionId,code,revision,revision_id,
      state,action,creator_id,editor_id,submitted_by,actor_id,reason,created_at,registered_at,availability,content_available} = value;
    const safeRights = rights === null ? null : {basis:rights.basis,holder:rights.holder,reference:rights.reference,
      scopes:[...rights.scopes],regions:[...rights.regions],valid_from:rights.valid_from,valid_until:rights.valid_until};
    return {title,stage,grade,subject,publisher,edition_label,academic_year,volume,rights:safeRights,id:editionId,code,revision,revision_id,
      state,action,creator_id,editor_id,submitted_by,actor_id,reason,created_at,registered_at,availability,content_available};
  }
  #textbooksPage(value: Page<TextbookEntry>, id?: string) {
    if (!this.#page(value)) throw new ApiError(502);
    return {items:value.items.map(item => this.#textbook(item,id)),next_cursor:value.next_cursor};
  }
  async schoolStructure(schoolId: string, year: string) {
    const value = await this.#request<StructureTree>(`/schools/${encodeURIComponent(schoolId)}/structure?${new URLSearchParams({academic_year:year})}`);
    if (!validTree(value,schoolId,year)) throw new ApiError(502);
    return {items:value.items.map(item => this.#structure(item,schoolId)),academic_year:value.academic_year,school_status:value.school_status,school_revision:value.school_revision};
  }
  async structureNode(schoolId: string, nodeId: string) {
    return this.#structure(await this.#request<StructureEntry>(`/schools/${encodeURIComponent(schoolId)}/structure/${encodeURIComponent(nodeId)}`),schoolId,nodeId);
  }
  async structureHistory(schoolId: string, nodeId: string, cursor?: string) {
    const value = await this.#request<Page<StructureEntry>>(`/schools/${encodeURIComponent(schoolId)}/structure/${encodeURIComponent(nodeId)}/revisions?${this.#query(cursor)}`);
    if (!this.#page(value)) throw new ApiError(502);
    return {items:value.items.map(item => this.#structure(item,schoolId,nodeId)),next_cursor:value.next_cursor};
  }
  async createStructure(schoolId: string, payload: StructureCreate) {
    return this.#structure(await this.#request<StructureEntry>(`/schools/${encodeURIComponent(schoolId)}/structure`,payload),schoolId);
  }
  async reviseStructure(schoolId: string, nodeId: string, payload: StructureRevision) {
    return this.#structure(await this.#request<StructureEntry>(`/schools/${encodeURIComponent(schoolId)}/structure/${encodeURIComponent(nodeId)}/revisions`,payload),schoolId,nodeId);
  }
  #structure(value: StructureEntry, schoolId: string, nodeId?: string): StructureEntry {
    if (!validStructure(value) || value.school_id !== schoolId || (nodeId !== undefined && value.id !== nodeId)) throw new ApiError(502);
    const {id,school_id,code,kind,name,academic_year,parent_id,status,revision,revision_id,reason,actor_id,created_at,registered_at} = value;
    return {id,school_id,code,kind,name,academic_year,parent_id,status,revision,revision_id,reason,actor_id,created_at,registered_at};
  }
  async schoolRoster(schoolId: string, filter: RosterFilter) {
    const params = this.#query(filter.cursor);
    params.set('academic_year',filter.academic_year);
    if (filter.q) params.set('q',filter.q);
    if (filter.status) params.set('status',filter.status);
    const value = await this.#request<Page<RosterEntry>>(`/schools/${encodeURIComponent(schoolId)}/roster?${params}`);
    if (!this.#page(value)) throw new ApiError(502);
    const items = value.items.map(item => this.#enrollment(item,schoolId));
    if (items.some(item => item.academic_year !== filter.academic_year)) throw new ApiError(502);
    return {items,next_cursor:value.next_cursor};
  }
  async rosterEnrollment(schoolId: string, id: string) {
    return this.#enrollment(await this.#request<RosterEntry>(`/schools/${encodeURIComponent(schoolId)}/roster/${encodeURIComponent(id)}`),schoolId,id);
  }
  async rosterHistory(schoolId: string, id: string, cursor?: string) {
    const value = await this.#request<Page<RosterEntry>>(`/schools/${encodeURIComponent(schoolId)}/roster/${encodeURIComponent(id)}/revisions?${this.#query(cursor)}`);
    if (!this.#page(value)) throw new ApiError(502);
    return {items:value.items.map(item => this.#enrollment(item,schoolId,id)),next_cursor:value.next_cursor};
  }
  async createEnrollment(schoolId: string, payload: EnrollmentCreate) {
    return this.#enrollment(await this.#request<RosterEntry>(`/schools/${encodeURIComponent(schoolId)}/roster`,payload),schoolId);
  }
  async reviseEnrollment(schoolId: string, id: string, payload: EnrollmentRevision) {
    return this.#enrollment(await this.#request<RosterEntry>(`/schools/${encodeURIComponent(schoolId)}/roster/${encodeURIComponent(id)}/revisions`,payload),schoolId,id);
  }
  #enrollment(value: RosterEntry, schoolId: string, id?: string): RosterEntry {
    if (!validEnrollment(value) || value.school_id !== schoolId || (id !== undefined && value.id !== id)) throw new ApiError(502);
    const {id:entryId,school_id,student_id,code,display_name,academic_year,class_id,status,revision,revision_id,reason,actor_id,created_at,registered_at} = value;
    return {id:entryId,school_id,student_id,code,display_name,academic_year,class_id,status,revision,revision_id,reason,actor_id,created_at,registered_at};
  }
  #page(value: Page<unknown>) {
    return Array.isArray(value?.items) && (value.next_cursor === null || typeof value.next_cursor === 'string');
  }
  #query(cursor?: string) {
    const params = new URLSearchParams({limit: '50'});
    if (cursor) params.set('cursor', cursor);
    return params;
  }
  async logout(all = false) {
    const revision = this.#revision;
    try { await this.#request('/auth/' + (all ? 'logout-all' : 'logout'), {}); }
    finally { if (revision === this.#revision) this.clear(); }
  }
}
