<script setup lang="ts">
import { computed, onUnmounted, ref, shallowRef, watch } from 'vue';
import { ElButton } from 'element-plus';
import { LogOut, ShieldCheck } from 'lucide-vue-next';
import { ApiError, type AccountDecision, type Identity, type PlatformApi, type SessionRevocation } from './api';

const props = defineProps<{api: PlatformApi; identity: Identity; accountId: string}>();
const emit = defineEmits<{failure: [error: unknown]; revalidate: []}>();
const records = ref<SessionRevocation[]>([]), cursor = ref<string | null>(null), reason = ref('');
const loading = ref(false), saving = ref(false), uncertain = ref(false), conflict = ref(false);
const issue = shallowRef<ApiError | null>(null), receipt = shallowRef<SessionRevocation | null>(null);
let pending: AccountDecision | null = null, generation = 0;
const canRevoke = computed(() => props.identity.permissions.includes('platform.accounts.sessions.revoke') && props.accountId !== props.identity.id);
const locked = computed(() => loading.value || saving.value || uncertain.value || conflict.value);
const time = (value: string) => new Date(value).toLocaleString('zh-CN',{hour12:false});
function reset() {
  generation++; pending=null; records.value=[]; cursor.value=null; reason.value=''; receipt.value=null;
  loading.value=false; saving.value=false; uncertain.value=false; conflict.value=false; issue.value=null;
}
function fail(error: unknown) {
  const value = error instanceof ApiError ? error : new ApiError(502);
  if (value.code === 'stale') return;
  issue.value=value;
  if (value.status === 401 || value.status === 403) {
    reset(); issue.value=value;
    if (value.status === 401) emit('failure',value); else emit('revalidate');
  }
}
async function load(more = false) {
  if (loading.value || saving.value || uncertain.value) return;
  const current = generation, id = props.accountId;
  loading.value=true; issue.value=null;
  try {
    const page = await props.api.accountSessionRevocations(id,more ? cursor.value ?? undefined : undefined);
    if (current !== generation) return;
    records.value=more ? [...records.value,...page.items] : page.items; cursor.value=page.next_cursor;
    if (!more) { pending=null; conflict.value=false; reason.value=''; }
  } catch (error) { if (current === generation) fail(error); }
  finally { if (current === generation) loading.value=false; }
}
async function submit() {
  if (!canRevoke.value || saving.value || loading.value || conflict.value) return;
  if (!pending) {
    const value = reason.value.trim();
    if (!value || value.length > 500 || /[\x00-\x1f\x7f]/.test(reason.value)) { issue.value=new ApiError(422); return; }
    pending={operation_id:crypto.randomUUID(),reason:value};
  }
  const current = generation, operation = pending, id = props.accountId;
  saving.value=true; issue.value=null; receipt.value=null;
  try {
    const me = await props.api.me();
    if (current !== generation) return;
    if (me.id !== props.identity.id || me.session_id !== props.identity.session_id ||
      !me.permissions.includes('platform.accounts.sessions.revoke')) throw new ApiError(403);
    const result = await props.api.revokeAccountSessions(id,operation);
    if (current !== generation) return;
    if (result.actor_id !== props.identity.id) throw new ApiError(502);
    receipt.value=result; pending=null; uncertain.value=false; reason.value='';
    records.value=[result,...records.value.filter(item => item.id !== result.id)];
  } catch (error) {
    if (current !== generation) return;
    const value = error instanceof ApiError ? error : new ApiError(502);
    if (value.status === 0 || value.status >= 500) uncertain.value=true;
    else { pending=null; uncertain.value=false; conflict.value=value.status === 409; }
    fail(value);
  } finally { if (current === generation) saving.value=false; }
}
watch([() => props.accountId, () => props.identity.id, () => props.identity.session_id,
  () => [...props.identity.permissions].sort().join('|')], () => {
  reset(); if (props.identity.permissions.includes('platform.accounts.read')) void load();
}, {immediate:true});
onUnmounted(reset);
</script>

