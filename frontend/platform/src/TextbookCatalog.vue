<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, shallowRef, watch } from 'vue';
import { ElButton, ElDialog, ElTag } from 'element-plus';
import TextbookDocuments from './TextbookDocuments.vue';
import { BookOpen, ChevronLeft, ChevronRight, CircleAlert, FileCheck2, Plus, X } from 'lucide-vue-next';
import { ApiError, type PlatformApi, type Identity, type TextbookEntry, type TextbookCreate, type TextbookRevision,
  type TextbookDecision, type TextbookAction, type TextbookFilter, type TextbookState, type TextbookScope } from './api';

const props = defineProps<{ api: PlatformApi; identity: Identity }>();
const emit = defineEmits<{ failure: [error: unknown]; revalidate: [] }>();
const readable = computed(() => props.identity.permissions.includes('platform.textbooks.read'));
const writable = computed(() => props.identity.permissions.includes('platform.textbooks.write'));
const reviewable = computed(() => props.identity.permissions.includes('platform.textbooks.review'));
const items = ref<TextbookEntry[]>([]), search = ref(''), state = ref('');
const applied = reactive<TextbookFilter>({});
const cursors = ref<(string | undefined)[]>([undefined]), pageIndex = ref(0), nextCursor = ref<string | null>(null);
const loading = ref(false), issue = shallowRef<ApiError | null>(null);
const selected = shallowRef<TextbookEntry | null>(null), history = ref<TextbookEntry[]>([]), historyCursor = ref<string | null>(null);
const detailLoading = ref(false), detailIssue = shallowRef<ApiError | null>(null);
type Mode = 'create' | 'revise' | TextbookAction;
const mode = ref<Mode>('create'), dialog = ref(false), target = shallowRef<TextbookEntry | null>(null);
const saving = ref(false), conflict = ref(false), uncertain = ref(false), formError = ref('');
const metadataMode = computed(() => mode.value === 'create' || mode.value === 'revise');
const canSave = computed(() => ['approve','reject'].includes(mode.value) ? reviewable.value : writable.value);
const blank = () => ({code:'',title:'',stage:'primary' as 'primary' | 'junior' | 'senior',grade:1,subject:'',publisher:'',
  edition_label:'',academic_year:'',volume:'first' as 'first' | 'second' | 'full',hasRights:false,
  basis:'publisher_contract' as 'publisher_contract' | 'open_license' | 'public_domain',holder:'',reference:'',
  scopes:['storage'] as TextbookScope[],regions:'CN',valid_from:'',valid_until:'',reason:''});
