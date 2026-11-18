<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, shallowRef, watch } from 'vue';
import { ElButton, ElDialog, ElTag } from 'element-plus';
import { Box, ChevronLeft, ChevronRight, CircleAlert, Plus, ShieldCheck, X } from 'lucide-vue-next';
import { ApiError, type PlatformApi, type Identity, type ModelEntry, type ModelPurpose, type ContractStatus, type CatalogStatus, type ModelCreate, type ModelRevision } from './api';

const props = defineProps<{ api: PlatformApi; identity: Identity }>();
const emit = defineEmits<{ failure: [error: unknown]; revalidate: [] }>();
const writable = computed(() => props.identity.permissions.includes('platform.models.write'));
const readable = computed(() => props.identity.permissions.includes('platform.models.read'));
const purposes: Record<ModelPurpose,string> = {ocr:'文字识别',structure:'结构化识别',verify:'批改核验',tutor:'学习辅导',report:'报告生成'};
const contracts: Record<ContractStatus,string> = {pending:'待核验',approved:'已核验',rejected:'未通过',expired:'已过期'};
const availability: Record<CatalogStatus,string> = {disabled:'目录停用',available:'目录可用',contract_expired:'合同到期阻断',contract_unapproved:'合同未核验',credential_missing:'密钥未配置'};
const items = ref<ModelEntry[]>([]);
const search = ref(''), status = ref('');
const applied = reactive({q:'',enabled:undefined as boolean | undefined});
const cursors = ref<(string | undefined)[]>([undefined]), pageIndex = ref(0), nextCursor = ref<string | null>(null);
const loading = ref(false), issue = shallowRef<ApiError | null>(null);
const selected = shallowRef<ModelEntry | null>(null), history = ref<ModelEntry[]>([]), historyCursor = ref<string | null>(null);
const detailLoading = ref(false), detailIssue = shallowRef<ApiError | null>(null);
const dialog = ref(false), editing = shallowRef<ModelEntry | null>(null), saving = ref(false), conflict = ref(false), uncertain = ref(false);
const formError = ref('');
const freshForm = () => ({provider:'',model_key:'',fixed_version:'',region:'',display_name:'',purposes:[] as ModelPurpose[],
  contract_status:'pending' as ContractStatus,contract_reference:'',contract_expires_on:'',input_price:'',output_price:'',enabled:false,
  secret_action:'keep',secret_ref:'',reason:''});
