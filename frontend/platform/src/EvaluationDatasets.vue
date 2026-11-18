<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, shallowRef, watch } from 'vue';
import { ElButton, ElDialog, ElTag } from 'element-plus';
import { ChevronLeft, ChevronRight, ClipboardCheck, LockKeyhole, Plus, RefreshCw } from 'lucide-vue-next';
import { ApiError, type Identity, type PlatformApi } from './api';
import { validEvaluationLabel, type EvaluationEntry, type EvaluationDataset, type EvaluationSample, type EvaluationLabel, type EvaluationCreate, type EvaluationIntake, type EvaluationAnnotation, type EvaluationAdjudication, type EvaluationCommand } from './evaluation';

const props = defineProps<{ api: PlatformApi; identity: Identity }>();
const emit = defineEmits<{ failure: [error: unknown]; revalidate: [] }>();
const has = (action: string) => props.identity.permissions.includes(`platform.evaluation.${action}`);
const readable = computed(() => has('read'));
const items = ref<EvaluationEntry[]>([]), listOffset = ref(0), nextOffset = ref<number | null>(null);
const selectedId = ref(''), dataset = shallowRef<EvaluationDataset | null>(null);
const loading = ref(false), detailLoading = ref(false), listError = ref(''), detailError = ref(''), notice = ref('');
type Kind = 'create' | 'intake' | 'annotate' | 'adjudicate' | 'lock';
type Pending = { kind:'create'; payload:EvaluationCreate } | { kind:'intake'; payload:EvaluationIntake } |
  { kind:'annotate'; payload:EvaluationAnnotation } | { kind:'adjudicate'; payload:EvaluationAdjudication } | { kind:'lock'; payload:EvaluationCommand };
const kind = ref<Kind>('create'), dialog = ref(false), saving = ref(false), uncertain = ref(false), conflict = ref(false), formError = ref('');
const targetSample = shallowRef<EvaluationSample | null>(null);
const blank = () => ({ name:'', split:'test' as EvaluationCreate['split'], handbook_version:'v1', question_id:'', source_revision:1,
  authorization_id:'', near_duplicate_group:'', redacted_stem:'', redacted_answer:'', redaction_note:'', reason:'', confirmed:false });
const blankLabel = (): EvaluationLabel => ({stem:'',student_answer:'',correct_answer:'',verdict:'correct',question_type:'short_answer',
  difficulty:'medium',requires_abstention:false,redaction_safe:false,box:[0,0,10000,10000]});
