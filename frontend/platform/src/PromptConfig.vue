<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, shallowRef, watch } from 'vue';
import { ElButton, ElDialog, ElForm, ElFormItem, ElInput, ElInputNumber, ElSwitch, ElTable, ElTableColumn, ElTag, ElTooltip, ElMessage, ElMessageBox } from 'element-plus';
import 'element-plus/es/components/form/style/css';
import 'element-plus/es/components/form-item/style/css';
import 'element-plus/es/components/input-number/style/css';
import 'element-plus/es/components/switch/style/css';
import 'element-plus/es/components/tooltip/style/css';
import 'element-plus/es/components/message/style/css';
import 'element-plus/es/components/message-box/style/css';
import { Plus, Search, RotateCcw } from 'lucide-vue-next';
import { ApiError, type PlatformApi, type Identity, type GradingPrompt } from './api';

const props = defineProps<{ api: PlatformApi; identity: Identity }>();
const emit = defineEmits<{ failure: [error: unknown]; revalidate: [] }>();
const writable = computed(() => props.identity.permissions.includes('platform.models.write'));
const items = ref<GradingPrompt[]>([]);
const keyword = ref(''), applied = ref('');
const loading = ref(false), issue = shallowRef<ApiError | null>(null);
const toggling = reactive<Record<string, boolean>>({});
const dialog = ref(false), editing = shallowRef<GradingPrompt | null>(null), saving = ref(false), formError = ref('');
const form = reactive({name:'',content:'',priority:10,enabled:true});
const rows = computed(() => {
  const q = applied.value.toLowerCase();
  return q ? items.value.filter(item => (item.name+' '+item.content).toLowerCase().includes(q)) : items.value;
});
const preferredId = computed(() => items.value.find(item => item.enabled)?.id ?? '');
let alive = true, version = 0;
const formatTime = (value: string) => new Date(value).toLocaleString('zh-CN',{hour12:false});
const preview = (value: string) => value.replace(/\s+/g, ' ');
function handle(error: unknown) {
  const value = error instanceof ApiError ? error : new ApiError(502);
  if (value.status === 401 || value.code === 'invalid_session') emit('failure',value);
  else if (value.status === 403) emit('revalidate');
  return value;
}
async function refresh() {
  const current = ++version;
  loading.value=true; issue.value=null;
  try {
    const result = await props.api.gradingPrompts();
    if (alive && current === version) items.value=result;
  } catch (error) { if (alive && current === version) { items.value=[]; issue.value=handle(error); } }
  finally { if (alive && current === version) loading.value=false; }
}
function query(reset = false) {
  if (reset) keyword.value='';
  applied.value=keyword.value.trim();
}
function openForm(item?: GradingPrompt) {
  if (!writable.value) return;
  editing.value=item ?? null; formError.value='';
  Object.assign(form, item ? {name:item.name,content:item.content,priority:item.priority,enabled:item.enabled}
    : {name:'',content:'',priority:items.value.length ? Math.min(9999,Math.max(...items.value.map(i => i.priority))+10) : 10,enabled:true});
  dialog.value=true;
}
function closeForm() { if (!saving.value) { dialog.value=false; editing.value=null; } }
function contentOf(value: string) {
  return value.replace(/\r\n/g,'\n').replace(/\r/g,'\n').replace(/^[\t \n]+|[\t \n]+$/g,'');
}
function validate() {
  const name = form.name.trim(), content = contentOf(form.content);
  if (!name) return '请填写名称。';
  if (/[\u0000-\u001f\u007f]/.test(name)) return '名称不能包含控制字符。';
  if (name.length > 80) return '名称不超过 80 字。';
  if (!content) return '请填写提示词。';
  if (/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(content)) return '提示词不能包含控制字符。';
  if (content.length > 4000) return '提示词不超过 4000 字。';
  if (!Number.isInteger(form.priority) || form.priority < 1 || form.priority > 9999) return '调用优先级须为 1–9999 的整数。';
  return '';
}
function payload() {
  return {name:form.name.trim(),content:contentOf(form.content),priority:form.priority,enabled:form.enabled};
}
async function save() {
  if (saving.value || !writable.value) return;
  formError.value=validate(); if (formError.value) return;
  saving.value=true;
  const body = payload();
  try {
    if (editing.value) await props.api.updateGradingPrompt(editing.value.id,body);
    else await props.api.createGradingPrompt(body);
    if (!alive) return;
    saving.value=false; closeForm();
    ElMessage.success('已保存');
    void refresh();
  } catch (error) {
    if (!alive) return;
    const value = handle(error);
    if (value.status === 401 || value.status === 403) { saving.value=false; closeForm(); return; }
    formError.value=value.message;
  } finally { saving.value=false; }
}
async function toggle(item: GradingPrompt, enabled: boolean) {
  if (!writable.value || toggling[item.id] || item.enabled === enabled) return;
  toggling[item.id]=true;
  try {
    const updated = await props.api.updateGradingPrompt(item.id,{name:item.name,content:item.content,priority:item.priority,enabled});
    if (!alive) return;
    items.value=items.value.map(entry => entry.id === item.id ? updated : entry).sort((a,b) => a.priority-b.priority || a.created_at.localeCompare(b.created_at));
    ElMessage.success(enabled ? '已启用' : '已停用');
  } catch (error) { ElMessage.error(handle(error).message); }
  finally { toggling[item.id]=false; }
}
async function remove(item: GradingPrompt) {
  if (!writable.value) return;
  try { await ElMessageBox.confirm(`确定删除提示词「${item.name}」吗？删除后不可恢复。`,'删除确认',{type:'warning',confirmButtonText:'删除',cancelButtonText:'取消'}); }
  catch { return; }
  try {
    await props.api.deleteGradingPrompt(item.id);
    ElMessage.success('已删除');
    void refresh();
  } catch (error) { ElMessage.error(handle(error).message); }
}
watch(writable, value => { if (!value) { dialog.value=false; editing.value=null; } });
onMounted(refresh);
onUnmounted(() => { alive=false; version++; });
defineExpose({refresh});
</script>

