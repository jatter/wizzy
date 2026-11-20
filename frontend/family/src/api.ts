import type { PublicKeyCredentialCreationOptionsJSON, PublicKeyCredentialRequestOptionsJSON, RegistrationResponseJSON, AuthenticationResponseJSON } from '@simplewebauthn/browser';
import type { HomeworkDay, DailyItem, HomeworkCreate, HomeworkUpdate, VersionedHomework, WorkState, ScopeValue, DailySubject, ImportBody, HomeworkImport, HomeworkImportSummary, HomeworkDraft } from './daily-homework-types';

export interface Identity { audience: 'guardian' | 'child'; tenant_id: string | null; student_id: string | null; role: string | null; user_id?: string; email?: string }
export interface Tenant { id: string; name: string; role: string }
export interface Student { id: string; nickname: string; grade: number; revision: number; consent_active: boolean; created_at: string }
export interface Page<T> { items: T[]; next_cursor: string | null }
export interface EvaluationPolicy { version: string; text: string; optional: boolean }
export interface EvaluationConsent { revision: number; granted: boolean; active: boolean; reason: string; policy_version: string }
export interface Device { id: string; student_id: string; device_name: string; created_at: string; expires_at: string }
export interface LearningPasskey { id: string; name: string; created_at: string; last_used_at: string | null }
export interface Mistake { question_id: string; stem: string; student_answer_raw: string; effective_revision: number; created_at: string }
export interface MistakeBankItem { id: string; stem: string; subject_label: string; incorrect_count: number; correct_count: number; latest_verdict: string; latest_day: string; repeated_count: number; guardian_note?: string }
export interface MistakeBankDetail { id: string; stem: string; subject_label: string; reference_answer: string; summary: string; timeline: string[]; guardian_note?: string; attempts: { id: string; day: string; verdict_label: string; student_answer: string; reasoning: string; question_number: string; page_position: number }[] }
export interface MistakeSheet { id: string; due_date: string; state: string; items: { id: string; stem: string; returned: boolean; reference_answer?: string }[] }
export interface ReviewTask { id: string; student_id: string; stem: string; question_id: string; due_date: string; state: string; source_is_current: boolean; draft: { revision: number; student_answer: string }; attempts: { id: string; outcome: string; verdict: string }[] }
export interface UploadIntent { media_id: string; upload_url: string; expires_at: string; max_size_bytes: number }
export type HomeworkSubject = 'math' | 'english' | 'chinese' | 'auto';
export type HomeworkActivity = 'auto' | 'exercise' | 'dictation';
export interface SubmissionBody { student_id: string; subject: HomeworkSubject; activity_type?: HomeworkActivity; reference_text?: string; completed_attempt: true; media_ids: string[]; client_request_id: string }
export interface SubmissionReceipt { submission_id: string; job_id: string; status: string; source_revision: number }
export interface Submission { id: string; student_id: string; subject: string; status: string; source_revision: number; created_at: string; page_count?: number }
export interface SubmissionFilters {
  date_from?: string; date_to?: string; subject?: HomeworkSubject;
  status?: 'queued' | 'processing' | 'completed' | 'partial' | 'failed' | 'cancelled';
}
export interface SubmissionDetail extends Submission { activity_type?: HomeworkActivity; reference_text?: string; job_id: string; pages: { position: number; media_id: string }[] }
export interface Job { id: string; submission_id: string; status: string; error_code: string | null; created_at: string; finished_at: string | null }
export interface Quota { page_limit: number; reserved_pages: number; used_pages: number; remaining_pages: number; period_start: string }
export interface ModelOutput {
  id: string; sequence: number; prompt_id: string; prompt_name: string; prompt_content: string;
  prompt_priority: number; model_id: string; model_name: string; model_priority: number;
  status: 'running' | 'succeeded' | 'failed'; content: string; error_message: string | null;
  created_at: string; finished_at: string | null; page_results?: HomeworkPageResult[];
}
export interface HomeworkPageResult {
  position: number; subject: 'math' | 'english' | 'chinese' | 'mixed' | 'unknown'; note: string;
  counts: { correct: number; incorrect: number; unanswered: number; unclear: number };
  questions: { number: string; item_type?: 'exercise' | 'word' | 'phrase'; grading_basis?: 'task_reference' | 'spelling_only' | 'none'; verdict: 'correct' | 'incorrect' | 'unanswered' | 'unclear'; student_answer: string; reasoning: string; steps: string[]; answer: string }[];
}
export interface GradingResult {
  submission_id: string; source_revision: number; review_notice: string;
  pages: { position: number; media_id: string; status: string; error_code?: string;
    questions?: { ordinal: number; stem: string; student_answer_raw: string; verdict: string }[] }[];
}
export type ReportType = 'weekly' | 'monthly';
export interface LearningReport {
  id: string; student_id: string; type: ReportType; period_start: string; period_end: string;
  timezone: string; rules_version: string; source_version: string; revision: number;
  source_count: number; stale: boolean; created_at: string;
  summary: {
    submissions: number;
    original: { accepted: number; effective: number; correct: number; incorrect: number; pending_review: number; unanswered: number; unsupported: number; accuracy: number | null };
    corrections: { attempts: number; correct: number; incorrect: number; pending_review: number; accuracy: number | null };
    reviews: { attempts: number; submitted: number; needs_help: number; skipped: number; awaiting_review: number; completed: number };
    weak_points: { status: string; items: unknown[] };
  };
}
export interface ReportSource {
  id: string; kind: 'original' | 'correction' | 'review'; source_id: string;
  question_id: string; submission_id: string; created_at: string;
  snapshot: { stem?: string; student_answer_raw?: string; student_answer?: string; verdict: string;
    verdict_provenance?: string; outcome?: string; page_position?: number; ordinal?: number; source_is_current?: boolean };
}
export interface ReviewPlanBody {
  student_id: string; type: ReportType; period_start: string; report_id: string;
  max_questions: number; max_minutes: number; minutes_per_question: number;
}
export interface ReviewPlan {
  id: string; student_id: string; type: ReportType; period_start: string; period_end: string;
  timezone: string; report_id: string | null; rules_version: string; stale: boolean; created_at: string;
  limits: { max_questions: number; max_minutes: number; minutes_per_question: number };
  question_count: number; estimated_minutes: number; duration_basis: 'planning_estimate';
  entries: { task_id: string; position: number; due_date: string; priority: 'overdue' | 'repeated_error' | 'remaining';
    confirmed_incorrect_attempts: number; task: ReviewTask | null }[];
}
export type ReviewPlanSummary = Pick<ReviewPlan, 'id' | 'student_id' | 'type' | 'period_start' | 'period_end' | 'timezone' | 'report_id' | 'created_at' | 'question_count' | 'estimated_minutes' | 'duration_basis'>;
export interface Term { id: string; student_id: string; name: string; start_date: string; end_date: string; week_starts_on: number; revision: number; timezone: string }
export interface TermBody { name: string; start_date: string; end_date: string; week_starts_on: number }
export interface LessonFields { period: number; subject: string; start_time: string | null; end_time: string | null; room: string | null }
export interface TimetableSlot extends LessonFields { id: string; weekday: number; weeks: 'all' | 'odd' | 'even' }
export interface ScheduleConflict { date: string; reason: 'same_period' | 'time_overlap' }
export interface LessonChange extends LessonFields { id: string; revision: number; slot_id: string; source_date: string; action: 'cancel' | 'move' | 'restore'; target_date: string | null; reason: string }
export interface Timetable { term: Term; slots: TimetableSlot[]; overrides: LessonChange[]; conflicts: ScheduleConflict[] }
export interface LessonInstance extends LessonFields { id: string; slot_id: string; source_date: string; date: string; week_number: number; overridden: boolean }
export interface TimetableBody { expected_revision: number; slots: TimetableSlot[]; confirm_conflicts: boolean }
export interface OverrideBody { term_id: string; expected_revision: number; slot_id: string; source_date: string; action: LessonChange['action']; reason: string; target_date?: string; replacement?: LessonFields; confirm_conflicts: boolean }
export interface RequestOptions { method: 'GET' | 'POST' | 'DELETE' | 'PUT'; headers: Record<string, string>; body?: unknown; responseType?: 'json' | 'arraybuffer' }
export interface ResponseData { status: number; body: unknown }
export type Transport = (path: string, options: RequestOptions) => Promise<ResponseData>;
interface Token { access_token?: string; token_type: string }
const errors: Record<number, string> = {
  0: '连接未完成，请检查网络后重试。', 401: '登录或配对已失效，请重新进入。',
  403: '当前账号没有权限，或尚未授权作业服务。', 404: '记录已不可访问，请刷新。',
  409: '记录已变化，请刷新后核对最新内容再操作。', 422: '请检查填写的内容和勾选项。',
  429: '操作过于频繁，请稍后重试。',
};
export class ApiError extends Error {
  status: number; code: string; requestId: string; conflicts: ScheduleConflict[];
  constructor(status: number, code = '', requestId = '', conflicts: ScheduleConflict[] = []) {
    super(code === 'stale' ? '已切换身份，请重新操作。' : code === 'family_login_failed' ?
      '家庭账号的用户名或密码不匹配，请重新输入。平台管理员请使用管理端入口。' : errors[status] ?? '服务暂时不可用，请稍后重试。');
    this.status = status; this.code = code; this.requestId = requestId; this.conflicts = conflicts;
  }
}
export function operationId() {
  // Operation keys are identifiers, never authentication credentials.
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.floor(Math.random() * 16); return (c === 'x' ? r : (r & 3) | 8).toString(16);
  });
}
const query = (cursor?: string) => '?limit=20' + (cursor ? '&cursor=' + encodeURIComponent(cursor) : '');
const id = (value: string) => encodeURIComponent(value);
export class FamilyApi {
  #token = ''; #generation = 0; #transport: Transport; #expired: () => void;
  constructor(transport: Transport, expired: () => void = () => {}) { this.#transport = transport; this.#expired = expired; }
  clear() { this.#token = ''; this.#generation++; }
  get generation() { return this.#generation; }
  async #request<T>(path: string, method: RequestOptions['method'] = 'GET', body?: unknown, headers: Record<string, string> = {}, responseType: 'json' | 'arraybuffer' = 'json', invalidate = true): Promise<T> {
    const generation = this.#generation;
    let response: ResponseData;
    try { response = await this.#transport('/api/v1' + path, { method, body, responseType, headers: {
      'Content-Type': 'application/json', ...(this.#token ? { Authorization: 'Bearer ' + this.#token } : {}), ...headers,
    } }); } catch { if (generation !== this.#generation) throw new ApiError(0, 'stale'); throw new ApiError(0); }
    if (generation !== this.#generation) throw new ApiError(0, 'stale');
    const envelope = response.body as { data?: T; error?: { code?: string; field_errors?: unknown }; request_id?: string } | null;
    if (response.status < 200 || response.status >= 300) {
      if (response.status === 401 && invalidate) { this.clear(); this.#expired(); }
      const requestId = typeof envelope?.request_id === 'string' && /^req_[a-f0-9]{32}$/.test(envelope.request_id) ? envelope.request_id : '';
      // Server messages/validation bodies can contain submitted values; use local copy only.
      const allowed = ['schedule_conflict', 'revision_conflict', 'active_override_conflict'];
      const code = response.status === 401 && path === '/auth/login' ? 'family_login_failed' :
        allowed.includes(envelope?.error?.code ?? '') ? envelope!.error!.code! : '';
      const fields = envelope?.error?.field_errors;
      const conflicts: ScheduleConflict[] = code === 'schedule_conflict' && Array.isArray(fields) ? fields.flatMap(row =>
        row && typeof row.date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(row.date) && ['same_period', 'time_overlap'].includes(row.reason)
          ? [{ date: row.date, reason: row.reason as ScheduleConflict['reason'] }] : []) : [];
      throw new ApiError(response.status, code, requestId, conflicts);
    }
    if (responseType === 'arraybuffer') {
      if (!(response.body instanceof ArrayBuffer)) throw new ApiError(502);
      return response.body as T;
    }
    if (!envelope || typeof envelope !== 'object' || !('data' in envelope)) throw new ApiError(502);
    return envelope.data as T;
  }
  async #adopt(token: Token, audience: Identity['audience']) {
    if (token?.token_type !== 'cookie' && (typeof token?.access_token !== 'string' || !token.access_token || token.token_type !== 'bearer')) throw new ApiError(502);
    this.clear(); this.#token = token.token_type === 'cookie' ? '' : token.access_token!;
    const generation = this.#generation;
    try {
      const profile = await this.me();
      if (profile.audience !== audience || (audience === 'child' && !profile.student_id)) throw new ApiError(403);
      return profile;
    } catch (error) { if (generation === this.#generation) { this.clear(); this.#expired(); } throw error; }
  }
  async login(username: string, password: string) {
    this.clear(); return this.#adopt(await this.#request<Token>('/auth/login', 'POST', { username, password }, {}, 'json', false), 'guardian');
  }
  async register(username: string, password: string) {
    this.clear(); return this.#adopt(await this.#request<Token>('/auth/register', 'POST', {
      username, password, privacy_version: '2026-10-25', accepted_privacy: true,
    }), 'guardian');
  }
  me() { return this.#request<Identity>('/auth/me'); }
  async resume() {
    this.clear();
    try {
      const identity = await this.#request<Identity>('/auth/me', 'GET', undefined, {}, 'json', false);
      if (!['guardian', 'child'].includes(identity?.audience) || (identity.audience === 'child' && !identity.student_id)) throw new ApiError(502);
      return identity;
    } catch (error) { if (error instanceof ApiError && error.status === 401) return null; throw error; }
  }
  async startLearning(student: string) {
    return this.#adopt(await this.#request<Token>('/auth/start-learning', 'POST', { student_id: student }), 'child');
  }
  passkeyRegistrationOptions() { return this.#request<PublicKeyCredentialCreationOptionsJSON>('/auth/passkeys/registration/options', 'POST'); }
  registerPasskey(credential: RegistrationResponseJSON, name: string) {
    return this.#request<LearningPasskey>('/auth/passkeys/registration/verify', 'POST', { credential, name });
  }
  passkeyAuthenticationOptions() { return this.#request<PublicKeyCredentialRequestOptionsJSON>('/auth/passkeys/authentication/options', 'POST', undefined, {}, 'json', false); }
  async loginWithPasskey(credential: AuthenticationResponseJSON) {
    return this.#adopt(await this.#request<Token>('/auth/passkeys/authentication/verify', 'POST', { credential }, {}, 'json', false), 'child');
  }
  passkeys() { return this.#request<{ items: LearningPasskey[] }>('/auth/passkeys'); }
  revokePasskey(id: string) { return this.#request<{ revoked: boolean }>('/auth/passkeys/' + encodeURIComponent(id), 'DELETE'); }
  async logout() {
    const token = this.#token;
    this.clear();
    try {
      const result = await this.#transport('/api/v1/auth/logout', { method: 'POST', headers: token ? { Authorization: 'Bearer ' + token } : {} });
      if (result.status < 200 || result.status >= 300) throw new ApiError(result.status);
    } catch (error) { throw error instanceof ApiError ? error : new ApiError(0); }
  }
  tenants() { return this.#request<{ items: Tenant[] }>('/tenants'); }
  async createTenant(name: string) { return this.#adopt(await this.#request<Token>('/tenants', 'POST', { name }), 'guardian'); }
  async activateTenant(tenant_id: string) { return this.#adopt(await this.#request<Token>('/auth/activate-tenant', 'POST', { tenant_id }), 'guardian'); }
  students(cursor?: string) { return this.#request<Page<Student>>('/students' + query(cursor)); }
  student(student: string) { return this.#request<Student>('/students/' + id(student)); }
  createStudent(nickname: string, grade: number) { return this.#request<Student>('/students', 'POST', { nickname, grade }); }
  updateStudent(student: Student, nickname: string, grade: number) { return this.#request<Student>('/students/' + id(student.id) + '/profile', 'POST', { expected_revision: student.revision, nickname, grade }); }
  serviceConsent(student_id: string, granted: boolean) { return this.#request('/consents', 'POST', { student_id, granted, policy_version: '2026-10-25', child_rules_version: '2026-10-25' }); }
  policy() { return this.#request<EvaluationPolicy>('/evaluation-policy'); }
  evaluation(student: string) { return this.#request<EvaluationConsent>('/students/' + id(student) + '/evaluation-consent'); }
  decideEvaluation(student: string, state: EvaluationConsent, granted: boolean, policy: EvaluationPolicy, key: string) {
    return this.#request<{ state: EvaluationConsent }>('/students/' + id(student) + '/evaluation-consent', 'POST', {
      operation_id: key, expected_revision: state.revision, granted, policy_version: policy.version,
    });
  }
  pairing(student: string) { return this.#request<{ code: string; expires_in_seconds: number }>('/students/' + id(student) + '/pairing-codes', 'POST'); }
  async pair(code: string, device_name: string) {
    this.clear(); return this.#adopt(await this.#request<Token>('/child-sessions', 'POST', { code, device_name }), 'child');
  }
  devices() { return this.#request<{ items: Device[] }>('/child-sessions'); }
  revokeDevice(device: string) { return this.#request('/child-sessions/' + id(device), 'DELETE'); }
  terms(student: string, cursor?: string) { return this.#request<Page<Term>>('/students/' + id(student) + '/terms' + query(cursor)); }
  createTerm(student: string, body: TermBody) { return this.#request<Term>('/students/' + id(student) + '/terms', 'POST', body); }
  timetable(term: string) { return this.#request<Timetable>('/timetables/' + id(term)); }
  lessons(term: string, start: string, end: string) { return this.#request<{ items: LessonInstance[]; revision: number; timezone: string; conflicts: ScheduleConflict[] }>('/timetables/' + id(term) + '/instances?start_date=' + id(start) + '&end_date=' + id(end)); }
  saveTimetable(term: string, body: TimetableBody) { return this.#request<Timetable>('/timetables/' + id(term), 'PUT', body); }
  changeLesson(body: OverrideBody) { return this.#request<Timetable>('/lesson-overrides', 'POST', body); }
  uploadIntent(student_id: string, content_type: string, size_bytes: number) {
    return this.#request<UploadIntent>('/media/upload-intents', 'POST', { student_id, content_type, size_bytes });
  }
  upload(media: string, bytes: ArrayBuffer, contentType: string) {
    return this.#request('/media/' + id(media) + '/upload', 'PUT', bytes, { 'Content-Type': contentType });
  }
  completeMedia(media: string) { return this.#request('/media/' + id(media) + '/complete', 'POST'); }
  submit(body: SubmissionBody, key: string) { return this.#request<SubmissionReceipt>('/submissions', 'POST', body, { 'Idempotency-Key': key }); }
  submissions(student: string, cursor?: string, filters: SubmissionFilters = {}) {
    const fields = (['date_from', 'date_to', 'subject', 'status'] as const)
      .filter(key => filters[key]).map(key => '&' + key + '=' + id(filters[key]!)).join('');
    return this.#request<Page<Submission>>('/submissions' + query(cursor) + '&student_id=' + id(student) + fields);
  }
  submission(submission: string) { return this.#request<SubmissionDetail>('/submissions/' + id(submission)); }
  submissionDays(student: string) { return this.#request<{ items: { date: string; count: number }[] }>('/students/' + id(student) + '/submission-days'); }
  result(submission: string) { return this.#request<GradingResult>('/submissions/' + id(submission) + '/result'); }
  modelOutputs(submission: string) { return this.#request<{ items: ModelOutput[] }>('/submissions/' + id(submission) + '/model-outputs'); }
  mediaContent(media: string) { return this.#request<ArrayBuffer>('/media/' + id(media) + '/content', 'GET', undefined, {}, 'arraybuffer'); }
  job(job: string) { return this.#request<Job>('/jobs/' + id(job)); }
  cancelJob(job: string) { return this.#request('/jobs/' + id(job) + '/cancel', 'POST'); }
  regrade(submission: string) { return this.#request<{ submission_id: string; job_id: string }>('/submissions/' + id(submission) + '/regrade', 'POST'); }
  quota() { return this.#request<Quota>('/quota'); }
  reports(student: string, type?: ReportType, cursor?: string) {
    return this.#request<Page<LearningReport>>('/reports' + query(cursor) + '&student_id=' + id(student) + (type ? '&type=' + type : ''));
  }
  generateReport(student_id: string, type: ReportType, period_start: string) { return this.#request<LearningReport>('/reports', 'POST', { student_id, type, period_start }); }
  report(report: string) { return this.#request<LearningReport>('/reports/' + id(report)); }
  reportSources(report: string, cursor?: string) { return this.#request<Page<ReportSource>>('/reports/' + id(report) + '/sources' + query(cursor)); }
  rebuildReport(report: string) { return this.#request<LearningReport>('/reports/' + id(report) + '/rebuild', 'POST'); }
  generatePlan(body: ReviewPlanBody, key: string) { return this.#request<ReviewPlan>('/review-plans', 'POST', body, { 'Idempotency-Key': key }); }
  plans(student: string, type?: ReportType, cursor?: string) {
    return this.#request<Page<ReviewPlanSummary>>('/review-plans' + query(cursor) + '&student_id=' + id(student) + (type ? '&type=' + type : ''));
  }
  plan(plan: string) { return this.#request<ReviewPlan>('/review-plans/' + id(plan)); }
  mistakes(student: string, cursor?: string) { return this.#request<Page<Mistake> & { pending_updates: number }>('/students/' + id(student) + '/mistakes' + query(cursor)); }
  mistakeBank(student: string) { return this.#request<{ items: MistakeBankItem[] }>('/students/' + id(student) + '/mistake-bank'); }
  mistakeDetail(student: string, item: string) { return this.#request<MistakeBankDetail>('/students/' + id(student) + '/mistake-bank/' + id(item)); }
  dismissMistake(student: string, item: string) { return this.#request<{ dismissed: boolean }>('/students/' + id(student) + '/mistake-bank/' + id(item) + '/dismiss', 'POST'); }
  mistakeSheets(student: string) { return this.#request<{ items: MistakeSheet[] }>('/students/' + id(student) + '/mistake-sheets'); }
  createMistakeSheet(student: string, body: { item_ids: string[]; due_in_days?: number; due_date?: string }) { return this.#request<MistakeSheet>('/students/' + id(student) + '/mistake-sheets', 'POST', body); }
  mistakeSheetPdf(sheet: string, kind: 'questions' | 'answers') { return this.#request<ArrayBuffer>('/mistake-sheets/' + id(sheet) + '/pdf?kind=' + kind, 'GET', undefined, {}, 'arraybuffer'); }
  tasks(student: string, cursor?: string) { return this.#request<Page<ReviewTask>>('/students/' + id(student) + '/review-tasks' + query(cursor)); }
  task(task: string) { return this.#request<ReviewTask>('/review-tasks/' + id(task)); }
  enroll(mistake: Mistake) { return this.#request<ReviewTask>('/questions/' + id(mistake.question_id) + '/review-tasks', 'POST', { expected_effective_revision: mistake.effective_revision }); }
  draft(task: ReviewTask, student_answer: string) { return this.#request<ReviewTask>('/review-tasks/' + id(task.id) + '/draft', 'POST', { expected_draft_revision: task.draft.revision, student_answer }); }
  attempt(task: ReviewTask, outcome: string, key: string) { return this.#request<ReviewTask>('/review-tasks/' + id(task.id) + '/attempts', 'POST', { expected_draft_revision: task.draft.revision, outcome }, { 'Idempotency-Key': key }); }
  homeworkDay(student: string, date: string) { return this.#request<HomeworkDay>('/students/' + id(student) + '/homework-days/' + id(date)); }
  homeworkDays(student: string, from: string, to: string) { return this.#request<{ items: HomeworkDay[] }>('/students/' + id(student) + '/homework-days?from=' + id(from) + '&to=' + id(to)); }
  homeworkBacklog(student: string) { return this.#request<{ items: DailyItem[] }>('/students/' + id(student) + '/homework-backlog?limit=100'); }
  homeworkItem(item: string) { return this.#request<DailyItem>('/homework-items/' + id(item)); }
  createHomework(student: string, date: string, body: HomeworkCreate) { return this.#request<DailyItem>('/students/' + id(student) + '/homework-days/' + id(date) + '/items', 'POST', body); }
  updateHomework(item: string, body: HomeworkUpdate) { return this.#request<DailyItem>('/homework-items/' + id(item), 'PUT', body); }
  progressHomework(item: string, body: VersionedHomework & { state: WorkState; reason?: string }) { return this.#request<DailyItem>('/homework-items/' + id(item) + '/progress', 'POST', body); }
  verifyHomework(item: string, body: VersionedHomework & { completed: boolean; reason?: string }) { return this.#request<DailyItem>('/homework-items/' + id(item) + '/verify', 'POST', body); }
  homeworkScope(student: string, date: string, body: VersionedHomework & { scope_state: 'complete' | 'incomplete'; subjects: Record<DailySubject, ScopeValue> }) { return this.#request<HomeworkDay>('/students/' + id(student) + '/homework-days/' + id(date) + '/scope', 'PUT', body); }
  createHomeworkImport(body: ImportBody) { return this.#request<HomeworkImport>('/homework-imports', 'POST', body); }
  homeworkImports(student: string, cursor?: string) { return this.#request<Page<HomeworkImportSummary>>('/homework-imports' + query(cursor) + '&student_id=' + id(student)); }
  homeworkImport(record: string) { return this.#request<HomeworkImport>('/homework-imports/' + id(record)); }
  saveHomeworkDraft(record: string, body: VersionedHomework & { draft: HomeworkDraft }) { return this.#request<HomeworkImport>('/homework-imports/' + id(record) + '/draft', 'PUT', body); }
  applyHomeworkImport(record: string, body: VersionedHomework & { day_revisions: Record<string, number> }) { return this.#request<HomeworkImport>('/homework-imports/' + id(record) + '/apply', 'POST', body); }
  retryHomeworkImport(record: string, body: VersionedHomework & { image_ids?: string[]; confirm_unknown_cost?: boolean }) { return this.#request<HomeworkImport>('/homework-imports/' + id(record) + '/retry', 'POST', body); }
  discardHomeworkImport(record: string, body: VersionedHomework) { return this.#request<HomeworkImport>('/homework-imports/' + id(record) + '/discard', 'POST', body); }
}