<template>
  <section class="session-security" aria-label="账号会话安全" :aria-busy="loading || saving">
    <header><span class="security-icon"><ShieldCheck :size="20" /></span><div><h3>会话安全</h3><p>撤销此账号的全部已有登录会话，保留账号角色和启停状态。</p></div></header>
    <div v-if="issue" class="error-box" role="alert"><strong>{{ issue.message }}</strong><small v-if="issue.requestId">请求标识：{{ issue.requestId }}</small></div>
    <p v-if="receipt" class="receipt" role="status">撤销操作已完成，已撤销有效会话 {{ receipt.revoked_count }} 个。新登录不受同一操作的重试影响。</p>
    <form v-if="canRevoke" aria-label="撤销账号会话" @submit.prevent="submit" novalidate>
      <fieldset :disabled="locked"><label for="session-revoke-reason">撤销原因<textarea id="session-revoke-reason" v-model="reason" rows="3" maxlength="500" placeholder="填写异常情况或人员变动原因，最多 500 字" required /></label></fieldset>
      <p class="impact">提交后，目标账号需重新登录；该操作会保留不可修改的审计回执。</p>
      <p v-if="uncertain" class="warning" role="status">操作结果尚未确认，原因与操作编号已锁定。请原样重试以核对回执。</p>
      <div v-if="conflict" class="warning">操作编号与历史记录冲突，请核对记录后刷新。<ElButton :disabled="saving" @click="load()">核对历史并清除草稿</ElButton></div>
      <ElButton type="danger" plain native-type="submit" :loading="saving" :disabled="saving || loading || conflict"><LogOut :size="15" />{{ uncertain ? '原样重试撤销' : '撤销全部会话' }}</ElButton>
    </form>
    <p v-else class="impact">{{ accountId === identity.id ? '本人请使用平台右上角的“退出全部会话”。' : '当前账号仅可查看撤销记录。' }}</p>
    <div class="history-heading"><h4>撤销记录</h4><ElButton size="small" :disabled="locked" @click="load()">刷新撤销记录</ElButton></div>
    <p v-if="loading" role="status">正在加载撤销记录…</p><p v-else-if="!records.length" class="empty">暂无独立撤销记录</p>
    <article v-for="item in records" :key="item.id" class="record">
      <div class="record-title"><strong>已撤销有效会话 {{ item.revoked_count }} 个</strong><time>{{ time(item.created_at) }}</time></div>
      <p class="reason">{{ item.reason }}</p><dl><div><dt>操作编号</dt><dd>{{ item.operation_id }}</dd></div><div><dt>操作者</dt><dd>{{ item.actor_id }}</dd></div></dl>
    </article>
    <ElButton v-if="cursor" :disabled="locked" @click="load(true)">加载更早撤销记录</ElButton>
  </section>
</template>

<style scoped>
.session-security { border:1px solid #dce5ef; border-radius:12px; padding:20px; margin-top:22px; color:#26364c; }
header { display:flex; align-items:center; gap:12px; }h3,h4,p { margin:0; }h3 { font-size:15px; }h4 { font-size:14px; }
header p,.impact { color:#64748b; font-size:13px; margin-top:5px; }.security-icon { display:grid; place-items:center; width:38px; height:38px; border-radius:10px; background:#eef3fa; color:#2463af; flex-shrink:0; }
fieldset { border:0; padding:0; margin:16px 0 8px; min-width:0; }label { display:flex; flex-direction:column; gap:7px; font-size:13px; font-weight:600; }
textarea { box-sizing:border-box; width:100%; padding:10px; border:1px solid #cbd5e1; border-radius:7px; font:inherit; color:inherit; resize:vertical; }textarea:focus-visible { outline:3px solid #94bffc; outline-offset:2px; }fieldset:disabled { opacity:.65; }
form>.el-button { margin-top:14px; }form .el-button svg { margin-right:7px; }.receipt,.warning { padding:12px; border-radius:8px; margin-top:14px; font-size:13px; }.receipt { background:#edf8f2; color:#236343; border:1px solid #c3e4d0; }.warning { background:#fff8eb; color:#8c5308; border:1px solid #f5ddb5; }
.history-heading,.record-title { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:8px; }.history-heading { border-top:1px solid #e2e8f0; margin-top:20px; padding-top:16px; }.empty { padding:22px; text-align:center; color:#64748b; font-size:13px; }.record { border-left:3px solid #d7e3f2; padding:14px; background:#f8fafc; border-radius:0 8px 8px 0; margin-top:12px; font-size:13px; }.record time { color:#64748b; font-size:12px; }.reason { white-space:pre-wrap; overflow-wrap:anywhere; margin-top:8px; }dl { margin:10px 0 0; font-size:12px; }dl div { display:flex; gap:12px; padding:2px 0; }dt { color:#64748b; flex-shrink:0; }dd { margin:0; overflow-wrap:anywhere; }
@media(max-width:560px) { .session-security { padding:14px; }dl div { flex-wrap:wrap; gap:4px; } }
</style>
