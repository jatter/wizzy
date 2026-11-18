<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, shallowRef, watch } from 'vue';
import { ElButton, ElDialog, ElTag } from 'element-plus';
import { GitBranch, Plus } from 'lucide-vue-next';
import { ApiError, type PlatformApi, type Identity, type SchoolEntry, type StructureEntry, type StructureKind, type StructureTree, type StructureCreate, type StructureRevision } from './api';

const props = defineProps<{ api: PlatformApi; identity: Identity; school: SchoolEntry }>();
const emit = defineEmits<{ failure: [error: unknown]; revalidate: [] }>();
const readable = computed(() => props.identity.permissions.includes('platform.schools.read'));
const writable = computed(() => props.identity.permissions.includes('platform.schools.write'));
const year = ref(props.school.academic_year), appliedYear = ref(props.school.academic_year);
const tree = shallowRef<StructureTree | null>(null), loading = ref(false), issue = ref('');
const selected = shallowRef<StructureEntry | null>(null), history = ref<StructureEntry[]>([]), cursor = ref<string | null>(null);
const detailLoading = ref(false), detailIssue = ref('');
const dialog = ref(false), editing = shallowRef<StructureEntry | null>(null), saving = ref(false);
const conflict = ref(false), uncertain = ref(false), formError = ref('');
const blank = () => ({ code:'', name:'', kind:'grade' as StructureKind, parent_id:'', status:'active' as 'active' | 'disabled', reason:'' });
const form = reactive(blank());
const kinds: Record<StructureKind,string> = {campus:'校区',department:'部门',grade:'年级',class:'班级'};
let alive = true, treeVersion = 0, detailVersion = 0, formVersion = 0;
let pending: StructureCreate | StructureRevision | null = null;
const nodes = computed(() => new Map((tree.value?.items ?? []).map(item => [item.id,item])));
const disabledSchool = computed(() => props.school.status === 'disabled' || tree.value?.school_status === 'disabled');
const canWrite = computed(() => writable.value && !!tree.value && !loading.value && !disabledSchool.value);
const rows = computed(() => {
  const result: {node: StructureEntry; depth:number}[] = [];
  const children = new Map<string | null,StructureEntry[]>();
  for (const node of nodes.value.values()) { const group=children.get(node.parent_id) ?? []; group.push(node); children.set(node.parent_id,group); }
  function visit(parent: string | null, depth: number) {
    for (const node of (children.get(parent) ?? []).sort((a,b) => a.code.localeCompare(b.code))) { result.push({node,depth}); visit(node.id,depth+1); }
  }
  visit(null,0); return result;
});
function effective(node: StructureEntry) {
  if (disabledSchool.value) return false;
  let ancestor: StructureEntry | undefined = node;
  while (ancestor) { if (ancestor.status === 'disabled') return false; ancestor=ancestor.parent_id ? nodes.value.get(ancestor.parent_id) : undefined; }
  return true;
}
const parentOptions = computed(() => rows.value.filter(({node}) => {
  if (form.kind === 'campus') return false;
  if (form.kind === 'class' ? node.kind !== 'grade' : !['campus','department'].includes(node.kind)) return false;
  let ancestor: StructureEntry | undefined = node;
  while (ancestor) { if (ancestor.id === editing.value?.id) return false; ancestor=ancestor.parent_id ? nodes.value.get(ancestor.parent_id) : undefined; }
  return true;
}));
const formatTime = (value: string) => new Date(value).toLocaleString('zh-CN',{hour12:false});
function handle(error: unknown) {
  const value=error instanceof ApiError ? error : new ApiError(502);
  if (value.status === 401 || value.code === 'invalid_session') emit('failure',value);
  else if (value.status === 403) emit('revalidate');
  return value;
}
function clearSelection() { detailVersion++; selected.value=null; history.value=[]; cursor.value=null; detailIssue.value=''; detailLoading.value=false; }
async function refresh() {
  const version=++treeVersion;
  clearSelection(); tree.value=null; issue.value='';
  const value=year.value.trim(), start=Number(value.slice(0,4));
  if (!/^[0-9]{4}-[0-9]{4}$/.test(value) || start<2000 || start>9998 || Number(value.slice(5))!==start+1) {
    loading.value=false; issue.value='请输入相邻学年，例如 2026-2027。'; return;
  }
  appliedYear.value=value; loading.value=true;
  try {
    const result=await props.api.schoolStructure(props.school.id,value);
    if (alive && version===treeVersion && readable.value) tree.value=result;
  } catch (error) { if (alive && version===treeVersion && readable.value) issue.value=handle(error).message; }
  finally { if (alive && version===treeVersion) loading.value=false; }
}
async function selectNode(id: string) {
  clearSelection(); const version=detailVersion; detailLoading.value=true;
  try {
    const [node,revisions]=await Promise.all([props.api.structureNode(props.school.id,id),props.api.structureHistory(props.school.id,id)]);
    if (!alive || version!==detailVersion || !readable.value) return;
    if (node.academic_year!==appliedYear.value) throw new ApiError(502);
    selected.value=node; history.value=revisions.items; cursor.value=revisions.next_cursor;
  } catch (error) { if (alive && version===detailVersion && readable.value) detailIssue.value=handle(error).message; }
  finally { if (alive && version===detailVersion) detailLoading.value=false; }
}
async function moreHistory() {
  if (!selected.value || !cursor.value || detailLoading.value) return;
  const version=++detailVersion; detailLoading.value=true; detailIssue.value='';
  try {
    const result=await props.api.structureHistory(props.school.id,selected.value.id,cursor.value);
    if (!alive || version!==detailVersion || !readable.value) return;
    history.value.push(...result.items); cursor.value=result.next_cursor;
  } catch (error) { if (alive && version===detailVersion && readable.value) detailIssue.value=handle(error).message; }
  finally { if (alive && version===detailVersion) detailLoading.value=false; }
}
function closeForm() {
  formVersion++; dialog.value=false; editing.value=null; pending=null; saving.value=false;
  conflict.value=false; uncertain.value=false; formError.value=''; Object.assign(form,blank());
}
function openForm(node?: StructureEntry) {
  if (!canWrite.value) return;
  closeForm(); editing.value=node ?? null;
  if (node) Object.assign(form,{code:node.code,name:node.name,kind:node.kind,parent_id:node.parent_id ?? '',status:node.status});
  dialog.value=true;
}
async function reloadRevision() {
  if (!editing.value || saving.value) return;
  const version=++formVersion; saving.value=true;
  try {
    const node=await props.api.structureNode(props.school.id,editing.value.id);
    if (!alive || version!==formVersion || !writable.value) return;
    editing.value=node; Object.assign(form,{code:node.code,name:node.name,kind:node.kind,parent_id:node.parent_id ?? '',status:node.status,reason:''});
    conflict.value=false; pending=null; formError.value='';
  } catch (error) { if (alive && version===formVersion) formError.value=handle(error).message; }
  finally { if (alive && version===formVersion) saving.value=false; }
}
async function save() {
  if (saving.value || !writable.value || conflict.value || (!pending && !canWrite.value)) return;
  formError.value='';
  if (!pending) {
    form.code=form.code.trim().toUpperCase(); form.name=form.name.trim(); form.reason=form.reason.trim();
    if (!/^[A-Z0-9][A-Z0-9_-]{1,39}$/.test(form.code) || !form.name || !form.reason || /[\x00-\x1f\x7f]/.test(form.name+form.reason) ||
      (form.kind==='class' && !form.parent_id) || (form.kind==='campus' && form.parent_id)) {
      formError.value='请填写组织代码、名称和变更原因；班级必须选择所属年级。'; return;
    }
    const fields={operation_id:crypto.randomUUID(),name:form.name,status:form.status,parent_id:form.parent_id || null,reason:form.reason};
    pending=editing.value ? {...fields,expected_revision:editing.value.revision} : {...fields,code:form.code,kind:form.kind,academic_year:appliedYear.value};
  }
  const version=++formVersion; saving.value=true;
  try {
    const node=editing.value ? await props.api.reviseStructure(props.school.id,editing.value.id,pending as StructureRevision) : await props.api.createStructure(props.school.id,pending as StructureCreate);
    if (!alive || version!==formVersion || !writable.value) return;
    closeForm(); await refresh();
    if (alive && readable.value && tree.value?.items.some(item => item.id===node.id)) await selectNode(node.id);
  } catch (error) {
    if (!alive || version!==formVersion || !writable.value) return;
    const value=handle(error); formError.value=value.message;
    uncertain.value=value.status===0 || value.status>=500;
    conflict.value=value.status===409;
    if (!uncertain.value) pending=null;
  } finally { if (alive && version===formVersion) saving.value=false; }
}
watch(() => form.kind, () => { if (!editing.value && form.parent_id && !parentOptions.value.some(row => row.node.id===form.parent_id)) form.parent_id=''; });
watch(writable,value => { if (!value) closeForm(); });
watch(readable,value => { if (!value) { treeVersion++; tree.value=null; loading.value=false; issue.value=''; clearSelection(); closeForm(); } });
onMounted(refresh);
onUnmounted(() => { alive=false; treeVersion++; detailVersion++; formVersion++; pending=null; });
</script>

