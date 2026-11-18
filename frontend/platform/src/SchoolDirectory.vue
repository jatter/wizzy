<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, shallowRef, watch } from 'vue';
import { ElButton, ElDialog, ElTag } from 'element-plus';
import { ChevronLeft, ChevronRight, CircleAlert, Plus, School, X } from 'lucide-vue-next';
import SchoolStructure from './SchoolStructure.vue';
import SchoolRoster from './SchoolRoster.vue';
import { ApiError, type PlatformApi, type Identity, type SchoolEntry, type SchoolCreate, type SchoolRevision, type SchoolFilter } from './api';

const props = defineProps<{ api: PlatformApi; identity: Identity }>();
const emit = defineEmits<{ failure: [error: unknown]; revalidate: [] }>();
const readable = computed(() => props.identity.permissions.includes('platform.schools.read'));
const writable = computed(() => props.identity.permissions.includes('platform.schools.write'));
const items = ref<SchoolEntry[]>([]), search = ref(''), status = ref('');
const applied = reactive<SchoolFilter>({});
const cursors = ref<(string | undefined)[]>([undefined]), pageIndex = ref(0), nextCursor = ref<string | null>(null);
const loading = ref(false), issue = shallowRef<ApiError | null>(null);
const selected = shallowRef<SchoolEntry | null>(null), history = ref<SchoolEntry[]>([]), historyCursor = ref<string | null>(null);
const detailLoading = ref(false), detailIssue = shallowRef<ApiError | null>(null);
const dialog = ref(false), editing = shallowRef<SchoolEntry | null>(null);
const saving = ref(false), conflict = ref(false), uncertain = ref(false), formError = ref('');
const blank = () => ({code:'',name:'',region:'',academic_year:'',status:'active' as 'active' | 'disabled',reason:''});
const form = reactive(blank());
let listVersion = 0, detailVersion = 0, formVersion = 0, alive = true;
let pending: SchoolCreate | SchoolRevision | null = null;
const formatTime = (value: string) => new Date(value).toLocaleString('zh-CN',{hour12:false});
function handle(error: unknown) {
  const value = error instanceof ApiError ? error : new ApiError(502);
  if (value.status === 401 || value.code === 'invalid_session') emit('failure',value);
  else if (value.status === 403) emit('revalidate');
  return value;
}
async function refresh() {
  const version = ++listVersion;
  loading.value=true; issue.value=null;
  try {
    const result = await props.api.schools({...applied,cursor:cursors.value[pageIndex.value]});
    if (!alive || version !== listVersion || !readable.value) return;
    items.value=result.items; nextCursor.value=result.next_cursor;
  } catch (error) {
    if (alive && version === listVersion) { items.value=[]; nextCursor.value=null; issue.value=handle(error); }
  } finally { if (alive && version === listVersion) loading.value=false; }
}
function query(reset = false) {
  if (reset) { search.value=''; status.value=''; }
  applied.q=search.value.trim(); applied.status=status.value === '' ? undefined : status.value as 'active' | 'disabled';
  cursors.value=[undefined]; pageIndex.value=0; void refresh();
}
function turnPage(direction: number) {
  if (loading.value || (direction < 0 && !pageIndex.value) || (direction > 0 && !nextCursor.value)) return;
  if (direction > 0) cursors.value[pageIndex.value+1]=nextCursor.value!;
  pageIndex.value+=direction; void refresh();
}
function closeDetail() {
  detailVersion++; selected.value=null; history.value=[]; historyCursor.value=null;
  detailIssue.value=null; detailLoading.value=false;
}
async function openDetail(id: string) {
  closeDetail(); const version = detailVersion;
  detailLoading.value=true;
  try {
    const [school,revisions] = await Promise.all([props.api.school(id),props.api.schoolHistory(id)]);
    if (!alive || version !== detailVersion || !readable.value) return;
    selected.value=school; history.value=revisions.items; historyCursor.value=revisions.next_cursor;
  } catch (error) { if (alive && version === detailVersion) detailIssue.value=handle(error); }
  finally { if (alive && version === detailVersion) detailLoading.value=false; }
}
async function moreHistory() {
  if (!selected.value || !historyCursor.value || detailLoading.value) return;
  const version = ++detailVersion;
  detailLoading.value=true; detailIssue.value=null;
  try {
    const result = await props.api.schoolHistory(selected.value.id,historyCursor.value);
    if (!alive || version !== detailVersion || !readable.value) return;
    history.value.push(...result.items); historyCursor.value=result.next_cursor;
  } catch (error) { if (alive && version === detailVersion) detailIssue.value=handle(error); }
  finally { if (alive && version === detailVersion) detailLoading.value=false; }
}
function closeForm() {
  formVersion++; dialog.value=false; editing.value=null; saving.value=false;
  pending=null; conflict.value=false; uncertain.value=false; formError.value=''; Object.assign(form,blank());
}
function openForm(school?: SchoolEntry) {
  closeForm(); editing.value=school ?? null;
  if (school) Object.assign(form,{code:school.code,name:school.name,region:school.region,
    academic_year:school.academic_year,status:school.status});
  dialog.value=true;
}
async function reloadRevision() {
  if (!editing.value || saving.value) return;
  const version = formVersion; saving.value=true;
  try {
    const school = await props.api.school(editing.value.id);
    if (!alive || version !== formVersion || !writable.value) return;
    openForm(school);
  } catch (error) {
    if (!alive || version !== formVersion) return;
    const value = handle(error);
    if ([401,403].includes(value.status) || value.code === 'invalid_session') closeForm();
    else formError.value=value.message;
  } finally { if (alive && version === formVersion) saving.value=false; }
}
function validate() {
  if (Object.values(form).some(value => /[\x00-\x1f\x7f]/.test(value))) return '字段不能包含控制字符。';
  if (!/^[A-Z0-9][A-Z0-9_-]{1,39}$/.test(form.code.trim().toUpperCase())) return '学校代码需为 2–40 位字母、数字、下划线或短横线，首位为字母或数字。';
  if (!form.name.trim() || !form.region.trim() || !form.reason.trim()) return '请填写学校名称、所在地区和变更原因。';
  const year = form.academic_year.trim(), first = Number(year.slice(0,4));
  if (!/^[0-9]{4}-[0-9]{4}$/.test(year) || first < 2000 || first > 9998 || Number(year.slice(5)) !== first+1)
    return '学年需为相邻年份，例如 2026-2027（起始年 2000–9998）。';
  return '';
}
async function save() {
  if (saving.value || conflict.value || !writable.value) return;
  if (!pending) {
    formError.value=validate(); if (formError.value) return;
    const metadata = {operation_id:crypto.randomUUID(),name:form.name.trim(),region:form.region.trim(),
      academic_year:form.academic_year.trim(),status:form.status,reason:form.reason.trim()};
    pending=editing.value ? {...metadata,expected_revision:editing.value.revision} : {...metadata,code:form.code.trim().toUpperCase()};
  }
  const version = formVersion, id = editing.value?.id;
  saving.value=true;
  try {
    const result = id ? await props.api.reviseSchool(id,pending as SchoolRevision) : await props.api.createSchool(pending as SchoolCreate);
    if (!alive || version !== formVersion || !writable.value) return;
    closeForm(); void refresh(); void openDetail(result.id);
  } catch (error) {
    if (!alive || version !== formVersion) return;
    const value = handle(error);
    if ([401,403].includes(value.status) || value.code === 'invalid_session') { closeForm(); return; }
    formError.value=value.message; conflict.value=value.status === 409;
    // A missing response can follow a commit; retain the exact operation for retry.
    uncertain.value=value.status === 0 || value.status >= 500;
    if (!uncertain.value) pending=null;
  } finally { if (alive && version === formVersion) saving.value=false; }
}
watch(writable,value => { if (!value) closeForm(); });
watch(readable,value => { if (!value) { listVersion++; items.value=[]; closeDetail(); closeForm(); } });
onMounted(refresh);
onUnmounted(() => { alive=false; listVersion++; closeDetail(); closeForm(); });
defineExpose({refresh});
</script>

