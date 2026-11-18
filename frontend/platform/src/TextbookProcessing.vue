<script setup lang="ts">
import TextbookContent from './TextbookContent.vue';
import { computed, onUnmounted, ref, shallowRef, watch } from 'vue';
import { ElButton, ElTag } from 'element-plus';
import { FileSearch, RefreshCw } from 'lucide-vue-next';
import { ApiError, type Identity, type PlatformApi, type TextbookEntry, type TextbookDocument, type TextbookDecision, type TextbookProcessingJob, type TextbookProcessingPage } from './api';

const props = defineProps<{api: PlatformApi; identity: Identity; edition: TextbookEntry; document: TextbookDocument}>();
const emit = defineEmits<{failure: [error: unknown]}>();
const open = ref(false), loading = ref(false), sending = ref(false), reading = ref(false), uncertain = ref(false), denied = ref(false);
const jobs = shallowRef<TextbookProcessingJob[]>([]), selected = shallowRef<TextbookProcessingJob | null>(null), draft = shallowRef<TextbookProcessingPage | null>(null);
const cursor = ref<string | null>(null), reason = ref(''), issue = ref(''), pageIssue = ref(''), notice = ref('');
let alive = true, generation = 0, pageGeneration = 0, pending: TextbookDecision | null = null;
const eligible = computed(() => !denied.value && props.identity.permissions.includes('platform.textbooks.write') &&
  props.edition.availability === 'rights_approved' && ['storage','ocr'].every(scope => props.edition.rights?.scopes.includes(scope as 'storage' | 'ocr')) &&
  props.document.edition_revision === props.edition.revision);
const active = computed(() => jobs.value.some(job => ['queued','running'].includes(job.state)));
const preview = computed(() => !denied.value && props.edition.availability === 'rights_approved' && props.edition.rights?.scopes.includes('preview') && selected.value?.edition_revision === props.edition.revision);
const labels = {queued:'等待处理',running:'正在处理',draft_ready:'草稿待校核',no_text:'未提取到文本',rejected:'处理被拒绝',unavailable:'处理暂不可用',blocked:'任务已阻止'};
const codes: Record<string,string> = {source_mismatch:'原文件摘要不匹配',scanner_rejected:'安全扫描未通过',source_unavailable:'隔离原文件不可读取',scanner_unavailable:'扫描服务不可用',storage_unavailable:'结果存储不可用',extraction_failed:'文本提取失败',extraction_timeout:'文本提取超时',extraction_unavailable:'提取进程不可用',encrypted_pdf:'加密 PDF 不支持提取',page_limit_exceeded:'页数超出上限',unsupported_pdf_features:'PDF 含不支持的特性',extraction_limit_exceeded:'文本量超出上限',invalid_pdf:'PDF 格式无效',authorization_changed:'申请人会话、教材版本或授权已变化',attempts_exhausted:'任务恢复次数已用尽'};
const time = (value: string) => new Date(value).toLocaleString('zh-CN');
function message(error: unknown) {
  if (!(error instanceof ApiError)) return '连接中断，请重试。';
  return error.status === 401 ? '会话已失效，请重新登录。' : error.status === 403 ? '当前账号没有权限，已清除草稿。' :
    error.code === 'draft_rights_unavailable' ? '当前教材版本或授权不允许预览，请刷新教材。' :
    error.status === 409 ? '任务或教材版本已变化，请刷新记录与教材后重试。' : error.status === 502 ? '服务返回的数据不符合约定，请重试。' : '请求失败，请重试。';
}
function failure(error: unknown) {
  if (error instanceof ApiError && [401,403].includes(error.status)) {
    denied.value = true; generation++; pageGeneration++; jobs.value = []; selected.value = null; draft.value = null; cursor.value = null;
    loading.value = false; reading.value = false; sending.value = false; uncertain.value = false; pending = null;
    emit('failure',error);
  }
}
function reset() {
  generation++; pageGeneration++; jobs.value = []; selected.value = null; draft.value = null; cursor.value = null;
  issue.value = ''; pageIssue.value = ''; notice.value = ''; reason.value = ''; loading.value = false; reading.value = false;
  sending.value = false; uncertain.value = false; pending = null; denied.value = false; open.value = false;
}
watch(() => [props.edition.id,props.edition.revision,props.edition.availability,props.edition.rights?.scopes.join(','),props.document.id,props.identity.id,props.identity.session_id,props.identity.permissions.join(',')],reset);
onUnmounted(() => { alive = false; generation++; pageGeneration++; });
async function load(more = false) {
  if (loading.value || denied.value) return;
  const version = ++generation; loading.value = true; issue.value = ''; pageIssue.value = ''; draft.value = null; selected.value = null; reading.value = false; pageGeneration++;
  try {
    const result = await props.api.textbookProcessingJobs(props.edition.id,props.document.id,more ? cursor.value ?? undefined : undefined);
    if (!alive || version !== generation) return;
    jobs.value = more ? [...jobs.value,...result.items.filter(item => !jobs.value.some(old => old.id === item.id))] : result.items;
    cursor.value = result.next_cursor;
  } catch (error) { if (alive && version === generation) { issue.value = message(error); failure(error); } }
  finally { if (alive && version === generation) loading.value = false; }
}
async function expand() { open.value = true; await load(); }
async function submit() {
  if (sending.value || !eligible.value || active.value) return;
  if (!pending) {
    const text = reason.value.trim();
    if (!text || text.length > 500 || /[\x00-\x1f\x7f]/.test(text)) { issue.value = '请填写 1–500 字的处理原因，不能包含控制字符。'; return; }
    pending = {operation_id:crypto.randomUUID(),expected_revision:props.edition.revision,reason:text};
  }
  const version = ++generation; sending.value = true; issue.value = ''; notice.value = ''; draft.value = null; selected.value = null; reading.value = false; pageGeneration++;
  try {
    const job = await props.api.requestTextbookProcessing(props.edition.id,props.document.id,pending);
    if (!alive || version !== generation) return;
    jobs.value = [job,...jobs.value.filter(item => item.id !== job.id)]; pending = null; uncertain.value = false; reason.value = '';
    notice.value = '处理申请已登记。请刷新任务查看进度；草稿仍需人工校核。';
  } catch (error) {
    if (!alive || version !== generation) return;
    issue.value = message(error); uncertain.value = !(error instanceof ApiError) || ![401,403,404,409,422].includes(error.status);
    if (!uncertain.value) pending = null;
    failure(error);
  } finally { if (alive && version === generation) sending.value = false; }
}
async function select(job: TextbookProcessingJob) {
  const version = ++pageGeneration; reading.value = true; draft.value = null; selected.value = null; pageIssue.value = '';
  try {
    const result = await props.api.textbookProcessingJob(props.edition.id,props.document.id,job.id);
    if (alive && version === pageGeneration) selected.value = result;
  } catch (error) { if (alive && version === pageGeneration) { pageIssue.value = message(error); failure(error); } }
  finally { if (alive && version === pageGeneration) reading.value = false; }
}
async function read(number: number) {
  const job = selected.value;
  if (!job || !preview.value || reading.value) return;
  const version = ++pageGeneration; reading.value = true; draft.value = null; pageIssue.value = '';
  try {
    const result = await props.api.textbookProcessingPage(props.edition.id,props.document.id,job.id,number);
    if (!alive || version !== pageGeneration) return;
    if (result.page_count !== job.page_count) throw new ApiError(502);
    draft.value = result;
  } catch (error) { if (alive && version === pageGeneration) { pageIssue.value = message(error); failure(error); } }
  finally { if (alive && version === pageGeneration) reading.value = false; }
}
</script>

