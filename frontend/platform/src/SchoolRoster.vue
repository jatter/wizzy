<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, shallowRef, watch } from 'vue';
import { ElButton, ElDialog, ElTag } from 'element-plus';
import { Plus, Users } from 'lucide-vue-next';
import { ApiError, type EnrollmentCreate, type EnrollmentRevision, type EnrollmentStatus, type Identity, type PlatformApi, type RosterEntry, type RosterFilter, type SchoolEntry, type StructureEntry, type StructureTree } from './api';
const props = defineProps<{api: PlatformApi; identity: Identity; school: SchoolEntry}>();
const emit = defineEmits<{failure: [error: ApiError]; revalidate: []}>();
const readable = computed(() => props.identity.permissions.includes('platform.students.read'));
const writable = computed(() => props.identity.permissions.includes('platform.students.write'));
const year = ref(props.school.academic_year), query = ref(''), status = ref<EnrollmentStatus | ''>('');
const applied = shallowRef<RosterFilter>({academic_year: year.value});
const entries = ref<RosterEntry[]>([]), next = ref<string | null>(null), tree = shallowRef<StructureTree | null>(null);
const loading = ref(false), issue = ref('');
const selected = shallowRef<RosterEntry | null>(null), history = ref<RosterEntry[]>([]), historyNext = ref<string | null>(null);
const detailLoading = ref(false), detailIssue = ref('');
const dialog = ref(false), editing = shallowRef<RosterEntry | null>(null), saving = ref(false);
const conflict = ref(false), uncertain = ref(false), formError = ref('');
const blank = () => ({code:'', display_name:'', class_id:'', status:'enrolled' as EnrollmentStatus, reason:''});
const form = reactive(blank());
const statuses: Record<EnrollmentStatus,string> = {enrolled:'在读',paused:'休学',left:'离校',graduated:'毕业'};
const nodes = computed(() => new Map((tree.value?.items ?? []).map(item => [item.id,item])));
const classes = computed(() => (tree.value?.items ?? []).filter(item => item.kind === 'class').sort((a,b) => a.code.localeCompare(b.code)));
const disabledSchool = computed(() => props.school.status === 'disabled' || tree.value?.school_status === 'disabled');
const canWrite = computed(() => writable.value && !!tree.value && !loading.value && !disabledSchool.value);
let alive = true, listVersion = 0, detailVersion = 0, formVersion = 0;
let pending: EnrollmentCreate | EnrollmentRevision | null = null;
const formatTime = (value: string) => new Date(value).toLocaleString('zh-CN',{hour12:false});
function effective(node: StructureEntry) {
  if (disabledSchool.value) return false;
  let ancestor: StructureEntry | undefined = node;
  while (ancestor) { if (ancestor.status !== 'active') return false; ancestor = ancestor.parent_id ? nodes.value.get(ancestor.parent_id) : undefined; }
  return true;
}
function classLabel(id: string | null) {
  if (!id) return '未分班';
  const node = nodes.value.get(id);
  if (!node) return id;
  const parent = node.parent_id ? nodes.value.get(node.parent_id) : undefined;
  return `${parent?.name ?? ''} · ${node.name}（${node.code}）${effective(node) ? '' : ' · 已停用'}`;
}
function handle(error: unknown) {
  const value = error instanceof ApiError ? error : new ApiError(502);
  if (value.status === 401 || value.code === 'invalid_session') emit('failure',value);
  else if (value.status === 403) emit('revalidate');
  return value;
}
function clearSelection() { detailVersion++; selected.value=null; history.value=[]; historyNext.value=null; detailIssue.value=''; detailLoading.value=false; }
async function refresh() {
  const version = ++listVersion;
  clearSelection(); entries.value=[]; next.value=null; tree.value=null; issue.value='';
  const value=year.value.trim(), start=Number(value.slice(0,4));
  if (!/^[0-9]{4}-[0-9]{4}$/.test(value) || start<2000 || start>9998 || Number(value.slice(5))!==start+1) {
    loading.value=false; issue.value='请输入相邻学年，例如 2026-2027。'; return;
  }
  applied.value={academic_year:value,q:query.value.trim() || undefined,status:status.value || undefined}; loading.value=true;
  try {
    const [page, structure] = await Promise.all([props.api.schoolRoster(props.school.id,applied.value), props.api.schoolStructure(props.school.id,value)]);
    if (!alive || version!==listVersion || !readable.value) return;
    entries.value=page.items; next.value=page.next_cursor; tree.value=structure;
  } catch (error) { if (alive && version===listVersion && readable.value) issue.value=handle(error).message; }
  finally { if (alive && version===listVersion) loading.value=false; }
}
async function moreEntries() {
  if (!next.value || loading.value) return;
  const version=++listVersion; loading.value=true; issue.value='';
  try {
    const page=await props.api.schoolRoster(props.school.id,{...applied.value,cursor:next.value});
    if (!alive || version!==listVersion || !readable.value) return;
    entries.value=[...new Map([...entries.value,...page.items].map(item => [item.id,item])).values()]; next.value=page.next_cursor;
  } catch (error) { if (alive && version===listVersion && readable.value) issue.value=handle(error).message; }
  finally { if (alive && version===listVersion) loading.value=false; }
}
async function selectEntry(id: string) {
  clearSelection(); const version=detailVersion; detailLoading.value=true;
  try {
    const [entry,revisions]=await Promise.all([props.api.rosterEnrollment(props.school.id,id), props.api.rosterHistory(props.school.id,id)]);
    if (!alive || version!==detailVersion || !readable.value) return;
    if (entry.academic_year!==applied.value.academic_year || revisions.items.some(item => item.student_id!==entry.student_id || item.academic_year!==entry.academic_year)) throw new ApiError(502);
    selected.value=entry; history.value=revisions.items; historyNext.value=revisions.next_cursor;
  } catch (error) { if (alive && version===detailVersion && readable.value) detailIssue.value=handle(error).message; }
  finally { if (alive && version===detailVersion) detailLoading.value=false; }
}
async function moreHistory() {
  if (!selected.value || !historyNext.value || detailLoading.value) return;
  const version=++detailVersion, entry=selected.value; detailLoading.value=true; detailIssue.value='';
  try {
    const page=await props.api.rosterHistory(props.school.id,entry.id,historyNext.value);
    if (!alive || version!==detailVersion || !readable.value) return;
    if (page.items.some(item => item.student_id!==entry.student_id || item.academic_year!==entry.academic_year)) throw new ApiError(502);
    history.value.push(...page.items); historyNext.value=page.next_cursor;
  } catch (error) { if (alive && version===detailVersion && readable.value) detailIssue.value=handle(error).message; }
  finally { if (alive && version===detailVersion) detailLoading.value=false; }
}
function closeForm() {
  formVersion++; dialog.value=false; editing.value=null; pending=null; saving.value=false;
  conflict.value=false; uncertain.value=false; formError.value=''; Object.assign(form,blank());
}
function openForm(entry?: RosterEntry) {
  if (!canWrite.value) return;
  closeForm(); editing.value=entry ?? null;
  if (entry) Object.assign(form,{code:entry.code,display_name:entry.display_name,class_id:entry.class_id ?? '',status:entry.status});
  dialog.value=true;
}
async function reloadRevision() {
  if (!editing.value || saving.value) return;
  const version=++formVersion; saving.value=true;
  try {
    const entry=await props.api.rosterEnrollment(props.school.id,editing.value.id);
    if (!alive || version!==formVersion || !writable.value) return;
    if (entry.academic_year!==applied.value.academic_year) throw new ApiError(502);
    editing.value=entry; Object.assign(form,{code:entry.code,display_name:entry.display_name,class_id:entry.class_id ?? '',status:entry.status,reason:''});
    conflict.value=false; pending=null; formError.value='';
  } catch (error) { if (alive && version===formVersion) formError.value=handle(error).message; }
  finally { if (alive && version===formVersion) saving.value=false; }
}
async function save() {
  if (saving.value || !writable.value || conflict.value || (!pending && !canWrite.value)) return;
  formError.value='';
  if (!pending) {
    form.code=form.code.trim().toUpperCase(); form.display_name=form.display_name.trim(); form.reason=form.reason.trim();
    if (!/^[A-Z0-9][A-Z0-9_-]{1,39}$/.test(form.code) || !form.display_name || !form.reason || /[\x00-\x1f\x7f]/.test(form.display_name+form.reason)) {
      formError.value='请填写学生代码、显示名和变更原因；代码为 2–40 位字母、数字、下划线或连字符。'; return;
    }
    if (form.status==='enrolled' && (!form.class_id || !nodes.value.get(form.class_id) || !effective(nodes.value.get(form.class_id)!))) {
      formError.value='在读学生必须选择已启用的班级，且该班级的所有上级组织均已启用。'; return;
    }
    const fields={operation_id:crypto.randomUUID(),display_name:form.display_name,class_id:form.class_id || null,status:form.status,reason:form.reason};
    pending=editing.value ? {...fields,expected_revision:editing.value.revision} : {...fields,code:form.code,academic_year:applied.value.academic_year};
  }
  const version=++formVersion; saving.value=true;
  try {
    const entry=editing.value ? await props.api.reviseEnrollment(props.school.id,editing.value.id,pending as EnrollmentRevision) : await props.api.createEnrollment(props.school.id,pending as EnrollmentCreate);
    if (!alive || version!==formVersion || !writable.value) return;
    closeForm(); await refresh();
    if (alive && readable.value && tree.value) await selectEntry(entry.id);
  } catch (error) {
    if (!alive || version!==formVersion || !writable.value) return;
    const value=handle(error); formError.value=value.message;
    uncertain.value=value.status===0 || value.status>=500; conflict.value=value.status===409;
    if (!uncertain.value) pending=null;
  } finally { if (alive && version===formVersion) saving.value=false; }
}
watch(writable,value => { if (!value) closeForm(); });
watch(readable,value => { if (!value) { listVersion++; entries.value=[]; next.value=null; tree.value=null; loading.value=false; issue.value=''; clearSelection(); closeForm(); } });
onMounted(refresh);
onUnmounted(() => { alive=false; listVersion++; detailVersion++; formVersion++; pending=null; });
</script>