const form = reactive(blank());
let listVersion = 0, detailVersion = 0, formVersion = 0, alive = true;
let pending: TextbookCreate | TextbookRevision | TextbookDecision | null = null;
const states = {draft:'草稿',pending_review:'待审核',approved:'权利已批准',withdrawn:'已撤回'};
const availability = {metadata_only:'仅元数据',pending_review:'待审核',expired:'授权已到期',scheduled:'授权尚未生效',rights_approved:'权利已批准',withdrawn:'已撤回'};
const actions = {create:'登记教材',revise:'修订教材',submit:'提交审核',approve:'批准权利',reject:'驳回申请',withdraw:'撤回版本'};
const stageName = {primary:'小学',junior:'初中',senior:'高中'}, volumeName = {first:'上册',second:'下册',full:'全册'};
const basisName = {publisher_contract:'出版社合同',open_license:'开放许可',public_domain:'公共领域依据'};
const scopeName = {storage:'文件存储',ocr:'OCR 解析',retrieval:'AI 检索',preview:'正文预览'};
const formatTime = (value: string) => new Date(value).toLocaleString('zh-CN',{hour12:false});
const independent = computed(() => !!selected.value && ![selected.value.creator_id,selected.value.editor_id,selected.value.submitted_by].includes(props.identity.id));
function handle(error: unknown) {
  const value = error instanceof ApiError ? error : new ApiError(502);
  if (value.status === 401 || value.code === 'invalid_session') emit('failure',value);
  else if (value.status === 403) emit('revalidate');
  return value;
}
async function loadList() {
  const version = ++listVersion;
  loading.value=true; issue.value=null;
  try {
    const result = await props.api.textbooks({...applied,cursor:cursors.value[pageIndex.value]});
    if (!alive || version !== listVersion || !readable.value) return;
    items.value=result.items; nextCursor.value=result.next_cursor;
  } catch (error) {
    if (alive && version === listVersion) { items.value=[]; nextCursor.value=null; issue.value=handle(error); }
  } finally { if (alive && version === listVersion) loading.value=false; }
}
function query(reset = false) {
  if (reset) { search.value=''; state.value=''; }
  applied.q=search.value.trim(); applied.state=state.value === '' ? undefined : state.value as TextbookState;
  cursors.value=[undefined]; pageIndex.value=0; void loadList();
}
function turnPage(direction: number) {
  if (loading.value || (direction < 0 && !pageIndex.value) || (direction > 0 && !nextCursor.value)) return;
  if (direction > 0) cursors.value[pageIndex.value+1]=nextCursor.value!;
  pageIndex.value+=direction; void loadList();
}
function closeDetail() {
  detailVersion++; selected.value=null; history.value=[]; historyCursor.value=null;
  detailIssue.value=null; detailLoading.value=false;
}
async function openDetail(id: string) {
  closeDetail(); const version = detailVersion;
  detailLoading.value=true;
  try {
    const [entry,revisions] = await Promise.all([props.api.textbook(id),props.api.textbookHistory(id)]);
    if (!alive || version !== detailVersion || !readable.value) return;
    selected.value=entry; history.value=revisions.items; historyCursor.value=revisions.next_cursor;
  } catch (error) { if (alive && version === detailVersion) detailIssue.value=handle(error); }
  finally { if (alive && version === detailVersion) detailLoading.value=false; }
}
async function moreHistory() {
  if (!selected.value || !historyCursor.value || detailLoading.value) return;
  const version = ++detailVersion;
  detailLoading.value=true; detailIssue.value=null;
  try {
    const result = await props.api.textbookHistory(selected.value.id,historyCursor.value);
    if (!alive || version !== detailVersion || !readable.value) return;
    history.value.push(...result.items); historyCursor.value=result.next_cursor;
  } catch (error) { if (alive && version === detailVersion) detailIssue.value=handle(error); }
  finally { if (alive && version === detailVersion) detailLoading.value=false; }
}
function refresh() { void loadList(); if (selected.value) void openDetail(selected.value.id); }
function closeForm() {
  formVersion++; dialog.value=false; target.value=null; saving.value=false;
  pending=null; conflict.value=false; uncertain.value=false; formError.value=''; Object.assign(form,blank());
}
function openForm(next: Mode, entry?: TextbookEntry) {
  closeForm(); mode.value=next; target.value=entry ?? null;
  if (entry) {
    Object.assign(form,{code:entry.code,title:entry.title,stage:entry.stage,grade:entry.grade,subject:entry.subject,publisher:entry.publisher,
      edition_label:entry.edition_label,academic_year:entry.academic_year,volume:entry.volume,hasRights:entry.rights !== null});
    if (entry.rights) Object.assign(form,{...entry.rights,scopes:[...entry.rights.scopes],regions:entry.rights.regions.join(', ')});
  }
  dialog.value=true;
}
async function reloadRevision() {
  if (!target.value || saving.value) return;
  const version = formVersion;
  saving.value=true;
  try {
    const current = await props.api.textbook(target.value.id);
    if (!alive || version !== formVersion) return;
    if (mode.value === 'revise' && ['draft','withdrawn'].includes(current.state)) openForm('revise',current);
    else { closeForm(); void openDetail(current.id); }
  } catch (error) {
    if (!alive || version !== formVersion) return;
    const value=handle(error);
    if ([401,403].includes(value.status) || value.code === 'invalid_session') closeForm();
    else formError.value=value.message;
  } finally { if (alive && version === formVersion) saving.value=false; }
}
function validate() {
  if (!form.reason.trim()) return '请填写操作原因。';
  if (/[\x00-\x1f\x7f]/.test(form.reason)) return '操作原因不能包含控制字符。';
  if (!metadataMode.value) return '';
  if (Object.values(form).some(v => typeof v === 'string' && /[\x00-\x1f\x7f]/.test(v))) return '字段不能包含控制字符。';
  if (!/^[A-Z0-9][A-Z0-9_-]{1,39}$/.test(form.code.trim().toUpperCase())) return '教材代码需为 2–40 位字母、数字、下划线或短横线。';
  if (![form.title,form.subject,form.publisher,form.edition_label].every(v => v.trim())) return '请补齐教材名称、科目、出版社和版本。';
  const first = Number(form.academic_year.slice(0,4));
  if (!/^[0-9]{4}-[0-9]{4}$/.test(form.academic_year) || first < 2000 || first > 9998 || Number(form.academic_year.slice(5)) !== first+1) return '学年需为相邻年份，例如 2026-2027。';
  const [low,high] = {primary:[1,6],junior:[7,9],senior:[10,12]}[form.stage];
  if (!Number.isInteger(form.grade) || form.grade < low! || form.grade > high!) return '年级与学段不匹配（小学 1–6、初中 7–9、高中 10–12）。';
  if (form.hasRights) {
    const regions = form.regions.split(',').map(v => v.trim());
    if (!form.holder.trim() || !form.reference.trim()) return '请填写权利人和权利凭据引用。';
    if (!regions.length || regions.length > 20 || new Set(regions).size !== regions.length || !regions.every(v => /^[A-Z]{2}(-[A-Z0-9]{1,8})?$/.test(v))) return '授权地区需为不重复的地区代码，使用英文逗号分隔，最多 20 个。';
    if (!form.scopes.includes('storage')) return '授权用途必须包含文件存储。';
    const day = (v: string) => /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(v) && v.slice(0,4) !== '0000' && Number.isFinite(Date.parse(v)) && new Date(v).toISOString().slice(0,10) === v;
    if (!day(form.valid_from) || !day(form.valid_until) || form.valid_from > form.valid_until) return '请填写有效的授权起止日期，起始日期不能晚于截止日期。';
  }
  return '';
}
async function save() {
  if (saving.value || conflict.value || !canSave.value) return;
  if (!pending) {
    formError.value=validate(); if (formError.value) return;
    const base = {operation_id:crypto.randomUUID(),reason:form.reason.trim()};
    if (metadataMode.value) {
      const metadata = {...base,title:form.title.trim(),stage:form.stage,grade:form.grade,subject:form.subject.trim(),publisher:form.publisher.trim(),
        edition_label:form.edition_label.trim(),academic_year:form.academic_year,volume:form.volume,
        rights:form.hasRights ? {basis:form.basis,holder:form.holder.trim(),reference:form.reference.trim(),scopes:[...form.scopes],
          regions:form.regions.split(',').map(v => v.trim()),valid_from:form.valid_from,valid_until:form.valid_until} : null};
      pending=target.value ? {...metadata,expected_revision:target.value.revision} : {...metadata,code:form.code.trim().toUpperCase()};
    } else if (target.value) pending={...base,expected_revision:target.value.revision};
  }
  if (!pending) return;
  const version=formVersion, id=target.value?.id;
  saving.value=true;
  try {
    const result = mode.value === 'create' ? await props.api.createTextbook(pending as TextbookCreate) : mode.value === 'revise' ?
      await props.api.reviseTextbook(id!,pending as TextbookRevision) : await props.api.decideTextbook(id!,mode.value as TextbookAction,pending as TextbookDecision);
    if (!alive || version !== formVersion || !canSave.value) return;
    closeForm(); void loadList(); void openDetail(result.id);
  } catch (error) {
    if (!alive || version !== formVersion) return;
    const value=handle(error);
    if ([401,403].includes(value.status) || value.code === 'invalid_session') { closeForm(); return; }
    formError.value=value.message; conflict.value=value.status === 409;
    uncertain.value=value.status === 0 || value.status >= 500;
    if (!uncertain.value) pending=null;
  } finally { if (alive && version === formVersion) saving.value=false; }
}
watch(canSave,value => { if (!value) closeForm(); });
watch(readable,value => { if (!value) { listVersion++; items.value=[]; closeDetail(); closeForm(); } });
onMounted(loadList);
onUnmounted(() => { alive=false; listVersion++; closeDetail(); closeForm(); });
defineExpose({refresh});
</script>

