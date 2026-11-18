<script setup lang="ts">
import TextbookProcessing from './TextbookProcessing.vue';
import { computed, onMounted, onUnmounted, ref, shallowRef, watch } from 'vue';
import { ElButton, ElTag } from 'element-plus';
import { FileText, ShieldCheck, Upload } from 'lucide-vue-next';
import { ApiError, type Identity, type PlatformApi, type TextbookEntry, type TextbookDocument, type TextbookUploadIntent } from './api';

const props = defineProps<{api: PlatformApi; identity: Identity; edition: TextbookEntry}>();
const emit = defineEmits<{failure: [error: unknown]}>();
const writable = computed(() => props.identity.permissions.includes('platform.textbooks.write'));
const eligible = computed(() => writable.value && props.edition.availability === 'rights_approved' && props.edition.rights?.scopes.includes('storage'));
const items = ref<TextbookDocument[]>([]), cursor = ref<string | null>(null), loading = ref(false), busy = ref(false);
const issue = ref(''), uploadIssue = ref(''), notice = ref(''), stage = ref(''), reason = ref('');
const file = shallowRef<File | null>(null), input = ref<HTMLInputElement | null>(null), uncertain = ref(false);
let alive = true, version = 0, uploadVersion = 0;
let pending: TextbookUploadIntent | null = null, receipt: TextbookDocument | null = null;
const time = (value: string) => new Date(value).toLocaleString('zh-CN',{hour12:false});
const size = (bytes: number) => `${(bytes / 1024 / 1024).toFixed(2)} MiB`;
function errorText(error: unknown) { return error instanceof ApiError ? `${error.message}${error.requestId ? `（${error.requestId}）` : ''}` : '文件处理失败，请重新选择文件后重试。'; }
function denied(error: unknown) {
  if (error instanceof ApiError && [401,403].includes(error.status)) { clear(); emit('failure',error); }
}
function clear() {
  uploadVersion++; busy.value = false; stage.value = '';
  file.value = null; reason.value = ''; pending = null; receipt = null; uncertain.value = false;
  if (input.value) input.value.value = '';
}
function choose(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0] ?? null;
  uploadIssue.value = ''; notice.value = '';
}
async function load(more = false) {
  const request = ++version; loading.value = true; issue.value = '';
  try {
    const value = await props.api.textbookDocuments(props.edition.id,more ? cursor.value ?? undefined : undefined);
    if (!alive || request !== version) return;
    items.value = more ? [...items.value,...value.items.filter(item => !items.value.some(old => old.id === item.id))] : value.items;
    cursor.value = value.next_cursor;
  } catch (error) {
    if (!alive || request !== version) return;
    issue.value = errorText(error); denied(error);
  } finally { if (alive && request === version) loading.value = false; }
}
async function send(existing?: TextbookDocument) {
  if (busy.value || !eligible.value) return;
  uploadIssue.value = ''; notice.value = '';
  const selected = file.value;
  if (!selected || selected.size < 1 || selected.size > 52428800 || selected.name.length > 160 || selected.name.trim() !== selected.name || /[\x00-\x1f\x7f/\\]/.test(selected.name) || !/\.pdf$/i.test(selected.name)) {
    uploadIssue.value = '请选择不超过 50 MiB 的非空 PDF，文件名不能含路径或控制字符。'; return;
  }
  if (!pending && !existing && (!reason.value.trim() || reason.value.trim().length > 500 || /[\x00-\x1f\x7f]/.test(reason.value))) {
    uploadIssue.value = '请填写 1–500 字的接收原因，不能含控制字符。'; return;
  }
  busy.value = true;
  const request = ++uploadVersion;
  const current = () => alive && request === uploadVersion && eligible.value;
  try {
    if (!pending && !receipt) {
      stage.value = '正在校验文件并计算摘要';
      const bytes = await selected.arrayBuffer();
      if (!current()) return;
      if (new TextDecoder().decode(bytes.slice(0,5)) !== '%PDF-') { uploadIssue.value = '文件缺少 PDF 签名，请选择正确的文件。'; return; }
      const hash = await crypto.subtle.digest('SHA-256',bytes);
      if (!current()) return;
      const sha256 = Array.from(new Uint8Array(hash),value => value.toString(16).padStart(2,'0')).join('');
      if (existing) {
        if (existing.filename !== selected.name || existing.size_bytes !== selected.size || existing.sha256 !== sha256) {
          uploadIssue.value = '所选文件与原申请不一致，请选择原文件。'; return;
        }
        receipt = existing;
      } else pending = {operation_id:crypto.randomUUID(),expected_revision:props.edition.revision,filename:selected.name,size_bytes:selected.size,sha256,reason:reason.value.trim()};
    }
    if (!receipt && pending) {
      stage.value = '正在申请上传凭证';
      const value = await props.api.requestTextbookUpload(props.edition.id,pending);
      if (!current()) return;
      receipt = value;
    }
    if (!current()) return;
    if (!receipt) throw new ApiError(502);
    if (receipt.status !== 'quarantined') {
      stage.value = '正在上传到私有隔离区';
      await props.api.uploadTextbook(props.edition.id,receipt.id,selected);
      if (!current()) return;
    }
    clear(); notice.value = 'PDF 已接收并隔离，等待扫描；尚未生成 TXT，也未开放 AI 检索。';
    await load();
  } catch (error) {
    if (!current()) return;
    uploadIssue.value = errorText(error);
    uncertain.value = (pending !== null || receipt !== null) && (!(error instanceof ApiError) || ![401,403,409,413,422].includes(error.status));
    if (!uncertain.value) { pending = null; receipt = null; }
    denied(error);
  } finally { if (alive && request === uploadVersion) {busy.value = false; stage.value = '';} }
}
watch(eligible,value => { if (!value) clear(); });
onMounted(() => load());
onUnmounted(() => {alive = false; version++; clear();});
</script>