<template>
  <div class="model-workspace school-workspace">
    <section class="surface" aria-label="学校目录">
      <div class="card-heading"><div><h2>学校档案</h2><p>统一学校代码 · 学年与状态 · 完整变更历史</p></div><ElButton v-if="writable" type="primary" @click="openForm()"><Plus :size="16" />登记学校</ElButton><ElTag v-else type="info" effect="plain">只读</ElTag></div>
      <form class="model-filters" @submit.prevent="query()">
        <label for="school-search">搜索学校<input id="school-search" v-model="search" maxlength="120" placeholder="学校代码、名称或所在地区" /></label>
        <label for="school-filter-status">学校状态<select id="school-filter-status" v-model="status"><option value="">全部状态</option><option value="active">已启用</option><option value="disabled">已停用</option></select></label>
        <div class="filter-actions"><ElButton native-type="submit" :disabled="loading">查询</ElButton><ElButton :disabled="loading" @click="query(true)">重置筛选</ElButton></div>
      </form>
      <div v-if="issue" class="error-box" role="alert"><CircleAlert :size="18" /><span>{{ issue.message }}</span><ElButton @click="refresh">重试查询</ElButton></div>
      <div class="school-list" :aria-busy="loading">
        <p v-if="loading" class="model-loading" role="status">正在读取学校档案…</p>
        <article v-for="school in items" :key="school.id" class="school-row">
          <div class="model-symbol"><School :size="22" /></div><div class="school-name"><h3>{{ school.name }}</h3><code>{{ school.code }}</code><p>{{ school.region }}</p></div>
          <dl class="school-summary"><div><dt>学年</dt><dd>{{ school.academic_year }}</dd></div><div><dt>当前版本</dt><dd>v{{ school.revision }}</dd></div></dl>
          <ElTag :type="school.status==='active' ? 'success' : 'info'" effect="plain">{{ school.status==='active' ? '已启用' : '已停用' }}</ElTag>
          <ElButton @click="openDetail(school.id)">查看详情</ElButton>
        </article>
        <div v-if="!loading && !issue && !items.length" class="empty-state"><School :size="30" /><h3>暂无匹配学校</h3><p>调整筛选条件，或登记学校档案。</p></div>
      </div>
      <div class="table-footer"><span>第 {{ pageIndex+1 }} 页 · 本页 {{ items.length }} 条 · 每页最多 50 条</span><div><ElButton :disabled="!pageIndex || loading" @click="turnPage(-1)"><ChevronLeft :size="14" />上一页</ElButton><ElButton :disabled="!nextCursor || loading" @click="turnPage(1)">下一页<ChevronRight :size="14" /></ElButton></div></div>
    </section>
    <section v-if="selected || detailLoading || detailIssue" class="surface school-detail" aria-label="学校详情">
      <div class="card-heading"><div><h2>{{ selected?.name ?? '学校详情' }}</h2><p v-if="selected">{{ selected.code }} · v{{ selected.revision }}</p></div><ElButton aria-label="关闭学校详情" text @click="closeDetail"><X :size="18" /></ElButton></div>
      <p v-if="detailLoading" role="status">正在读取学校及变更记录…</p>
      <div v-if="detailIssue" class="error-box" role="alert">{{ detailIssue.message }}</div>
      <template v-if="selected">
        <dl class="details"><div><dt>所在地区</dt><dd>{{ selected.region }}</dd></div><div><dt>学年</dt><dd>{{ selected.academic_year }}</dd></div><div><dt>状态</dt><dd>{{ selected.status==='active' ? '已启用' : '已停用' }}</dd></div><div><dt>登记时间</dt><dd>{{ formatTime(selected.registered_at) }}</dd></div><div><dt>最近更新</dt><dd>{{ formatTime(selected.created_at) }}</dd></div></dl>
        <p class="inline-note">学校状态用于档案管理。访问学生、家庭和作业内容须另行授权。</p>
        <div class="school-detail-actions"><ElButton v-if="writable" type="primary" :disabled="selected.revision>=999999999" @click="openForm(selected)">编辑学校 / 启停</ElButton><ElButton :disabled="detailLoading" @click="openDetail(selected.id)">刷新详情</ElButton></div>
        <SchoolStructure :key="selected.id" :api="api" :identity="identity" :school="selected" @failure="emit('failure',$event)" @revalidate="emit('revalidate')" />
          <SchoolRoster v-if="identity.permissions.includes('platform.students.read')" :key="selected.id" :api="api" :identity="identity" :school="selected" @failure="emit('failure',$event)" @revalidate="emit('revalidate')" />
        <h3 class="school-history-heading">变更历史</h3><ol class="school-history"><li v-for="entry in history" :key="entry.revision_id"><div class="school-history-title"><strong>v{{ entry.revision }} · {{ entry.status==='active' ? '已启用' : '已停用' }}</strong><time :datetime="entry.created_at">{{ formatTime(entry.created_at) }}</time></div><p>{{ entry.name }} · {{ entry.region }} · {{ entry.academic_year }}</p><p>{{ entry.reason }}</p><small>操作人：{{ entry.actor_id }}</small></li></ol>
        <ElButton v-if="historyCursor" :disabled="detailLoading" @click="moreHistory">加载更早记录</ElButton>
      </template>
    </section>
    <ElDialog :model-value="dialog" :title="editing ? '编辑学校' : '登记学校'" width="min(680px, 94vw)" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" @update:model-value="value => { if (!value) closeForm(); }">
      <form class="model-form" @submit.prevent="save" novalidate>
        <p class="form-intro">{{ editing ? '学校代码固定。保存将追加变更记录，保留原档案和操作人。' : '以唯一代码登记学校；学校同名时，请结合地区与代码核对。' }}</p>
        <div v-if="formError" class="error-box" role="alert">{{ formError }}</div>
        <p v-if="uncertain" class="model-warning">保存结果未确认，输入暂时锁定。重试使用原请求；关闭或刷新页面后，请先查询目录及历史再操作。</p>
        <ElButton v-if="conflict && editing" :disabled="saving" @click="reloadRevision">载入最新版本（替换草稿）</ElButton>
        <p v-if="conflict && !editing" class="model-warning">请取消并核对学校代码及已有档案，再重新登记。</p>
        <fieldset :disabled="saving || uncertain || conflict"><legend>学校基本信息</legend><div class="model-form-grid">
          <label for="school-code">学校代码<input id="school-code" v-model="form.code" :disabled="!!editing" maxlength="40" placeholder="例如 SH_PUDONG_001" required /></label>
          <label for="school-name">学校名称<input id="school-name" v-model="form.name" maxlength="120" required /></label>
          <label for="school-region">所在地区<input id="school-region" v-model="form.region" maxlength="120" placeholder="例如 上海市 / 浦东新区" required /></label>
          <label for="school-year">学年<input id="school-year" v-model="form.academic_year" maxlength="9" placeholder="2026-2027" required /></label>
          <label for="school-status">档案状态<select id="school-status" v-model="form.status"><option value="active">已启用</option><option value="disabled">已停用</option></select></label>
        </div><label for="school-reason" class="model-reason">变更原因<textarea id="school-reason" v-model="form.reason" maxlength="500" rows="3" required /></label></fieldset>
        <div class="model-form-footer"><ElButton :disabled="saving" @click="closeForm">取消</ElButton><ElButton type="primary" native-type="submit" :loading="saving" :disabled="saving || conflict">{{ uncertain ? '重试原请求' : editing ? '保存变更' : '保存登记' }}</ElButton></div>
      </form>
    </ElDialog>
  </div>
</template>