const form = reactive(freshForm());
let listVersion = 0, detailVersion = 0, formVersion = 0, alive = true;
let pending: ModelCreate | ModelRevision | null = null;
const formatTime = (value: string) => new Date(value).toLocaleString('zh-CN',{hour12:false});
function asError(error: unknown) { return error instanceof ApiError ? error : new ApiError(502); }
function handle(error: unknown) {
  const value = asError(error);
  if (value.status === 401 || value.code === 'invalid_session') emit('failure',value);
  else if (value.status === 403) emit('revalidate');
  return value;
}
async function refresh() {
  const version = ++listVersion;
  loading.value=true; issue.value=null;
  try {
    const result = await props.api.models({...applied,cursor:cursors.value[pageIndex.value]});
    if (!alive || version !== listVersion || !readable.value) return;
    items.value=result.items; nextCursor.value=result.next_cursor;
  } catch (error) { if (alive && version === listVersion) { items.value=[]; nextCursor.value=null; issue.value=handle(error); } }
  finally { if (alive && version === listVersion) loading.value=false; }
}
function query(reset = false) {
  if (reset) { search.value=''; status.value=''; }
  applied.q=search.value.trim(); applied.enabled=status.value === '' ? undefined : status.value === 'true';
  cursors.value=[undefined]; pageIndex.value=0; void refresh();
}
function turnPage(direction: number) {
  if (loading.value || (direction < 0 && !pageIndex.value) || (direction > 0 && !nextCursor.value)) return;
  if (direction > 0) cursors.value[pageIndex.value+1]=nextCursor.value!;
  pageIndex.value+=direction; void refresh();
}
async function openDetail(id: string) {
  const version = ++detailVersion;
  selected.value=null; history.value=[]; historyCursor.value=null; detailIssue.value=null; detailLoading.value=true;
  try {
    const [model,revisions] = await Promise.all([props.api.model(id),props.api.modelHistory(id)]);
    if (!alive || version !== detailVersion || !readable.value) return;
    selected.value=model; history.value=revisions.items; historyCursor.value=revisions.next_cursor;
  } catch (error) { if (alive && version === detailVersion) detailIssue.value=handle(error); }
  finally { if (alive && version === detailVersion) detailLoading.value=false; }
}
function closeDetail() { detailVersion++; selected.value=null; history.value=[]; historyCursor.value=null; detailLoading.value=false; detailIssue.value=null; }
async function moreHistory() {
  if (!selected.value || !historyCursor.value || detailLoading.value) return;
  const version = ++detailVersion, id = selected.value.id;
  detailLoading.value=true; detailIssue.value=null;
  try {
    const result = await props.api.modelHistory(id,historyCursor.value);
    if (!alive || version !== detailVersion) return;
    history.value.push(...result.items); historyCursor.value=result.next_cursor;
  } catch (error) { if (alive && version === detailVersion) detailIssue.value=handle(error); }
  finally { if (alive && version === detailVersion) detailLoading.value=false; }
}
function closeForm() {
  formVersion++; dialog.value=false; editing.value=null; pending=null;
  Object.assign(form,freshForm()); formError.value=''; saving.value=false; conflict.value=false; uncertain.value=false;
}
function fillForm(model: ModelEntry) {
  Object.assign(form,freshForm(),model,{purposes:[...model.purposes],contract_reference:model.contract_reference ?? '',
    contract_expires_on:model.contract_expires_on ?? '',input_price:model.input_price_micros === null ? '' : String(model.input_price_micros),
    output_price:model.output_price_micros === null ? '' : String(model.output_price_micros),reason:'',secret_action:'keep',secret_ref:''});
}
function openForm(model?: ModelEntry) {
  if (!writable.value) return;
  closeForm(); editing.value=model ?? null;
  if (model) fillForm(model); else form.secret_action='clear';
  dialog.value=true;
}
async function reloadRevision() {
  if (!editing.value || saving.value || !writable.value) return;
  const version = formVersion, id = editing.value.id;
  saving.value=true;
  try {
    const model = await props.api.model(id);
    if (!alive || version !== formVersion || !writable.value) return;
    editing.value=model; fillForm(model); pending=null; conflict.value=false; uncertain.value=false; formError.value='';
  } catch (error) { if (alive && version === formVersion) formError.value=handle(error).message; }
  finally { if (alive && version === formVersion) saving.value=false; }
}
function price(value: string) { return value === '' ? null : Number(value); }
function validate() {
  if (!form.display_name.trim() || !form.reason.trim() || !form.purposes.length) return '请填写显示名称、模型用途及变更原因。';
  if (!editing.value && (!/^[a-z0-9][a-z0-9._-]{0,79}$/.test(form.provider) || !/^[a-z0-9][a-z0-9._-]{0,79}$/.test(form.region) ||
    !/^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,119}$/.test(form.model_key) || !/^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,119}$/.test(form.fixed_version) ||
    /(^|[._/-])(latest|auto|default|stable|current|preview)([._/-]|$)/i.test(form.fixed_version))) return '请填写有效模型身份及固定版本，不可使用 latest 等浮动别名。';
  if ([form.input_price,form.output_price].some(v => v !== '' && (!/^\d+$/.test(v) || !Number.isSafeInteger(Number(v))))) return '费用需为非负安全整数微元，未知请留空。';
  if (form.secret_action === 'replace' && !/^secret:\/\/providers\/[a-z0-9][a-z0-9_-]*\/[a-zA-Z0-9][a-zA-Z0-9_/-]*$/.test(form.secret_ref)) return '仅接受 secret://providers/ 开头的密钥服务引用，不接受明文密钥。';
  const credential = form.secret_action === 'replace' || (form.secret_action === 'keep' && editing.value?.credential_configured);
  if (form.contract_status === 'approved' && (!form.contract_reference.trim() || !form.contract_expires_on)) return '已核验合同需填写合同引用及有效期。';
  if (form.enabled && (!credential || form.contract_status !== 'approved' || !form.contract_reference.trim() || !form.contract_expires_on)) return '启用需有效合同及密钥引用。目录启用不代表生产准入。';
  return '';
}
function payload(): ModelCreate | ModelRevision {
  const metadata = {operation_id:crypto.randomUUID(),display_name:form.display_name.trim(),purposes:[...form.purposes],
    contract_status:form.contract_status,contract_reference:form.contract_reference.trim() || null,contract_expires_on:form.contract_expires_on || null,
    input_price_micros:price(form.input_price),output_price_micros:price(form.output_price),enabled:form.enabled,reason:form.reason.trim(),
    ...(form.secret_action === 'keep' ? {} : {secret_ref:form.secret_action === 'clear' ? null : form.secret_ref})};
  return editing.value ? {...metadata,expected_revision:editing.value.revision} : {...metadata,provider:form.provider,model_key:form.model_key,fixed_version:form.fixed_version,region:form.region};
}
async function save() {
  if (saving.value || conflict.value || !writable.value) return;
  formError.value=validate(); if (formError.value) return;
  pending ??= payload();
  const version = formVersion, id = editing.value?.id;
  saving.value=true;
  try {
    const result = id ? await props.api.reviseModel(id,pending as ModelRevision) : await props.api.createModel(pending as ModelCreate);
    if (!alive || version !== formVersion || !writable.value) return;
    closeForm(); void refresh(); void openDetail(result.id);
  } catch (error) {
    if (!alive || version !== formVersion) return;
    const value = handle(error);
    if (value.status === 401 || value.status === 403 || value.code === 'invalid_session') { closeForm(); return; }
    formError.value=value.message;
    conflict.value=value.status === 409;
    // A lost response may already have committed: retry the exact payload and operation UUID.
    uncertain.value=value.status === 0 || value.status >= 500;
    if (!uncertain.value) pending=null;
  } finally { if (alive && version === formVersion) saving.value=false; }
}
watch(writable, value => { if (!value) closeForm(); });
watch(readable, value => { if (!value) { listVersion++; items.value=[]; closeDetail(); closeForm(); } });
onMounted(refresh);
onUnmounted(() => { alive=false; listVersion++; closeDetail(); closeForm(); });
defineExpose({refresh});
</script>