<template>
  <section class="textbook-documents" aria-label="教材 PDF 文件">
    <div class="document-heading"><div><h3><FileText :size="19" aria-hidden="true" />教材 PDF 文件</h3><p class="inline-note">接收原文件，保留权利版本与摘要，供后续处理追溯。</p></div><ElTag type="warning" effect="plain">隔离接收阶段</ElTag></div>
    <div class="document-boundary"><ShieldCheck :size="20" aria-hidden="true" /><p>接收凭证仅证明文件大小、摘要及 PDF 签名通过校验；扫描证据和提取草稿请在处理任务中查看。原文件不提供下载，草稿未经校核发布，不可用于 AI 检索。</p></div>
    <form v-if="eligible" class="document-form" @submit.prevent="send()">
      <fieldset :disabled="busy || uncertain"><legend>接收授权 PDF</legend>
        <label :for="`pdf-file-${edition.id}`">教材 PDF（最大 50 MiB）<input :id="`pdf-file-${edition.id}`" ref="input" type="file" accept=".pdf,application/pdf" @change="choose" /></label>
        <p v-if="file" class="inline-note">{{ file.name }} · {{ size(file.size) }}</p>
        <label :for="`pdf-reason-${edition.id}`">PDF 接收原因<input :id="`pdf-reason-${edition.id}`" v-model="reason" maxlength="500" placeholder="例如：接收已签约教材原文件" /></label>
      </fieldset>
      <p v-if="uploadIssue" class="error-box" role="alert">{{ uploadIssue }}</p>
      <p v-if="uncertain" class="model-warning">接收结果未确认，文件与原因已锁定。重试将复用原申请；刷新页面后可在待上传记录中选择原文件继续上传。</p>
      <p v-if="notice" role="status" class="document-success">{{ notice }}</p>
      <div class="document-actions"><ElButton type="primary" native-type="submit" :loading="busy" :disabled="busy"><Upload v-if="!busy" :size="16" aria-hidden="true" />{{ busy ? stage : uncertain ? '重试原上传' : '接收 PDF' }}</ElButton></div>
    </form>
    <p v-else class="inline-note">{{ writable ? '仅已批准、当前有效且包含文件存储用途的教材可接收 PDF，请核对权利信息。' : '当前账号可查看文件记录，没有接收文件权限。' }}</p>
    <div class="document-list" :aria-busy="loading">
      <div class="document-heading"><h4>文件接收记录</h4><ElButton :disabled="loading || busy" @click="load()">刷新文件记录</ElButton></div>
      <div v-if="issue" class="error-box" role="alert">{{ issue }} <ElButton :disabled="loading" @click="load()">重试文件列表</ElButton></div>
      <p v-if="loading" role="status">正在读取文件记录…</p><p v-else-if="!items.length && !issue" class="inline-note">暂无 PDF 接收记录。</p>
      <article v-for="item in items" :key="item.id" class="document-record">
        <div class="document-heading"><strong>{{ item.filename }}</strong><ElTag :type="item.status === 'quarantined' ? 'warning' : 'info'">{{ item.status === 'quarantined' ? '已隔离接收' : '待上传' }}</ElTag></div>
        <p>{{ size(item.size_bytes) }} · 权利版本 v{{ item.edition_revision }} · {{ time(item.created_at) }}</p>
        <p>{{ item.reason }}</p><details><summary>查看接收凭证与摘要</summary><dl><dt>文件编号</dt><dd>{{ item.id }}</dd><dt>SHA-256</dt><dd>{{ item.sha256 }}</dd><dt>上传人</dt><dd>{{ item.created_by }}</dd><dt>{{ item.received_at ? '接收时间' : '凭证到期' }}</dt><dd>{{ time(item.received_at ?? item.expires_at) }}</dd></dl></details>
        <ElButton v-if="eligible && !uncertain && item.status === 'awaiting_upload' && item.created_by === identity.id && item.edition_revision === edition.revision && Date.parse(item.expires_at) > Date.now()" :disabled="busy || !file" @click="send(item)">使用所选原文件继续上传</ElButton>
        <TextbookProcessing v-if="item.status === 'quarantined'" :api="api" :identity="identity" :edition="edition" :document="item" @failure="emit('failure',$event)" />
      </article>
      <ElButton v-if="cursor" :disabled="loading || busy" @click="load(true)">加载更早文件</ElButton>
    </div>
  </section>