<template>
  <section v-if="readable" class="school-structure school-roster" aria-label="学校学生名册" :aria-busy="loading">
    <div class="structure-heading"><div><h3><Users :size="18" />学生名册与在读关系</h3><p>以校内学生代码维护逐学年在读记录，追踪调班与状态变更。</p></div><ElButton v-if="writable" :disabled="!canWrite" @click="openForm()"><Plus :size="15" />登记学生</ElButton></div>
    <form class="structure-toolbar roster-filters" @submit.prevent="refresh"><label for="roster-year">名册学年<input id="roster-year" v-model="year" maxlength="9" :disabled="dialog" /></label><label for="roster-query">搜索学生<input id="roster-query" v-model="query" maxlength="80" placeholder="学生代码或显示名" :disabled="dialog" /></label><label for="roster-filter-status">在读状态<select id="roster-filter-status" v-model="status" :disabled="dialog"><option value="">全部状态</option><option v-for="(label,key) in statuses" :key="key" :value="key">{{ label }}</option></select></label><ElButton native-type="submit" :disabled="loading || dialog">查询名册</ElButton></form>
    <p v-if="disabledSchool" class="model-warning">学校已停用，学生名册只读；启用学校后可继续维护。</p><div v-if="issue" class="error-box" role="alert">{{ issue }}</div><p v-if="loading" role="status">正在读取学生名册…</p>
    <p class="inline-note">同一学校跨学年沿用原学生代码；同名学生使用不同代码。名册登记不会自动关联家庭账号或授予访问权限。</p>
    <p v-if="tree && !entries.length && !loading" class="inline-note">该学年或筛选条件下暂无学生记录。</p>
    <div v-if="tree" class="roster-layout">
      <div class="roster-list"><div class="roster-list-heading"><strong>{{ applied.academic_year }}</strong><span>已载入 {{ entries.length }} 条</span></div><ul class="structure-tree"><li v-for="entry in entries" :key="entry.id"><button type="button" :aria-pressed="selected?.id===entry.id" @click="selectEntry(entry.id)"><span>{{ entry.display_name }}<small>{{ entry.code }} · {{ classLabel(entry.class_id) }}</small></span><ElTag size="small" :type="entry.status==='enrolled' ? 'success' : 'info'">{{ statuses[entry.status] }}</ElTag></button></li></ul><ElButton v-if="next" :disabled="loading || dialog" @click="moreEntries">加载更多学生</ElButton></div>
      <section class="structure-detail" aria-label="学生在读详情"><p v-if="detailLoading" role="status">正在读取在读详情与历史…</p><div v-if="detailIssue" class="error-box" role="alert">{{ detailIssue }}<ElButton @click="refresh">刷新名册</ElButton></div><p v-if="!selected && !detailLoading && !detailIssue" class="inline-note">选择学生，查看本学年在读信息与完整变更记录。</p>
        <template v-if="selected"><div class="structure-node-title"><h4>{{ selected.display_name }}</h4><ElTag size="small">{{ statuses[selected.status] }} · v{{ selected.revision }}</ElTag></div><dl class="details"><div><dt>学生代码</dt><dd>{{ selected.code }}</dd></div><div><dt>所属学年</dt><dd>{{ selected.academic_year }}</dd></div><div><dt>班级归属</dt><dd>{{ classLabel(selected.class_id) }}</dd></div><div><dt>本次变更原因</dt><dd>{{ selected.reason }}</dd></div></dl><ElButton v-if="writable" :disabled="!canWrite || detailLoading || selected.revision>=999999999" @click="openForm(selected)">编辑学生 / 调班 / 状态</ElButton><h4>在读变更记录</h4><ol class="structure-history roster-history"><li v-for="entry in history" :key="entry.revision_id"><strong>v{{ entry.revision }} · {{ entry.display_name }} · {{ statuses[entry.status] }}</strong><p>{{ classLabel(entry.class_id) }}</p><p>{{ entry.reason }}</p><small>{{ formatTime(entry.created_at) }} · 操作人：{{ entry.actor_id }}</small></li></ol><ElButton v-if="historyNext" :disabled="detailLoading" @click="moreHistory">加载更早在读记录</ElButton></template>
      </section>
    </div>
    <ElDialog :model-value="dialog" :title="editing ? '编辑学生在读信息' : '登记学生'" width="min(680px, 94vw)" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" @update:model-value="value => { if (!value) closeForm(); }">
      <form class="model-form" @submit.prevent="save" novalidate><p class="form-intro">{{ applied.academic_year }} 学年。学生代码与学年登记后固定；调班、显示名与状态变更均保留历史。</p><div v-if="formError" class="error-box" role="alert">{{ formError }}</div><p v-if="uncertain" class="model-warning">保存结果未确认，输入暂时锁定。重试使用原请求；关闭后请先查询名册和历史再操作。</p><ElButton v-if="conflict && editing" :disabled="saving" @click="reloadRevision">载入最新在读版本（替换草稿）</ElButton><p v-if="conflict && !editing" class="model-warning">请取消并查询名册，核对学生代码是否已在本学年登记，以及学校和班级状态。</p>
        <fieldset :disabled="saving || uncertain || conflict"><legend>学生在读信息</legend><div class="model-form-grid"><label for="roster-code">学生代码<input id="roster-code" v-model="form.code" maxlength="40" :disabled="!!editing" placeholder="例如 STU_20260001" /></label><label for="roster-name">学生显示名<input id="roster-name" v-model="form.display_name" maxlength="80" /></label><label for="roster-class">所属班级<select id="roster-class" v-model="form.class_id"><option value="">未分班（仅非在读状态可用）</option><option v-for="node in classes" :key="node.id" :value="node.id" :disabled="form.status==='enrolled' && !effective(node)">{{ classLabel(node.id) }}</option></select></label><label for="roster-status">学生状态<select id="roster-status" v-model="form.status"><option v-for="(label,key) in statuses" :key="key" :value="key">{{ label }}</option></select></label></div><label for="roster-reason" class="model-reason">在读变更原因<textarea id="roster-reason" v-model="form.reason" rows="3" maxlength="500" /></label></fieldset>
        <div class="model-form-footer"><ElButton :disabled="saving" @click="closeForm">取消</ElButton><ElButton type="primary" native-type="submit" :loading="saving" :disabled="saving || conflict">{{ uncertain ? '重试原在读请求' : editing ? '保存在读变更' : '保存学生登记' }}</ElButton></div>
      </form>
    </ElDialog>
  </section>
</template>