<template>
  <div class="model-workspace">
    <div class="model-gate"><ShieldCheck :size="24" /><div><strong>目录配置与生产准入分开管理</strong><p>登记、启用不会触发模型调用。正确率须来自锁定评测集与人工金标准；未完成真实评测时，不生成评分或生产资格。</p></div></div>
    <section class="surface model-directory" aria-label="模型目录">
      <div class="card-heading"><div><h2>已登记模型</h2><p>固定版本 · 保留修订 · 密钥引用只写不读</p></div><ElButton v-if="writable" type="primary" @click="openForm()"><Plus :size="16" />登记模型</ElButton><ElTag v-else type="info" effect="plain">只读</ElTag></div>
      <form class="model-filters" @submit.prevent="query()"><label for="model-search">搜索模型<input id="model-search" v-model="search" maxlength="120" placeholder="名称、供应商或模型标识" /></label><label for="model-status">目录状态<select id="model-status" v-model="status"><option value="">全部状态</option><option value="true">已启用</option><option value="false">已停用</option></select></label><div class="filter-actions"><ElButton native-type="submit" :disabled="loading">查询</ElButton><ElButton :disabled="loading" @click="query(true)">重置筛选</ElButton></div></form>
      <div v-if="issue" class="error-box" role="alert"><CircleAlert :size="18" /><span>{{ issue.message }}</span><ElButton @click="refresh">重试查询</ElButton></div>
      <div :aria-busy="loading" class="model-list">
        <p v-if="loading" class="model-loading" role="status">正在读取模型目录…</p>
        <article v-for="model in items" :key="model.id" class="model-row"><div class="model-symbol"><Box :size="22" /></div><div class="model-name"><h3>{{ model.display_name }}</h3><code>{{ model.provider }} / {{ model.model_key }}</code><div class="model-tags"><ElTag v-for="purpose in model.purposes" :key="purpose" effect="plain" size="small">{{ purposes[purpose] }}</ElTag></div></div><dl class="model-brief"><div><dt>固定版本</dt><dd>{{ model.fixed_version }}</dd></div><div><dt>区域 / 修订</dt><dd>{{ model.region }} / v{{ model.revision }}</dd></div></dl><div class="model-state"><ElTag :type="model.catalog_status === 'available' ? 'success' : model.catalog_status === 'disabled' ? 'info' : 'warning'" size="small">{{ availability[model.catalog_status] }}</ElTag><span>{{ model.enabled ? '配置启用' : '配置停用' }}</span><span>未评测</span><small>不可生产</small></div><ElButton @click="openDetail(model.id)">查看详情</ElButton></article>
        <div v-if="!loading && !issue && !items.length" class="empty-state"><Box :size="30" /><h3>暂无匹配模型</h3><p>调整筛选条件，或登记一个固定版本模型。</p></div>
      </div><div class="table-footer"><span>第 {{ pageIndex+1 }} 页 · 本页 {{ items.length }} 条 · 每页最多 50 条</span><div><ElButton :disabled="!pageIndex || loading" @click="turnPage(-1)"><ChevronLeft :size="14" />上一页</ElButton><ElButton :disabled="!nextCursor || loading" @click="turnPage(1)">下一页<ChevronRight :size="14" /></ElButton></div></div>
    </section>
    <section v-if="selected || detailLoading || detailIssue" class="surface model-detail" role="region" aria-label="模型详情" :aria-busy="detailLoading"><div class="card-heading"><h2>模型详情</h2><ElButton aria-label="关闭模型详情" text @click="closeDetail"><X :size="18" /></ElButton></div><p v-if="detailLoading" role="status">正在读取详情与修订…</p><div v-if="detailIssue" class="error-box" role="alert">{{ detailIssue.message }}</div><template v-if="selected"><div class="model-detail-title"><div><h3>{{ selected.display_name }}</h3><code>{{ selected.provider }} / {{ selected.model_key }} / {{ selected.fixed_version }}</code></div><ElButton v-if="writable" @click="openForm(selected)">创建修订</ElButton></div><dl class="details model-details"><div><dt>部署区域</dt><dd>{{ selected.region }}</dd></div><div><dt>当前修订</dt><dd>v{{ selected.revision }}</dd></div><div><dt>合同状态</dt><dd>{{ contracts[selected.contract_status] }}</dd></div><div><dt>合同引用</dt><dd>{{ selected.contract_reference ?? '未填写' }}</dd></div><div><dt>合同有效至</dt><dd>{{ selected.contract_expires_on ?? '未填写' }}</dd></div><div><dt>密钥引用</dt><dd>{{ selected.credential_configured ? '已配置（不回显）' : '未配置' }}</dd></div><div><dt>输入费用</dt><dd>{{ selected.input_price_micros === null ? '未知' : selected.input_price_micros+' 微元 / 百万 token' }}</dd></div><div><dt>输出费用</dt><dd>{{ selected.output_price_micros === null ? '未知' : selected.output_price_micros+' 微元 / 百万 token' }}</dd></div><div><dt>配置开关</dt><dd>{{ selected.enabled ? '配置启用' : '配置停用' }}</dd></div><div><dt>当前目录可用性</dt><dd>{{ availability[selected.catalog_status] }}</dd></div><div><dt>生产准入</dt><dd>不可生产（未完成真实评测）</dd></div><div><dt>批改正确率</dt><dd>—（未完成真实评测）</dd></div><div><dt>变更原因</dt><dd>{{ selected.reason }}</dd></div><div><dt>最近修订</dt><dd>{{ formatTime(selected.created_at) }}</dd></div></dl><div class="revision-heading"><h3>不可变修订记录</h3><span>按修订时间倒序</span></div><p>历史配置的可用性按当前服务器时间判断，不代表修订发生时的状态。</p><ol class="revision-list"><li v-for="revision in history" :key="revision.revision_id"><div><strong>v{{ revision.revision }}</strong><time>{{ formatTime(revision.created_at) }}</time></div><p>{{ revision.reason }}</p><small>{{ revision.display_name }} · {{ contracts[revision.contract_status] }} · {{ revision.enabled ? '配置启用' : '配置停用' }} · 当前判断：{{ availability[revision.catalog_status] }} · 密钥{{ revision.credential_configured ? '已配置' : '未配置' }}</small></li></ol><ElButton v-if="historyCursor" :disabled="detailLoading" @click="moreHistory">加载更多修订</ElButton></template></section>
    <ElDialog :model-value="dialog" :title="editing ? '创建模型修订' : '登记模型'" width="min(760px, 94vw)" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" @update:model-value="value => { if (!value) closeForm(); }">
      <form class="model-form" @submit.prevent="save" novalidate><p class="form-intro">{{ editing ? '身份不可修改。更换模型版本请登记新条目；本次保存追加一个修订。' : '登记固定版本模型。可先保存停用草稿，完成合同及密钥配置后再启用。' }}</p><div v-if="formError" class="error-box" role="alert">{{ formError }}</div><p v-if="uncertain" class="model-warning">保存结果未确认，输入暂时锁定。重试将复用原操作键，不重复创建修订。</p><ElButton v-if="conflict && editing" :disabled="saving" @click="reloadRevision">载入最新版本（替换草稿）</ElButton><p v-if="conflict && !editing" class="model-warning">模型身份或操作键已存在，请取消并查询目录，确认后重新登记。</p>
      <fieldset :disabled="saving || uncertain || conflict"><legend>模型身份</legend><div class="model-form-grid"><label for="model-display">显示名称<input id="model-display" v-model="form.display_name" maxlength="120" required /></label><label for="model-provider">供应商标识<input id="model-provider" v-model="form.provider" :disabled="!!editing" maxlength="80" placeholder="例如 example" required /></label><label for="model-key">模型标识<input id="model-key" v-model="form.model_key" :disabled="!!editing" maxlength="120" required /></label><label for="model-version">固定版本<input id="model-version" v-model="form.fixed_version" :disabled="!!editing" maxlength="120" placeholder="固定版本，不使用 latest" required /></label><label for="model-region">部署区域<input id="model-region" v-model="form.region" :disabled="!!editing" maxlength="80" required /></label></div><div class="model-purpose-label">模型用途（至少一项）</div><div class="model-checkboxes"><label v-for="(label,key) in purposes" :key="key"><input v-model="form.purposes" type="checkbox" :value="key" />{{ label }}</label></div></fieldset>
      <fieldset :disabled="saving || uncertain || conflict"><legend>合同与密钥</legend><div class="model-form-grid"><label for="model-contract-status">合同状态<select id="model-contract-status" v-model="form.contract_status"><option v-for="(label,key) in contracts" :key="key" :value="key">{{ label }}</option></select></label><label for="model-contract-ref">合同引用<input id="model-contract-ref" v-model="form.contract_reference" maxlength="200" /></label><label for="model-contract-date">合同有效至<input id="model-contract-date" v-model="form.contract_expires_on" type="date" aria-describedby="contract-date-hint" /></label><label for="model-secret-action">密钥操作<select id="model-secret-action" v-model="form.secret_action"><option v-if="editing" value="keep">保留现有引用（不回显）</option><option value="replace">写入新引用</option><option value="clear">不配置 / 清除引用</option></select></label><label v-if="form.secret_action==='replace'" for="model-secret" class="form-wide">密钥服务引用<input id="model-secret" v-model="form.secret_ref" autocomplete="off" maxlength="240" placeholder="secret://providers/example/primary" aria-describedby="secret-ref-hint" /></label></div><small id="contract-date-hint">合同按服务器 Asia/Shanghai 日期判断，有效至当日结束。</small><small v-if="form.secret_action==='replace'" id="secret-ref-hint">只提交密钥服务引用，不填写 API Key；服务端不会回传引用。</small></fieldset>
      <fieldset :disabled="saving || uncertain || conflict"><legend>费用与目录状态</legend><div class="model-form-grid"><label for="model-input-price">输入费用（微元 / 百万 token）<input id="model-input-price" v-model="form.input_price" inputmode="numeric" placeholder="未知留空；0 表示已知免费" /></label><label for="model-output-price">输出费用（微元 / 百万 token）<input id="model-output-price" v-model="form.output_price" inputmode="numeric" placeholder="未知留空；0 表示已知免费" /></label></div><label class="model-enabled"><input v-model="form.enabled" type="checkbox" />目录启用</label><small>启用需有效合同及密钥引用，不代表可用于生产批改。1 元 = 1,000,000 微元。</small><label for="model-reason" class="model-reason">变更原因<textarea id="model-reason" v-model="form.reason" maxlength="500" rows="3" required /></label></fieldset>
      <div class="model-form-footer"><ElButton :disabled="saving" @click="closeForm">取消</ElButton><ElButton type="primary" native-type="submit" :loading="saving" :disabled="saving || conflict">{{ editing ? '保存修订' : '保存登记' }}</ElButton></div></form>
    </ElDialog>
  </div>
</template>