<template>
  <div class="erp-page" aria-label="提示词管理">
    <form class="erp-query" @submit.prevent="query()">
      <label for="prompt-search">名称</label>
      <ElInput id="prompt-search" v-model="keyword" size="small" clearable maxlength="80" placeholder="名称或提示词内容" style="width:220px" />
      <ElButton type="primary" size="small" native-type="submit"><Search :size="13" />查询</ElButton>
      <ElButton size="small" @click="query(true)"><RotateCcw :size="13" />重置</ElButton>
    </form>
    <div class="erp-toolbar">
      <ElButton v-if="writable" type="primary" size="small" @click="openForm()"><Plus :size="13" />新增</ElButton>
      <span class="erp-toolbar-tip">数字越小越优先。已启用且数字最小的一条为优先调用。</span>
    </div>
    <div v-if="issue" class="erp-alert" role="alert">{{ issue.message }}<ElButton link type="primary" @click="refresh">重试</ElButton></div>
    <ElTable :data="rows" border stripe size="small" row-key="id" :empty-text="loading ? '加载中…' : '暂无数据'" class="erp-table">
      <ElTableColumn type="index" label="序号" width="56" align="center" />
      <ElTableColumn prop="name" label="名称" min-width="160" show-overflow-tooltip />
      <ElTableColumn label="提示词" min-width="280">
        <template #default="{row}">
          <ElTooltip :content="row.content" placement="top" popper-class="prompt-tip">
            <span class="prompt-preview">{{ preview(row.content) }}</span>
          </ElTooltip>
        </template>
      </ElTableColumn>
      <ElTableColumn label="调用优先级" width="150" align="center">
        <template #default="{row}">
          <span>{{ row.priority }}</span>
          <ElTag v-if="row.id===preferredId" class="prompt-preferred" size="small" type="success" effect="light">优先调用</ElTag>
        </template>
      </ElTableColumn>
      <ElTableColumn label="状态" width="120" align="center">
        <template #default="{row}">
          <ElSwitch v-if="writable" :model-value="row.enabled" :loading="toggling[row.id]" :disabled="!!toggling[row.id]" aria-label="启用状态" inline-prompt active-text="启用" inactive-text="停用" @change="value => toggle(row as GradingPrompt, value === true)" />
          <ElTag v-else :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? '启用' : '停用' }}</ElTag>
        </template>
      </ElTableColumn>
      <ElTableColumn label="更新时间" width="160"><template #default="{row}">{{ formatTime(row.updated_at) }}</template></ElTableColumn>
      <ElTableColumn label="操作" width="120" align="center" fixed="right">
        <template #default="{row}">
          <template v-if="writable">
            <ElButton link type="primary" size="small" @click="openForm(row as GradingPrompt)">编辑</ElButton>
            <ElButton link type="danger" size="small" @click="remove(row as GradingPrompt)">删除</ElButton>
          </template>
          <span v-else class="muted">只读</span>
        </template>
      </ElTableColumn>
    </ElTable>
    <div class="erp-footer">共 {{ rows.length }} 条</div>

    <ElDialog :model-value="dialog" :title="editing ? '编辑提示词' : '新增提示词'" width="640px" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" @update:model-value="value => { if (!value) closeForm(); }">
      <ElForm label-width="96px" size="default" class="erp-form" @submit.prevent="save">
        <div v-if="formError" class="erp-alert" role="alert">{{ formError }}</div>
        <ElFormItem label="名称" required><ElInput id="prompt-name" v-model="form.name" maxlength="80" placeholder="如 错题原因与正确答案" aria-label="名称" /></ElFormItem>
        <ElFormItem label="提示词" required><ElInput id="prompt-content" v-model="form.content" type="textarea" :rows="10" maxlength="4000" show-word-limit placeholder="批改作业时发给模型的提示词" aria-label="提示词" /></ElFormItem>
        <ElFormItem label="调用优先级" required><ElInputNumber v-model="form.priority" :min="1" :max="9999" :step="1" step-strictly controls-position="right" aria-label="调用优先级" /><span class="erp-form-tip">数字越小越优先</span></ElFormItem>
        <ElFormItem label="状态"><ElSwitch v-model="form.enabled" inline-prompt active-text="启用" inactive-text="停用" aria-label="启用" /></ElFormItem>
      </ElForm>
      <template #footer>
        <ElButton :disabled="saving" @click="closeForm">取消</ElButton>
        <ElButton type="primary" :loading="saving" @click="save">保存</ElButton>
      </template>
    </ElDialog>
  </div>
</template>