const form = reactive(blank()), label = reactive(blankLabel());
let pending: Pending | null = null, alive = true, listVersion = 0, detailVersion = 0, formVersion = 0;
const permission = (value: Kind) => value === 'annotate' ? 'annotate' : value === 'adjudicate' ? 'adjudicate' : 'curate';
const title = computed(() => ({create:'新建评测数据集',intake:'录入授权样本',annotate:'独立标注',adjudicate:'分歧裁决',lock:'锁定评测数据集'}[kind.value]));
const splitName = (value: string) => ({train:'训练分组',validation:'验证集',test:'测试集'}[value] ?? value);
const stateName = (value: string) => ({authorization_inactive:'授权已失效',adjudicated:'已裁决',agreed:'双标一致',disputed:'待裁决',pending:'待完成双标'}[value] ?? value);
const verdictName = (value: string) => ({correct:'正确',incorrect:'错误',unanswered:'未作答',pending_review:'待人工复核',unsupported:'不支持判定'}[value] ?? value);
const time = (value: string) => new Date(value).toLocaleString('zh-CN',{hour12:false});
const labelsOf = (sample: EvaluationSample) => [
  ...(sample.own_label ? [{title:'我的独立标注',value:sample.own_label}] : []),
  ...(sample.disagreement_labels ?? []).map((value,index) => ({title:`独立标注 ${String.fromCharCode(65+index)}`,value})),
  ...(sample.gold ? [{title:'已锁定标准答案',value:sample.gold}] : []),
];
function handle(error: unknown) {
  const issue = error instanceof ApiError ? error : new ApiError(502);
  if (issue.code === 'stale') return '';
  if (issue.status === 401 || issue.status === 403 || issue.code === 'invalid_session') {
    listVersion++; detailVersion++; items.value=[]; dataset.value=null; selectedId.value=''; loading.value=false; detailLoading.value=false; closeForm();
    if (issue.status === 403) emit('revalidate'); else emit('failure',issue);
  }
  return issue.message;
}
async function loadList() {
  if (!readable.value) return;
  const version = ++listVersion; loading.value=true; listError.value='';
  try {
    const result = await props.api.evaluationDatasets(listOffset.value);
    if (!alive || version !== listVersion || !readable.value) return;
    items.value=result.items; nextOffset.value=result.next_offset;
  } catch (error) { if (alive && version === listVersion) { items.value=[]; nextOffset.value=null; listError.value=handle(error); } }
  finally { if (alive && version === listVersion) loading.value=false; }
}
async function openDataset(id: string, offset = 0) {
  if (dialog.value || !readable.value) return;
  const version = ++detailVersion; selectedId.value=id; dataset.value=null; detailLoading.value=true; detailError.value=''; notice.value='';
  try {
    const result = await props.api.evaluationDataset(id,offset);
    if (alive && version === detailVersion && readable.value) dataset.value=result;
  } catch (error) { if (alive && version === detailVersion) detailError.value=handle(error); }
  finally { if (alive && version === detailVersion) detailLoading.value=false; }
}
async function refresh() {
  if (dialog.value) return;
  await Promise.all([loadList(),selectedId.value ? openDataset(selectedId.value) : Promise.resolve()]);
}
function turnList(next: boolean) {
  if (loading.value || dialog.value) return;
  listOffset.value=next ? nextOffset.value ?? listOffset.value : Math.max(0,listOffset.value-50); void loadList();
}
function closeForm() {
  formVersion++; dialog.value=false; saving.value=false; uncertain.value=false; conflict.value=false; formError.value='';
  pending=null; targetSample.value=null; Object.assign(form,blank()); Object.assign(label,blankLabel());
}
function openForm(value: Kind, sample?: EvaluationSample) {
  if (!has(permission(value)) || detailLoading.value || (value !== 'create' && dataset.value?.state !== 'draft')) return;
  closeForm(); kind.value=value; targetSample.value=sample ?? null;
  if (sample) Object.assign(label,blankLabel(),{stem:sample.redacted_stem ?? '',student_answer:sample.redacted_answer ?? ''});
  dialog.value=true;
}
function command(): Pending | null {
  const base = {operation_id:crypto.randomUUID(),expected_revision:dataset.value?.revision ?? 0};
  if (kind.value === 'create') return {kind:'create',payload:{...base,expected_revision:0,name:form.name.trim(),split:form.split,handbook_version:form.handbook_version.trim()}};
  if (kind.value === 'intake') return {kind:'intake',payload:{...base,question_id:form.question_id.trim(),source_revision:form.source_revision,
    authorization_id:form.authorization_id.trim(),near_duplicate_group:form.near_duplicate_group.trim(),
    redacted_stem:form.redacted_stem.trim(),redacted_answer:form.redacted_answer.trim(),redaction_note:form.redaction_note.trim()}};
  if (kind.value === 'lock') {
    if (!form.confirmed) { formError.value='请确认样本脱敏、独立标注及裁决已完成。'; return null; }
    return {kind:'lock',payload:base};
  }
  const copy: EvaluationLabel = {...label,box:[...label.box]};
  if (!validEvaluationLabel(copy)) { formError.value='请核对标签：可判题需填写标准答案，未作答需留空学生答案；不可判题必须勾选拒评，题框须为有效矩形。'; return null; }
  return kind.value === 'annotate' ? {kind:'annotate',payload:{...base,label:copy}} :
    {kind:'adjudicate',payload:{...base,label:copy,reason:form.reason.trim()}};
}
async function save() {
  if (saving.value || conflict.value || !has(permission(kind.value))) return;
  if (!pending) pending=command();
  if (!pending) return;
  const operation = pending, id = dataset.value?.id ?? '', sample = targetSample.value?.id ?? '', version = ++formVersion;
  saving.value=true; formError.value='';
  try {
    let result: EvaluationDataset;
    if (operation.kind === 'create') result=await props.api.createEvaluationDataset(operation.payload);
    else if (operation.kind === 'intake') result=await props.api.intakeEvaluationSample(id,operation.payload);
    else if (operation.kind === 'annotate') result=await props.api.annotateEvaluationSample(id,sample,operation.payload);
    else if (operation.kind === 'adjudicate') result=await props.api.adjudicateEvaluationSample(id,sample,operation.payload);
    else result=await props.api.lockEvaluationDataset(id,operation.payload);
    if (!alive || version !== formVersion || !readable.value || !has(permission(operation.kind))) return;
    detailVersion++; dataset.value=result; selectedId.value=result.id; detailLoading.value=false;
    closeForm(); notice.value='操作已保存，已显示服务端当前修订。'; void loadList();
  } catch (error) {
    if (!alive || version !== formVersion) return;
    const issue = error instanceof ApiError ? error : new ApiError(502);
    if (issue.status === 401 || issue.status === 403 || issue.code === 'invalid_session' || issue.code === 'stale') { handle(issue); return; }
    uncertain.value=issue.status === 0 || issue.status >= 500; conflict.value=issue.status === 409;
    if (!uncertain.value) pending=null;
    formError.value=conflict.value ? '修订或操作状态已变化。请关闭窗口并刷新详情，核对后重新操作。' :
      uncertain.value ? '保存结果尚未确认。表单已冻结，重试将发送相同的操作编号与内容。' : issue.message;
  } finally { if (alive && version === formVersion) saving.value=false; }
}
watch(() => [props.identity.id,...props.identity.permissions].join('|'),() => {
  listVersion++; detailVersion++; closeForm(); dataset.value=null; selectedId.value=''; items.value=[];
  loading.value=false; detailLoading.value=false; if (readable.value) void loadList();
});
onMounted(loadList);
onUnmounted(() => { alive=false; listVersion++; detailVersion++; closeForm(); });
defineExpose({refresh});
</script>