<template>
  <div class="model-workspace textbook-workspace">
    <section class="surface" aria-label="教材目录">
      <div class="card-heading"><h2>教材与权利登记</h2><ElButton v-if="writable" type="primary" size="small" @click="openForm('create')"><Plus :size="16" />登记教材</ElButton><ElTag v-else type="info" effect="plain">只读</ElTag></div>
      <div class="textbook-content-note"><FileCheck2 :size="22" /><div><strong>权利审核与正文处理分开管理</strong><p>本页登记教材元数据和授权。PDF 支持隔离接收；病毒扫描、OCR 转 TXT、章节抽检及正文发布尚未开放；审核通过后，正文仍不可使用。</p></div></div>
      <form class="model-filters" @submit.prevent="query()">
        <label for="textbook-search">搜索教材<input id="textbook-search" v-model="search" maxlength="120" placeholder="代码、名称、科目或出版社" /></label>
        <label for="textbook-state">审核状态<select id="textbook-state" aria-label="审核状态" v-model="state"><option value="">全部状态</option><option v-for="(label,key) in states" :key="key" :value="key">{{ label }}</option></select></label>
        <div class="filter-actions"><ElButton native-type="submit" :disabled="loading">查询</ElButton><ElButton :disabled="loading" @click="query(true)">重置筛选</ElButton></div>
      </form>
      <div v-if="issue" class="error-box" role="alert"><CircleAlert :size="18" /><span>{{ issue.message }}</span><ElButton @click="loadList">重试查询</ElButton></div>
      <div class="school-list" :aria-busy="loading">
        <p v-if="loading" role="status">正在读取教材目录…</p>
        <p v-else-if="!items.length && !issue" class="empty-state">没有符合条件的教材。可调整筛选条件或登记教材版本。</p>
        <article v-for="entry in items" :key="entry.id" class="school-row">
          <BookOpen :size="22" class="muted" /><div class="school-row-main"><h3>{{ entry.title }} <ElTag effect="plain">{{ states[entry.state] }}</ElTag></h3><p>{{ entry.code }} · {{ stageName[entry.stage] }} {{ entry.grade }} 年级 · {{ entry.subject }} · {{ volumeName[entry.volume] }}</p><small>{{ entry.publisher }} · {{ entry.edition_label }} · {{ entry.academic_year }} · v{{ entry.revision }}</small><p class="inline-note">{{ availability[entry.availability] }} · 正文不可用</p></div>
          <ElButton :aria-label="`查看教材 ${entry.code}`" @click="openDetail(entry.id)">查看详情</ElButton>
        </article>
      </div>
      <div class="pager"><span>第 {{ pageIndex+1 }} 页</span><ElButton :disabled="loading || !pageIndex" @click="turnPage(-1)"><ChevronLeft :size="16" />上一页</ElButton><ElButton :disabled="loading || !nextCursor" @click="turnPage(1)">下一页<ChevronRight :size="16" /></ElButton></div>
    </section>
    <section v-if="selected || detailLoading || detailIssue" class="surface school-detail" aria-label="教材详情">
      <div class="card-heading"><div><h2>{{ selected?.title ?? '教材详情' }}</h2><p v-if="selected">{{ selected.code }} · v{{ selected.revision }}</p></div><ElButton aria-label="关闭教材详情" text @click="closeDetail"><X :size="18" /></ElButton></div>
      <p v-if="detailLoading" role="status">正在读取教材及审核历史…</p><div v-if="detailIssue" class="error-box" role="alert">{{ detailIssue.message }}</div>
      <template v-if="selected">
        <dl class="details"><div><dt>学段 / 年级</dt><dd>{{ stageName[selected.stage] }} / {{ selected.grade }} 年级</dd></div><div><dt>科目 / 册次</dt><dd>{{ selected.subject }} / {{ volumeName[selected.volume] }}</dd></div><div><dt>出版社</dt><dd>{{ selected.publisher }}</dd></div><div><dt>版本 / 学年</dt><dd>{{ selected.edition_label }} / {{ selected.academic_year }}</dd></div><div><dt>审核状态</dt><dd>{{ states[selected.state] }}</dd></div><div><dt>授权可用状态</dt><dd>{{ availability[selected.availability] }}</dd></div><div><dt>登记时间</dt><dd>{{ formatTime(selected.registered_at) }}</dd></div><div><dt>最近更新</dt><dd>{{ formatTime(selected.created_at) }}</dd></div></dl>
        <section class="textbook-rights" aria-label="权利登记"><h3>权利依据与范围</h3><dl v-if="selected.rights" class="details"><div><dt>权利来源</dt><dd>{{ basisName[selected.rights.basis] }}</dd></div><div><dt>权利人</dt><dd>{{ selected.rights.holder }}</dd></div><div><dt>凭据引用</dt><dd>{{ selected.rights.reference }}</dd></div><div><dt>授权用途</dt><dd>{{ selected.rights.scopes.map(v => scopeName[v]).join('、') }}</dd></div><div><dt>授权地区</dt><dd>{{ selected.rights.regions.join('、') }}</dd></div><div><dt>授权期限（上海日期）</dt><dd>{{ selected.rights.valid_from }} 至 {{ selected.rights.valid_until }}</dd></div></dl><p v-else class="inline-note">尚未登记权利。草稿可保存，提交审核前须补齐有效授权。</p></section>
        <div class="school-detail-actions">
          <template v-if="selected.revision < 999999999">
            <ElButton v-if="writable && ['draft','withdrawn'].includes(selected.state)" type="primary" @click="openForm('revise',selected)">修订教材 / 权利</ElButton>
            <ElButton v-if="writable && selected.state==='draft'" :disabled="!selected.rights" @click="openForm('submit',selected)">提交审核</ElButton>
            <ElButton v-if="reviewable && selected.state==='pending_review'" type="primary" :disabled="!independent" @click="openForm('approve',selected)">批准权利</ElButton>
            <ElButton v-if="reviewable && selected.state==='pending_review'" :disabled="!independent" @click="openForm('reject',selected)">驳回申请</ElButton>
            <ElButton v-if="writable && ['pending_review','approved'].includes(selected.state)" @click="openForm('withdraw',selected)">撤回版本</ElButton>
          </template><ElButton :disabled="detailLoading" @click="openDetail(selected.id)">刷新详情</ElButton>
        </div>
        <p v-if="reviewable && selected.state==='pending_review' && !independent" class="model-warning">创建者、当前编辑者和本次提交人不能审核此版本，请由另一位有审核权限的人员处理。</p>
        <p v-if="selected.state==='approved'" class="inline-note">修改已批准档案需先撤回，再修订并重新审核。权利批准不代表正文已发布。</p>
        <TextbookDocuments :key="`${selected.id}:${selected.revision}`" :api="api" :identity="identity" :edition="selected" @failure="error => emit('failure',error)" />
        <h3 class="school-history-heading">修订与审核历史</h3><ol class="school-history"><li v-for="entry in history" :key="entry.revision_id"><div class="school-history-title"><strong>v{{ entry.revision }} · {{ actions[entry.action] }} · {{ states[entry.state] }}</strong><time :datetime="entry.created_at">{{ formatTime(entry.created_at) }}</time></div><p>{{ entry.reason }}</p><small>操作人：{{ entry.actor_id }}</small><details class="textbook-snapshot"><summary>查看本次版本快照</summary><p>{{ entry.title }} · {{ stageName[entry.stage] }} {{ entry.grade }} 年级 · {{ entry.subject }} · {{ volumeName[entry.volume] }}</p><p>{{ entry.publisher }} · {{ entry.edition_label }} · {{ entry.academic_year }}</p><p v-if="entry.rights">{{ basisName[entry.rights.basis] }} · {{ entry.rights.holder }} · {{ entry.rights.reference }}<br>{{ entry.rights.valid_from }} 至 {{ entry.rights.valid_until }} · {{ entry.rights.regions.join('、') }}<br>{{ entry.rights.scopes.map(v => scopeName[v]).join('、') }}</p><p v-else>未登记权利</p><small>创建者：{{ entry.creator_id }}<br>元数据编辑者：{{ entry.editor_id }}<br>本次提交人：{{ entry.submitted_by ?? '尚未提交' }}</small></details></li></ol>
        <ElButton v-if="historyCursor" :disabled="detailLoading" @click="moreHistory">加载更早记录</ElButton>
      </template>
    </section>
    <ElDialog :model-value="dialog" :title="actions[mode]" width="min(760px, 94vw)" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" @update:model-value="value => { if (!value) closeForm(); }">
      <form class="model-form" @submit.prevent="save" novalidate>
        <p class="form-intro">{{ metadataMode ? '教材代码固定，同名教材请按版本区分。权利变更需重新审核，草稿可暂不登记授权。' : `正在对 ${target?.code} 的 v${target?.revision} 执行「${actions[mode]}」。请填写可追溯的操作原因。` }}</p>
        <p v-if="mode==='approve'" class="model-warning">请核对权利来源、期限、地区与用途。批准仅确认权利登记，正文处理仍未开放。</p>
        <div v-if="formError" class="error-box" role="alert">{{ formError }}</div>
        <p v-if="uncertain" class="model-warning">提交结果未确认，输入暂时锁定。重试使用同一请求；关闭或刷新页面后，请先核对目录与历史再操作。</p>
        <ElButton v-if="conflict && target" :disabled="saving" @click="reloadRevision">载入最新版本（替换草稿或返回详情）</ElButton>
        <p v-if="conflict && !target" class="model-warning">请取消并核对教材代码及已有档案，再重新登记。</p>
        <fieldset v-if="metadataMode" :disabled="saving || uncertain || conflict"><legend>教材版本信息</legend><div class="model-form-grid">
          <label for="textbook-code">教材代码<input id="textbook-code" v-model="form.code" :disabled="!!target" maxlength="40" placeholder="例如 MATH_G3_2026_A" required /></label>
          <label for="textbook-title">教材名称<input id="textbook-title" v-model="form.title" maxlength="120" required /></label>
          <label for="textbook-stage">学段<select id="textbook-stage" aria-label="学段" v-model="form.stage"><option value="primary">小学</option><option value="junior">初中</option><option value="senior">高中</option></select></label>
          <label for="textbook-grade">年级<input id="textbook-grade" aria-label="年级" aria-describedby="textbook-grade-help" v-model.number="form.grade" type="number" min="1" max="12" required /><small id="textbook-grade-help">初中填写 7–9，高中填写 10–12。</small></label>
          <label for="textbook-subject">科目<input id="textbook-subject" v-model="form.subject" maxlength="120" required /></label>
          <label for="textbook-publisher">出版社<input id="textbook-publisher" v-model="form.publisher" maxlength="120" required /></label>
          <label for="textbook-edition">版本标识<input id="textbook-edition" v-model="form.edition_label" maxlength="120" placeholder="例如 2026 修订版" required /></label>
          <label for="textbook-year">学年<input id="textbook-year" v-model="form.academic_year" maxlength="9" placeholder="2026-2027" required /></label>
          <label for="textbook-volume">册次<select id="textbook-volume" v-model="form.volume"><option value="first">上册</option><option value="second">下册</option><option value="full">全册</option></select></label>
        </div></fieldset>
        <fieldset v-if="metadataMode" :disabled="saving || uncertain || conflict"><legend>权利登记</legend><label class="textbook-check"><input v-model="form.hasRights" type="checkbox" />已具备权利依据</label>
          <div v-if="form.hasRights" class="model-form-grid textbook-rights-fields">
            <label for="textbook-basis">权利来源<select id="textbook-basis" aria-label="权利来源" v-model="form.basis"><option v-for="(label,key) in basisName" :key="key" :value="key">{{ label }}</option></select></label>
            <label for="textbook-holder">权利人<input id="textbook-holder" v-model="form.holder" maxlength="200" required /></label>
            <label for="textbook-reference">权利凭据引用<input id="textbook-reference" v-model="form.reference" maxlength="200" placeholder="合同编号或受控凭据标识" required /></label>
            <label for="textbook-regions">授权地区代码<input id="textbook-regions" aria-label="授权地区代码" aria-describedby="textbook-regions-help" v-model="form.regions" maxlength="280" placeholder="CN, CN-33" required /><small id="textbook-regions-help">英文逗号分隔，最多 20 个。</small></label>
            <label for="textbook-from">授权起始日期<input id="textbook-from" v-model="form.valid_from" type="date" min="0001-01-01" max="9999-12-31" required /></label>
            <label for="textbook-until">授权截止日期<input id="textbook-until" v-model="form.valid_until" type="date" min="0001-01-01" max="9999-12-31" required /></label>
            <div class="textbook-scopes"><span>授权用途</span><label v-for="(label,key) in scopeName" :key="key" class="textbook-check"><input v-model="form.scopes" type="checkbox" :value="key" />{{ label }}</label><small>必须包含文件存储；各用途将在后续正文处理环节分别校验。</small></div>
          </div><p v-else class="inline-note">可保存仅元数据草稿，补齐授权后才能提交审核。</p>
        </fieldset>
        <fieldset :disabled="saving || uncertain || conflict"><legend>操作说明</legend><label for="textbook-reason" class="model-reason">操作原因<textarea id="textbook-reason" v-model="form.reason" maxlength="500" rows="3" required /></label></fieldset>
        <div class="model-form-footer"><ElButton :disabled="saving" @click="closeForm">取消</ElButton><ElButton type="primary" native-type="submit" :loading="saving" :disabled="saving || conflict || !canSave">{{ uncertain ? '重试原请求' : metadataMode ? '保存草稿' : actions[mode] }}</ElButton></div>
      </form>
    </ElDialog>
  </div>
</template>
