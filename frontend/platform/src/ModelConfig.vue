<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, shallowRef, watch } from 'vue';
import { ElButton, ElDialog, ElForm, ElFormItem, ElInput, ElInputNumber, ElTable, ElTableColumn, ElTag, ElTooltip, ElMessage, ElMessageBox } from 'element-plus';
import 'element-plus/es/components/form/style/css';
import 'element-plus/es/components/form-item/style/css';
import 'element-plus/es/components/input-number/style/css';
import 'element-plus/es/components/tooltip/style/css';
import 'element-plus/es/components/message/style/css';
import 'element-plus/es/components/message-box/style/css';
import { Plus, Search, RotateCcw } from 'lucide-vue-next';
import { ApiError, type PlatformApi, type Identity, type ModelConfig } from './api';

const props = defineProps<{ api: PlatformApi; identity: Identity }>();
const emit = defineEmits<{ failure: [error: unknown]; revalidate: [] }>();
const writable = computed(() => props.identity.permissions.includes('platform.models.write'));
const items = ref<ModelConfig[]>([]);
const keyword = ref(''), applied = ref('');
const loading = ref(false), issue = shallowRef<ApiError | null>(null);
const testing = reactive<Record<string, boolean>>({});
const dialog = ref(false), editing = shallowRef<ModelConfig | null>(null), saving = ref(false), formError = ref('');
const form = reactive({name:'',base_url:'',api_key:'',priority:10});
const rows = computed(() => {
  const q = applied.value.toLowerCase();
  return q ? items.value.filter(item => (item.name+' '+item.base_url).toLowerCase().includes(q)) : items.value;
});
let alive = true, version = 0;
const formatTime = (value: string | null) => value ? new Date(value).toLocaleString('zh-CN',{hour12:false}) : '—';
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
    const result = await props.api.modelConfigs();
    if (alive && current === version) items.value=result;
  } catch (error) { if (alive && current === version) { items.value=[]; issue.value=handle(error); } }
  finally { if (alive && current === version) loading.value=false; }
}
function query(reset = false) {
  if (reset) keyword.value='';
  applied.value=keyword.value.trim();
}
function openForm(item?: ModelConfig) {
  if (!writable.value) return;
  editing.value=item ?? null; formError.value='';
  Object.assign(form,item ? {name:item.name,base_url:item.base_url,api_key:'',priority:item.priority}
    : {name:'',base_url:'',api_key:'',priority:items.value.length ? Math.min(9999,Math.max(...items.value.map(i => i.priority))+10) : 10});
  dialog.value=true;
}
function closeForm() { if (!saving.value) { dialog.value=false; editing.value=null; form.api_key=''; } }
function validate() {
  if (!form.name.trim()) return '请填写模型名。';
  if (!/^https?:\/\/\S+$/.test(form.base_url.trim())) return '调用地址需以 http:// 或 https:// 开头。';
  if (!Number.isInteger(form.priority) || form.priority < 1 || form.priority > 9999) return '调用优先级须为 1–9999 的整数。';
  return '';
}
async function save() {
  if (saving.value || !writable.value) return;
  formError.value=validate(); if (formError.value) return;
  saving.value=true;
  const payload = {name:form.name.trim(),base_url:form.base_url.trim(),api_key:form.api_key.trim() || null,priority:form.priority};
  try {
    if (editing.value) await props.api.updateModelConfig(editing.value.id,payload);
    else await props.api.createModelConfig(payload);
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
async function remove(item: ModelConfig) {
  if (!writable.value) return;
  try { await ElMessageBox.confirm(`确定删除模型「${item.name}」吗？删除后不可恢复。`,'删除确认',{type:'warning',confirmButtonText:'删除',cancelButtonText:'取消'}); }
  catch { return; }
  try {
    await props.api.deleteModelConfig(item.id);
    ElMessage.success('已删除');
    void refresh();
  } catch (error) { ElMessage.error(handle(error).message); }
}
async function test(item: ModelConfig) {
  if (!writable.value || testing[item.id]) return;
  testing[item.id]=true;
  try {
    const result = await props.api.testModelConfig(item.id);
    if (!alive) return;
    items.value=items.value.map(entry => entry.id === item.id ? result.config : entry);
    if (result.ok) ElMessage.success(`${item.name}：${result.message}`);
    else ElMessage({type:'error',message:`${item.name}：${result.message}`,duration:6000});
  } catch (error) { ElMessage.error(handle(error).message); }
  finally { testing[item.id]=false; }
}
watch(writable, value => { if (!value) { dialog.value=false; editing.value=null; } });
onMounted(refresh);
onUnmounted(() => { alive=false; version++; });
defineExpose({refresh});
</script>

<template>
  <div class="erp-page" aria-label="模型配置">
    <form class="erp-query" @submit.prevent="query()">
      <label for="model-config-search">模型名</label>
      <ElInput id="model-config-search" v-model="keyword" size="small" clearable maxlength="120" placeholder="模型名或调用地址" style="width:220px" />
      <ElButton type="primary" size="small" native-type="submit"><Search :size="13" />查询</ElButton>
      <ElButton size="small" @click="query(true)"><RotateCcw :size="13" />重置</ElButton>
    </form>
    <div v-if="writable" class="erp-toolbar">
      <ElButton type="primary" size="small" @click="openForm()"><Plus :size="13" />新增</ElButton>
    </div>
    <div v-if="issue" class="erp-alert" role="alert">{{ issue.message }}<ElButton link type="primary" @click="refresh">重试</ElButton></div>
    <ElTable :data="rows" border stripe size="small" row-key="id" :empty-text="loading ? '加载中…' : '暂无数据'" class="erp-table">
      <ElTableColumn type="index" label="序号" width="56" align="center" />
      <ElTableColumn prop="name" label="模型名" min-width="180" show-overflow-tooltip />
      <ElTableColumn prop="base_url" label="调用地址" min-width="260" show-overflow-tooltip />
      <ElTableColumn label="密钥" width="120"><template #default="{row}"><span v-if="row.api_key_configured" class="mono">{{ row.api_key_hint }}</span><span v-else class="muted">未配置</span></template></ElTableColumn>
      <ElTableColumn prop="priority" label="调用优先级" width="96" align="center" />
      <ElTableColumn label="连接状态" width="110" align="center">
        <template #default="{row}">
          <ElTooltip v-if="row.last_test_at" :content="`${formatTime(row.last_test_at)}　${row.last_test_message ?? ''}`" placement="top">
            <ElTag :type="row.last_test_ok ? 'success' : 'danger'" size="small" effect="light">{{ row.last_test_ok ? '连接正常' : '连接失败' }}</ElTag>
          </ElTooltip>
          <ElTag v-else type="info" size="small" effect="plain">未测试</ElTag>
        </template>
      </ElTableColumn>
      <ElTableColumn label="更新时间" width="160"><template #default="{row}">{{ formatTime(row.updated_at) }}</template></ElTableColumn>
      <ElTableColumn label="操作" width="160" align="center" fixed="right">
        <template #default="{row}">
          <template v-if="writable">
            <ElButton link type="primary" size="small" :loading="testing[row.id]" @click="test(row as ModelConfig)">测试</ElButton>
            <ElButton link type="primary" size="small" @click="openForm(row as ModelConfig)">编辑</ElButton>
            <ElButton link type="danger" size="small" @click="remove(row as ModelConfig)">删除</ElButton>
          </template>
          <span v-else class="muted">只读</span>
        </template>
      </ElTableColumn>
    </ElTable>
    <div class="erp-footer">共 {{ rows.length }} 条</div>

    <ElDialog :model-value="dialog" :title="editing ? '编辑模型' : '新增模型'" width="520px" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" @update:model-value="value => { if (!value) closeForm(); }">
      <ElForm label-width="96px" size="default" class="erp-form" @submit.prevent="save">
        <div v-if="formError" class="erp-alert" role="alert">{{ formError }}</div>
        <ElFormItem label="模型名" required><ElInput id="model-config-name" v-model="form.name" maxlength="120" placeholder="调用时使用的模型名，如 qwen-vl-max" aria-label="模型名" /></ElFormItem>
        <ElFormItem label="调用地址" required><ElInput id="model-config-url" v-model="form.base_url" maxlength="500" placeholder="如 https://dashscope.aliyuncs.com/compatible-mode/v1" aria-label="调用地址" /></ElFormItem>
        <ElFormItem label="密钥"><ElInput id="model-config-key" v-model="form.api_key" type="password" show-password autocomplete="new-password" maxlength="1000" :placeholder="editing?.api_key_configured ? `已配置 ${editing.api_key_hint}，留空则不修改` : 'API Key，可留空'" aria-label="密钥" /></ElFormItem>
        <ElFormItem label="调用优先级" required><ElInputNumber v-model="form.priority" :min="1" :max="9999" :step="1" step-strictly controls-position="right" aria-label="调用优先级" /><span class="erp-form-tip">数字越小越优先</span></ElFormItem>
      </ElForm>
      <template #footer>
        <ElButton :disabled="saving" @click="closeForm">取消</ElButton>
        <ElButton type="primary" :loading="saving" @click="save">保存</ElButton>
      </template>
    </ElDialog>
  </div>
</template>