<template>
  <section class="processing-panel" :aria-label="`${document.filename} 的处理任务`">
    <ElButton v-if="!open" @click="expand"><FileSearch :size="16" aria-hidden="true" />查看处理任务</ElButton>
    <template v-else>
      <div class="processing-heading"><h5>扫描与文本提取</h5><ElButton :disabled="loading || sending || denied" @click="load()"><RefreshCw :size="14" aria-hidden="true" />刷新任务</ElButton></div>
      <p class="processing-note">提取 PDF 内嵌文本，不执行 OCR。结果为待校核草稿，尚未发布，不可供 AI 使用。</p>
      <form v-if="eligible" @submit.prevent="submit">
        <label :for="`processing-reason-${document.id}`">处理原因<input :id="`processing-reason-${document.id}`" v-model="reason" maxlength="500" :disabled="sending || uncertain" placeholder="例如：扫描授权原件并生成逐页文本草稿" /></label>
        <p v-if="uncertain" class="processing-note">申请结果未确认，原因已锁定；重试将复用原操作键。</p>
        <ElButton type="primary" native-type="submit" :loading="sending" :disabled="sending || loading || active">{{ uncertain ? '重试原处理申请' : '申请扫描与文本提取' }}</ElButton>
        <p v-if="active" class="processing-note">此文件已有活动任务，请刷新查看进度。</p>
      </form>
      <p v-else class="processing-note">申请处理需要教材写权限、匹配的文件版本，以及当前有效的存储和解析授权。</p>
      <p v-if="issue" class="error-box" role="alert">{{ issue }}</p><p v-if="notice" role="status">{{ notice }}</p>
      <p v-if="loading" role="status">正在读取任务…</p><p v-else-if="!jobs.length && !issue" class="processing-note">暂无处理任务。</p>
      <ol class="processing-jobs" aria-label="处理历史">
        <li v-for="job in jobs" :key="job.id">
          <div class="processing-heading"><ElTag :type="job.state === 'draft_ready' ? 'success' : ['queued','running'].includes(job.state) ? 'info' : 'warning'">{{ labels[job.state] }}</ElTag><span>{{ time(job.created_at) }}</span><ElButton :disabled="reading || loading" @click="select(job)">查看任务详情</ElButton></div>
          <p>{{ job.reason }} · 版本 v{{ job.edition_revision }} · 已领取 {{ job.attempts }} 次</p><p v-if="job.code">{{ codes[job.code] }}</p>
        </li>
      </ol>
      <ElButton v-if="cursor" :disabled="loading || sending" @click="load(true)">加载更早任务</ElButton>
      <p v-if="pageIssue" class="error-box" role="alert">{{ pageIssue }}</p>
      <div v-if="selected" class="processing-detail" aria-label="处理任务详情">
        <h5>{{ labels[selected.state] }}</h5><dl><dt>任务编号</dt><dd>{{ selected.id }}</dd><dt>申请人</dt><dd>{{ selected.created_by }}</dd><dt>完成时间</dt><dd>{{ selected.finished_at ? time(selected.finished_at) : '尚未完成' }}</dd></dl>
        <p v-if="selected.code">{{ codes[selected.code] }}</p>
        <details v-if="selected.scan"><summary>查看本次扫描证据</summary><dl><dt>扫描引擎</dt><dd>{{ selected.scan.engine }}</dd><dt>病毒库版本</dt><dd>{{ selected.scan.database_revision }}</dd><dt>病毒库日期</dt><dd>{{ selected.scan.database_date }}</dd><dt>扫描时间</dt><dd>{{ time(selected.scan.checked_at) }}</dd></dl></details>
        <template v-if="selected.page_count !== null">
          <p>共 {{ selected.page_count }} 页 · {{ selected.character_count }} 字符 · {{ selected.pages_without_text?.length }} 页未提取到文本</p>
          <p v-if="selected.state === 'no_text'" class="processing-note">可能是扫描件；本次未执行 OCR。</p>
          <p v-if="!preview" class="processing-note">预览需要当前教材版本匹配且具有有效的预览授权。</p>
          <div v-else class="page-controls">
            <label :for="`draft-page-${document.id}`">草稿页码<select aria-label="草稿页码" :id="`draft-page-${document.id}`" :value="draft?.page.number ?? ''" :disabled="reading" @change="read(Number(($event.target as HTMLSelectElement).value))"><option disabled value="">选择页面</option><option v-for="n in selected.page_count" :key="n" :value="n">第 {{ n }} 页{{ selected.pages_without_text?.includes(n) ? '（无文本）' : '' }}</option></select></label>
            <ElButton :disabled="reading" @click="read(draft?.page.number ?? 1)">{{ draft ? '重新读取此页' : '读取第一页' }}</ElButton>
          </div>
          <p v-if="reading" role="status">正在读取草稿页面…</p>
          <div v-if="draft" class="draft-page" aria-label="逐页文本草稿"><strong>第 {{ draft.page.number }} / {{ draft.page_count }} 页 · 待校核</strong><pre v-if="draft.page.text">{{ draft.page.text }}</pre><p v-else>此页未提取到文本。</p></div>
        </template>
        <TextbookContent v-if="selected.state === 'draft_ready'" :key="selected.id" :api="api" :identity="identity" :edition="edition" :job="selected" @failure="emit('failure',$event)" />
      </div>
    </template>
  </section>