<template>
  <div class="evaluation-workspace">
    <p v-if="notice" role="status" class="inline-note">{{ notice }}</p>
    <div class="evaluation-columns">
      <section class="surface evaluation-list" aria-label="评测数据集列表" :aria-busy="loading">
        <div class="card-heading"><h2>评测数据集</h2><div class="heading-actions"><ElButton v-if="has('curate')" type="primary" size="small" @click="openForm('create')"><Plus :size="14" />新建数据集</ElButton><ElButton text :disabled="loading || dialog" aria-label="刷新数据集列表" @click="loadList"><RefreshCw :size="16" /></ElButton></div></div>
        <p v-if="loading" role="status" class="subtext">正在加载数据集…</p>
        <div v-else-if="listError" role="alert" class="evaluation-error"><p>{{ listError }}</p><ElButton @click="loadList">重试列表</ElButton></div>
        <div v-else-if="!items.length" class="evaluation-empty"><ClipboardCheck :size="32" /><h3>暂无评测数据集</h3><p>{{ has('curate') ? '先建档，再录入已授权的脱敏题目。' : '请等待管理员创建数据集。' }}</p></div>
        <button v-for="item in items" :key="item.id" class="evaluation-list-item" :class="{selected:selectedId===item.id}" :disabled="dialog" :aria-pressed="selectedId===item.id" @click="openDataset(item.id)">
          <span><strong>{{ item.name }}</strong><small>{{ splitName(item.split) }} · {{ item.handbook_version }} · 修订 {{ item.revision }}</small></span>
          <ElTag :type="item.state==='locked' ? 'success' : 'info'" size="small" effect="plain">{{ item.state==='locked' ? '已锁定' : '草稿' }}</ElTag>
        </button>
        <div class="evaluation-pager"><ElButton :disabled="!listOffset || loading || dialog" aria-label="上一页数据集" @click="turnList(false)"><ChevronLeft :size="15" /></ElButton><span>第 {{ Math.floor(listOffset/50)+1 }} 页</span><ElButton :disabled="nextOffset===null || loading || dialog" aria-label="下一页数据集" @click="turnList(true)"><ChevronRight :size="15" /></ElButton></div>
      </section>
      <section class="surface evaluation-detail" aria-label="评测数据集详情" :aria-busy="detailLoading">
        <p v-if="detailLoading" role="status">正在核对样本授权与标注状态…</p>
        <div v-else-if="detailError" role="alert" class="evaluation-error"><p>{{ detailError }}</p><ElButton @click="openDataset(selectedId)">重试详情</ElButton></div>
        <div v-else-if="!dataset" class="evaluation-empty"><ClipboardCheck :size="40" /><h3>选择一个数据集开始核对</h3><p>在这里查看样本、提交独立标注、处理分歧并锁定标准答案。</p></div>
        <template v-else>
          <div class="card-heading"><div><span class="eyebrow muted">{{ splitName(dataset.split) }} · {{ dataset.handbook_version }}</span><h2>{{ dataset.name }}</h2></div><ElTag :type="dataset.state==='locked' ? 'success' : 'info'">{{ dataset.state==='locked' ? '已锁定' : '草稿' }} · 修订 {{ dataset.revision }}</ElTag></div>
          <p class="subtext">建档于 {{ time(dataset.created_at) }} · 仅限获授权的内部脱敏离线评测</p>
          <div class="evaluation-metrics"><div><strong>{{ dataset.sample_count }}</strong><span>收录样本</span></div><div><strong>{{ dataset.agreement.double_labeled }}</strong><span>完成双标</span></div><div><strong>{{ dataset.agreement.rate===null ? '—' : `${(dataset.agreement.rate*100).toFixed(1)}%` }}</strong><span>双标一致率</span></div><div><strong>{{ dataset.agreement.adjudicated }} / {{ dataset.agreement.disputed }}</strong><span>已裁决 / 分歧</span></div></div>
          <p v-if="!dataset.usable && dataset.sample_count > 0" role="status" class="evaluation-warning">存在失效授权或数据集暂不可用，请核对样本状态。失效样本的正文、标签及标准答案均不再展示。</p>
          <p class="inline-note">双标一致率衡量人工标注一致程度。模型批改正确率需要后续固定版本评测；此处不表示生产准入或训练授权。</p>
          <div v-if="dataset.state==='draft' && has('curate')" class="evaluation-toolbar"><ElButton @click="openForm('intake')"><Plus :size="15" />录入授权样本</ElButton><ElButton :disabled="!dataset.sample_count || !dataset.usable" @click="openForm('lock')"><LockKeyhole :size="15" />检查并锁定</ElButton></div>
          <dl v-if="dataset.state==='locked'" class="details evaluation-lock"><div><dt>锁定时间</dt><dd>{{ time(dataset.locked_at!) }}</dd></div><div><dt>锁定摘要</dt><dd class="mono">{{ dataset.locked_digest }}</dd></div></dl>
          <div v-if="!dataset.samples.length" class="evaluation-empty"><h3>当前页没有样本</h3><p>录入时需要源题编号、源题修订和有效授权编号。</p></div>
          <article v-for="sample in dataset.samples" :key="sample.id" class="evaluation-sample">
            <div class="card-heading"><h3>{{ sample.grade }} 年级 · {{ stateName(sample.state) }}</h3><ElTag size="small" effect="plain">{{ sample.label_count }} / 2 独立标注</ElTag></div>
            <p class="evaluation-reference mono">样本 {{ sample.id }}</p>
            <template v-if="sample.state!=='authorization_inactive'">
              <dl class="evaluation-source"><div><dt>脱敏题干</dt><dd>{{ sample.redacted_stem }}</dd></div><div><dt>脱敏作答</dt><dd>{{ sample.redacted_answer || '（空白作答）' }}</dd></div></dl>
              <div class="evaluation-labels"><section v-for="entry in labelsOf(sample)" :key="entry.title" class="evaluation-label"><h4>{{ entry.title }}</h4><dl><div><dt>题干</dt><dd>{{ entry.value.stem }}</dd></div><div><dt>学生答案</dt><dd>{{ entry.value.student_answer || '（空白）' }}</dd></div><div><dt>标准答案</dt><dd>{{ entry.value.correct_answer || '（未提供）' }}</dd></div><div><dt>判定</dt><dd>{{ verdictName(entry.value.verdict) }}</dd></div><div><dt>题型 / 难度</dt><dd>{{ entry.value.question_type }} / {{ {easy:'简单',medium:'中等',hard:'困难'}[entry.value.difficulty] }}</dd></div><div><dt>拒评 / 脱敏</dt><dd>{{ entry.value.requires_abstention ? '需拒评' : '可判定' }} / {{ entry.value.redaction_safe ? '已确认脱敏' : '未确认脱敏' }}</dd></div><div><dt>题目框</dt><dd>{{ entry.value.box.join(', ') }}</dd></div></dl></section></div>
              <div v-if="dataset.state==='draft'" class="evaluation-toolbar"><ElButton v-if="has('annotate') && !sample.own_label && sample.label_count<2" @click="openForm('annotate',sample)">提交独立标注</ElButton><ElButton v-if="has('adjudicate') && sample.disagreement_labels" @click="openForm('adjudicate',sample)">裁决此分歧</ElButton><span v-if="sample.own_label" class="subtext">已提交的标注不可修改；独立标注阶段隐藏他人答案。</span></div>
            </template>
            <p v-else class="evaluation-warning">源题评测授权已撤回、过期或变更，此样本不可使用。</p>
          </article>
          <div class="evaluation-pager"><ElButton :disabled="!dataset.offset || detailLoading" @click="openDataset(dataset.id,Math.max(0,dataset.offset-50))">上一页样本</ElButton><span>第 {{ Math.floor(dataset.offset/50)+1 }} 页</span><ElButton :disabled="dataset.next_offset===null || detailLoading" @click="openDataset(dataset.id,dataset.next_offset!)">下一页样本</ElButton></div>
        </template>
      </section>
    </div>
    <ElDialog :model-value="dialog" :title="title" width="min(740px, 94vw)" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" @update:model-value="value => { if (!value && !saving) closeForm(); }">
      <form class="model-form evaluation-form" @submit.prevent="save">
        <p v-if="kind!=='create'" class="subtext">{{ dataset?.name }} · 基于修订 {{ dataset?.revision }}</p>
        <fieldset :disabled="saving || uncertain || conflict">
          <template v-if="kind==='create'">
            <label>数据集名称<input v-model="form.name" required maxlength="120" autocomplete="off" /></label>
            <div class="evaluation-form-grid"><label>数据分组<select v-model="form.split"><option value="test">测试集</option><option value="validation">验证集</option><option value="train">训练分组（不授予训练权）</option></select></label><label>标注手册版本<input v-model="form.handbook_version" required pattern="[a-zA-Z0-9][a-zA-Z0-9._/-]{0,119}" maxlength="120" /></label></div>
            <p class="subtext">同一学生、页面、近重复题组和归一化题干不能跨分组。分组和手册版本在建档后固定。</p>
          </template>
          <template v-else-if="kind==='intake'">
            <label>源题编号<input v-model="form.question_id" required pattern="[0-9a-fA-F-]{36}" maxlength="36" /></label>
            <div class="evaluation-form-grid"><label>源题修订<input v-model.number="form.source_revision" type="number" min="1" step="1" required /></label><label>近重复题组<input v-model="form.near_duplicate_group" required pattern="[a-zA-Z0-9][a-zA-Z0-9._/-]{0,119}" maxlength="120" /></label></div>
            <label>授权编号<input v-model="form.authorization_id" required pattern="[0-9a-fA-F-]{36}" maxlength="36" /></label>
            <label>脱敏题干<textarea v-model="form.redacted_stem" required maxlength="8000" rows="3" /></label>
            <label>脱敏学生答案<textarea v-model="form.redacted_answer" maxlength="8000" rows="2" /></label>
            <label>脱敏处理说明<textarea v-model="form.redaction_note" required maxlength="1000" rows="2" /></label>
            <p class="subtext">人工移除姓名、联系方式和其他身份信息；服务端核验源题修订、用途授权及跨分组泄漏。</p>
          </template>
          <template v-else-if="kind==='annotate' || kind==='adjudicate'">
            <p class="inline-note">{{ kind==='annotate' ? '请独立判断。提交前无法查看他人标注，提交后不可修改。' : '请结合详情中的两份独立标注裁决；裁决人必须独立于两位标注人。' }}</p>
            <label>标注题干<textarea v-model="label.stem" required maxlength="8000" rows="3" /></label>
            <label>标注学生答案<textarea v-model="label.student_answer" maxlength="8000" rows="2" /></label>
            <label>标准答案<textarea v-model="label.correct_answer" maxlength="8000" rows="2" /></label>
            <div class="evaluation-form-grid"><label>判定<select v-model="label.verdict"><option value="correct">正确</option><option value="incorrect">错误</option><option value="unanswered">未作答</option><option value="pending_review">待人工复核</option><option value="unsupported">不支持判定</option></select></label><label>难度<select v-model="label.difficulty"><option value="easy">简单</option><option value="medium">中等</option><option value="hard">困难</option></select></label></div>
            <label>题型代码<input v-model="label.question_type" required pattern="[a-zA-Z0-9][a-zA-Z0-9._/-]{0,119}" maxlength="120" /></label>
            <div class="evaluation-box"><label v-for="(name,index) in ['题框左边界','题框上边界','题框右边界','题框下边界']" :key="name">{{ name }}<input v-model.number="label.box[index]" type="number" required min="0" max="10000" step="1" /></label></div>
            <p class="subtext">题框使用 0–10000 的归一化坐标；左边界小于右边界，上边界小于下边界。</p>
            <label class="evaluation-check"><input v-model="label.requires_abstention" type="checkbox" />该题需要拒评 / 转人工</label>
            <label class="evaluation-check"><input v-model="label.redaction_safe" type="checkbox" />已人工核对：题目和作答均完成脱敏</label>
            <label v-if="kind==='adjudicate'">裁决依据<textarea v-model="form.reason" required maxlength="1000" rows="3" /></label>
          </template>
          <template v-else>
            <div class="evaluation-warning"><strong>锁定后不可修改样本与标签。</strong><p>服务端将重新核验授权、脱敏确认、独立双标和分歧裁决，生成固定摘要。未完成条件时会拒绝锁定。</p></div>
            <label class="evaluation-check"><input v-model="form.confirmed" type="checkbox" required />我已核对样本、双标与裁决，确认锁定</label>
          </template>
        </fieldset>
        <p v-if="formError" role="alert" class="evaluation-error">{{ formError }}</p>
        <div class="evaluation-form-actions"><ElButton :disabled="saving" @click="closeForm">{{ conflict ? '关闭并核对详情' : '关闭' }}</ElButton><ElButton type="primary" native-type="submit" :loading="saving" :disabled="conflict">{{ uncertain ? '重试原请求' : kind==='lock' ? '确认锁定' : '提交保存' }}</ElButton></div>
      </form>
    </ElDialog>
  </div>
</template>