<template>
  <section v-if="readable" class="school-structure" aria-label="学校组织结构" :aria-busy="loading">
    <div class="structure-heading"><div><h3><GitBranch :size="18" />组织、年级与班级</h3><p>按学年维护学校结构，保留每次调整的版本与原因。</p></div><ElButton v-if="writable" :disabled="!canWrite || (tree?.items.length ?? 0)>=2000" @click="openForm()"><Plus :size="15" />登记组织</ElButton></div>
    <form class="structure-toolbar" @submit.prevent="refresh"><label for="structure-year">组织学年<input id="structure-year" v-model="year" maxlength="9" placeholder="2026-2027" :disabled="dialog" /></label><ElButton native-type="submit" :disabled="loading || dialog">查询组织</ElButton><span v-if="tree">{{ tree.items.length }} / 2000 个节点</span></form>
    <p v-if="disabledSchool" class="model-warning">学校已停用，组织结构只读；启用学校后可继续维护。</p>
    <div v-if="issue" class="error-box" role="alert">{{ issue }}</div><p v-if="loading" role="status">正在读取组织结构…</p>
    <p v-if="tree && !tree.items.length" class="inline-note">该学年暂无组织，可先登记校区或年级，再在年级下登记班级。</p>
    <div v-if="tree?.items.length" class="structure-layout">
      <div class="structure-tree"><p class="structure-root">{{ school.name }} · {{ appliedYear }}</p><ul aria-label="组织目录"><li v-for="row in rows" :key="row.node.id"><button type="button" :aria-pressed="selected?.id===row.node.id" :style="{'--node-depth':row.depth}" @click="selectNode(row.node.id)"><span class="structure-kind">{{ kinds[row.node.kind] }}</span><span>{{ row.node.name }}<small>{{ row.node.code }}</small></span><span v-if="!effective(row.node)" class="structure-disabled">停用</span></button></li></ul></div>
      <section class="structure-detail" aria-label="组织详情">
        <p v-if="detailLoading" role="status">正在读取组织及修订记录…</p><div v-if="detailIssue" class="error-box" role="alert">{{ detailIssue }}<ElButton @click="refresh">刷新组织</ElButton></div>
        <p v-if="!selected && !detailLoading && !detailIssue" class="inline-note">选择左侧组织，查看归属、有效状态与修订记录。</p>
        <template v-if="selected"><div class="structure-node-title"><h4>{{ selected.name }}</h4><ElTag size="small">{{ kinds[selected.kind] }} · v{{ selected.revision }}</ElTag></div>
          <dl class="details"><div><dt>组织代码</dt><dd>{{ selected.code }}</dd></div><div><dt>上级组织</dt><dd>{{ selected.parent_id ? nodes.get(selected.parent_id)?.name ?? selected.parent_id : '学校直属' }}</dd></div><div><dt>自身状态</dt><dd>{{ selected.status==='active' ? '已启用' : '已停用' }}</dd></div><div><dt>有效状态</dt><dd>{{ effective(selected) ? '已启用' : '已停用（自身、学校或上级组织停用）' }}</dd></div></dl>
          <p class="inline-note">上级停用会影响下级的有效状态，不改写下级自身配置。组织登记不自动授予学生或家庭访问权限。</p>
          <ElButton v-if="writable" :disabled="!canWrite || selected.revision>=999999999 || detailLoading" @click="openForm(selected)">编辑组织 / 移动 / 启停</ElButton>
          <h4>组织修订记录</h4><ol class="structure-history"><li v-for="entry in history" :key="entry.revision_id"><strong>v{{ entry.revision }} · {{ entry.name }} · {{ entry.status==='active' ? '已启用' : '已停用' }}</strong><p>上级编号：{{ entry.parent_id ?? '学校直属' }}</p><p>{{ entry.reason }}</p><small>{{ formatTime(entry.created_at) }} · 操作人：{{ entry.actor_id }}</small></li></ol><ElButton v-if="cursor" :disabled="detailLoading" @click="moreHistory">加载更早组织记录</ElButton>
        </template>
      </section>
    </div>
    <ElDialog :model-value="dialog" :title="editing ? '编辑组织' : '登记组织'" width="min(680px, 94vw)" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" @update:model-value="value => { if (!value) closeForm(); }">
      <form class="model-form" @submit.prevent="save" novalidate><p class="form-intro">{{ appliedYear }} 学年。组织代码、类型和学年登记后固定；调整名称、上级或状态将追加修订记录。</p><div v-if="formError" class="error-box" role="alert">{{ formError }}</div>
        <p v-if="uncertain" class="model-warning">保存结果未确认，输入暂时锁定。重试使用原请求；关闭后请先查询组织和历史再操作。</p><ElButton v-if="conflict && editing" :disabled="saving" @click="reloadRevision">载入最新组织版本（替换草稿）</ElButton><p v-if="conflict && !editing" class="model-warning">请取消并刷新组织，核对学校状态、组织代码和容量后重新登记。</p>
        <fieldset :disabled="saving || uncertain || conflict"><legend>组织基本信息</legend><div class="model-form-grid">
          <label for="structure-code">组织代码<input id="structure-code" v-model="form.code" maxlength="40" :disabled="!!editing" placeholder="例如 GRADE_01" /></label>
          <label for="structure-name">组织名称<input id="structure-name" v-model="form.name" maxlength="120" /></label>
          <label for="structure-kind">组织类型<select id="structure-kind" v-model="form.kind" :disabled="!!editing"><option v-for="(label,key) in kinds" :key="key" :value="key">{{ label }}</option></select></label>
          <label for="structure-parent">上级组织<select id="structure-parent" v-model="form.parent_id"><option v-if="form.kind!=='class'" value="">学校直属</option><option v-else value="" disabled>请选择所属年级</option><option v-for="row in parentOptions" :key="row.node.id" :value="row.node.id">{{ kinds[row.node.kind] }} · {{ row.node.name }}（{{ row.node.code }}）</option></select></label>
          <label for="structure-status">组织自身状态<select id="structure-status" v-model="form.status"><option value="active">已启用</option><option value="disabled">已停用</option></select></label>
        </div><label for="structure-reason" class="model-reason">组织变更原因<textarea id="structure-reason" v-model="form.reason" rows="3" maxlength="500" /></label></fieldset>
        <div class="model-form-footer"><ElButton :disabled="saving" @click="closeForm">取消</ElButton><ElButton type="primary" native-type="submit" :loading="saving" :disabled="saving || conflict">{{ uncertain ? '重试原组织请求' : editing ? '保存组织变更' : '保存组织登记' }}</ElButton></div>
      </form>
    </ElDialog>
  </section>
</template>