</template>

<style scoped>
.processing-panel{margin-top:16px;padding:16px;border:1px solid var(--el-border-color-light);border-radius:8px;background:var(--el-fill-color-extra-light)}.processing-heading{display:flex;align-items:center;gap:12px;flex-wrap:wrap;justify-content:space-between}.processing-heading h5,.processing-detail h5{font-size:14px;margin:0}.processing-heading span,.processing-note,.processing-jobs p{font-size:13px;color:var(--el-text-color-secondary);line-height:1.7}.processing-panel svg{margin-right:6px}.processing-note{margin:10px 0}.processing-panel label{display:grid;gap:8px;font-size:13px;margin:12px 0}.processing-panel input,.processing-panel select{box-sizing:border-box;max-width:100%;width:100%;border:1px solid var(--el-border-color);border-radius:6px;background:var(--el-bg-color);color:inherit;padding:9px 12px}.processing-panel input:focus-visible,.processing-panel select:focus-visible{outline:2px solid var(--el-color-primary);outline-offset:2px}.processing-jobs{list-style:none;padding:0;margin:16px 0}.processing-jobs li{padding:12px 0;border-bottom:1px solid var(--el-border-color-light)}.processing-detail{border:1px solid var(--el-border-color-light);padding:16px;border-radius:8px}.processing-detail dl{display:grid;grid-template-columns:80px minmax(0,1fr);gap:8px;font-size:13px}.processing-detail dd{margin:0;overflow-wrap:anywhere}.processing-detail summary{cursor:pointer;font-size:13px}.page-controls{display:flex;align-items:end;gap:12px;flex-wrap:wrap}.page-controls label{margin-bottom:0}.draft-page{margin-top:16px;border-top:1px solid var(--el-border-color-light);padding-top:16px;font-size:13px}.draft-page pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;line-height:1.8;max-height:420px;overflow:auto}.processing-panel form>.el-button{max-width:100%;white-space:normal;height:auto;min-height:32px}
</style>