</template>

<style scoped>
.textbook-documents{margin:24px 0;border:1px solid var(--el-border-color-light);border-radius:12px;overflow:hidden;background:var(--el-bg-color)}
.document-heading{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}.textbook-documents>.document-heading{padding:20px 20px 12px}.document-heading h3{display:flex;align-items:center;gap:8px;margin:0}.document-heading h4{margin:0}.document-heading p{margin:7px 0 0}
.document-boundary{display:flex;align-items:flex-start;gap:10px;background:var(--el-fill-color-light);padding:12px 20px;color:var(--el-text-color-secondary);font-size:13px}.document-boundary svg{flex-shrink:0}.document-boundary p{margin:0;line-height:1.7}
.document-form,.document-list{padding:18px 20px}.document-form{border-bottom:1px solid var(--el-border-color-light)}fieldset{border:0;padding:0;margin:0}legend{font-weight:600;margin-bottom:12px}label{display:grid;gap:8px;margin-bottom:14px;font-size:13px}input{width:100%;box-sizing:border-box;border:1px solid var(--el-border-color);border-radius:6px;padding:9px 12px;background:var(--el-fill-color-blank);color:inherit}input:focus-visible{outline:2px solid var(--el-color-primary);outline-offset:2px}input[type=file]{padding:10px}input[type=file]::file-selector-button{padding:6px 10px;margin-right:12px;border:0;border-radius:4px;background:var(--el-fill-color);color:inherit;cursor:pointer}.document-actions{display:flex;gap:10px;margin-top:14px}.document-actions svg{margin-right:7px}.document-success{color:var(--el-color-success);font-size:13px}.document-record{padding:16px 0;border-bottom:1px solid var(--el-border-color-light)}.document-record:last-of-type{border:0}.document-record p,.document-record details{font-size:13px;line-height:1.7;color:var(--el-text-color-secondary)}.document-record strong,.document-record dd{overflow-wrap:anywhere}.document-record summary{cursor:pointer}.document-record dl{display:grid;grid-template-columns:80px minmax(0,1fr);gap:8px}.document-record dd{margin:0}.textbook-documents>.inline-note{padding:0 20px}
</style>
