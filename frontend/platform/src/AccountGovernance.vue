<script setup lang="ts">
import { computed, onUnmounted, reactive, ref, shallowRef, watch } from 'vue';
import { ElButton, ElDialog, ElTag } from 'element-plus';
import { ShieldCheck, History, ArrowRight } from 'lucide-vue-next';
import AccountSessionRevocations from './AccountSessionRevocations.vue';
import { ApiError, type PlatformApi, type Identity, type AccountDetail, type AccountChange, type AccountWrite, type AccountDecision, type AccountRole, type AccountStatus } from './api';

const props = defineProps<{api: PlatformApi; identity: Identity; accountId: string | null}>();
const emit = defineEmits<{close: []; changed: []; failure: [error: unknown]; revalidate: []}>();
const account = shallowRef<AccountDetail | null>(null);
const history = ref<AccountChange[]>([]), cursor = ref<string | null>(null);
const loading = ref(false), saving = ref(false), conflict = ref(false), uncertain = ref(false);
const issue = shallowRef<ApiError | null>(null), notice = ref('');
const form = reactive<{role: AccountRole; status: AccountStatus; reason: string}>({role:'operator',status:'active',reason:''});
const decisionChange = shallowRef<AccountChange | null>(null), decisionKind = ref<'approve' | 'reject'>('approve'), decisionReason = ref('');
type Pending = {kind: 'change'; id: string; payload: AccountWrite} | {kind: 'approve' | 'reject'; id: string; payload: AccountDecision};
let pending: Pending | null = null, generation = 0, readVersion = 0;
const canWrite = computed(() => props.identity.permissions.includes('platform.accounts.write'));
const canDecide = computed(() => props.identity.permissions.includes('platform.accounts.approve'));
const locked = computed(() => saving.value || loading.value || uncertain.value || conflict.value);
const promotion = computed(() => form.role === 'security_admin' && form.status === 'active' && !(account.value?.role === 'security_admin' && account.value?.status === 'active'));
const roleName = (v: string) => ({security_admin:'安全管理员',operator:'平台运营',evaluation_annotator:'评测标注员',evaluation_adjudicator:'评测裁决员'}[v] ?? v);
const statusName = (v: string) => v === 'active' ? '启用' : '停用';
const stateName = (v: AccountChange['state']) => ({pending:'待独立审批',applied:'已生效',approved:'已批准',rejected:'已拒绝'}[v]);
const time = (v: string) => new Date(v).toLocaleString('zh-CN',{hour12:false});
function reset() {
  generation++; readVersion++; account.value=null; history.value=[]; cursor.value=null;
  loading.value=false; saving.value=false; conflict.value=false; uncertain.value=false;
  pending=null; decisionChange.value=null; decisionReason.value=''; form.reason=''; issue.value=null; notice.value='';
}
function fail(error: unknown) {
  const value = error instanceof ApiError ? error : new ApiError(502);
  if (value.code === 'stale') return;
  issue.value=value;
  if (value.status === 401 || value.code === 'invalid_session') emit('failure',value);
  if (value.status === 403) emit('revalidate');
}
async function load(more = false) {
  const id = props.accountId;
  if (!id || saving.value) return;
  const current = generation, version = ++readVersion;
  loading.value=true; issue.value=null;
  try {
    if (more) {
      const result = await props.api.accountHistory(id,cursor.value ?? undefined);
      if (current !== generation || version !== readVersion) return;
      history.value.push(...result.items); cursor.value=result.next_cursor;
    } else {
      const [detail, changes] = await Promise.all([props.api.account(id),props.api.accountHistory(id)]);
      if (current !== generation || version !== readVersion) return;
      account.value=detail; history.value=changes.items; cursor.value=changes.next_cursor;
      form.role=detail.role; form.status=detail.status; form.reason='';
      pending=null; conflict.value=false; uncertain.value=false; decisionChange.value=null; decisionReason.value='';
    }
  } catch (error) { if (current === generation && version === readVersion) fail(error); }
  finally { if (current === generation && version === readVersion) loading.value=false; }
}
function close() { if (!saving.value) { reset(); emit('close'); } }
function choose(change: AccountChange, kind: 'approve' | 'reject') {
  if (locked.value || !canDecide.value || change.actor_id === props.identity.id || (kind === 'approve' && change.stale)) return;
  decisionChange.value=change; decisionKind.value=kind; decisionReason.value=''; issue.value=null;
}
function reasonValid(value: string) { return value.trim().length > 0 && value.trim().length <= 500 && !/[\x00-\x1f\x7f]/.test(value); }
async function submit(kind: 'change' | 'decision') {
  if (saving.value || loading.value || conflict.value || !account.value) return;
  if (!pending) {
    const reason = kind === 'change' ? form.reason : decisionReason.value;
    if (!reasonValid(reason)) { issue.value=new ApiError(422); return; }
    if (kind === 'change') {
      if (!canWrite.value) return;
      if (form.role === account.value.role && form.status === account.value.status) { issue.value=new ApiError(422); return; }
      pending={kind:'change',id:account.value.id,payload:{operation_id:crypto.randomUUID(),expected_revision:account.value.revision,role:form.role,status:form.status,reason:reason.trim()}};
    } else {
      if (!canDecide.value || !decisionChange.value || decisionChange.value.actor_id === props.identity.id || (decisionKind.value === 'approve' && decisionChange.value.stale)) return;
      pending={kind:decisionKind.value,id:decisionChange.value.id,payload:{operation_id:crypto.randomUUID(),reason:reason.trim()}};
    }
  }
  const current = generation, operation = pending;
  saving.value=true; issue.value=null; notice.value='';
  try {
    const me = await props.api.me();
    if (current !== generation) return;
    const permission = operation.kind === 'change' ? 'platform.accounts.write' : 'platform.accounts.approve';
    if (me.id !== props.identity.id || me.session_id !== props.identity.session_id || !me.permissions.includes(permission)) {
      throw new ApiError(403);
    }
    const result = operation.kind === 'change' ? await props.api.changeAccount(operation.id,operation.payload) : await props.api.decideAccount(operation.id,operation.kind,operation.payload);
    if (current !== generation) return;
    pending=null; uncertain.value=false; saving.value=false;
    notice.value=result.state === 'pending' ? '申请已提交，需由另一位安全管理员独立审批。账号权限尚未改变。' : '处理结果已保存。实际角色或状态变化会撤销该账号的全部会话。';
    await load();
    if (current === generation) emit('changed');
  } catch (error) {
    if (current !== generation) return;
    const value = error instanceof ApiError ? error : new ApiError(502);
    if (value.status === 0 || value.status >= 500) uncertain.value=true;
    else { uncertain.value=false; conflict.value=value.status === 409; pending=null; }
    fail(value);
  } finally { if (current === generation) saving.value=false; }
}
watch([() => props.accountId, () => props.identity.id, () => props.identity.session_id, () => [...props.identity.permissions].sort().join('|')], () => {
  reset(); if (props.accountId && props.identity.permissions.includes('platform.accounts.read')) void load();
}, {immediate:true});
onUnmounted(reset);
</script>

<template>
  <ElDialog :model-value="!!accountId" title="平台账号管理" width="min(880px, 96vw)" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" @update:model-value="value => { if (!value) close(); }">
    <div class="governance" :aria-busy="loading || saving">
      <div v-if="issue" class="error-box" role="alert"><strong>{{ issue.message }}</strong><small v-if="issue.requestId">请求标识：{{ issue.requestId }}</small></div>
      <p v-if="notice" class="notice" role="status">{{ notice }}</p>
      <p v-if="loading" role="status">正在核对账号与变更历史…</p>
      <p v-if="uncertain" class="governance-warning">提交结果尚未确认，输入已锁定。请原样重试，系统会复用同一操作编号。</p>
      <div v-if="conflict" class="governance-warning">草稿已保留。请核对历史并刷新账号版本，刷新将清除当前草稿。<ElButton :disabled="saving" @click="load()">刷新账号版本并清除草稿</ElButton></div>
      <ElButton v-if="!account && !loading" @click="load()">重新加载账号</ElButton>
      <template v-if="account">
        <section class="governance-summary" aria-label="账号详情">
          <div class="governance-icon"><ShieldCheck :size="24" /></div><div class="governance-heading"><h2>{{ account.email }}</h2><p>{{ roleName(account.role) }} · 当前版本 {{ account.revision }}</p></div><ElTag :type="account.status==='active' ? 'success' : 'info'">{{ statusName(account.status) }}</ElTag>
          <dl><div><dt>账号 ID</dt><dd>{{ account.id }}</dd></div><div><dt>创建时间</dt><dd>{{ time(account.created_at) }}</dd></div></dl>
        </section>
        <form v-if="canWrite" class="governance-form" @submit.prevent="submit('change')" novalidate>
          <h3>角色与账号状态</h3><p class="governance-hint">变更生效后，该账号的全部现有会话将被撤销。重新启用后需重新登录。</p>
          <fieldset :disabled="locked || !!decisionChange"><legend class="sr-only">账号变更</legend><div class="governance-fields"><label for="account-role">账号角色<select id="account-role" v-model="form.role"><option value="operator">平台运营</option><option value="evaluation_annotator">评测标注员</option><option value="evaluation_adjudicator">评测裁决员</option><option value="security_admin">安全管理员</option></select></label><label for="account-status">账号状态<select id="account-status" v-model="form.status"><option value="active">启用</option><option value="disabled">停用</option></select></label></div>
          <p v-if="promotion" class="governance-warning">授予启用的安全管理员权限需要独立审批，申请人不能自行批准。</p>
          <label for="account-reason">变更原因<textarea id="account-reason" v-model="form.reason" maxlength="500" rows="3" placeholder="说明调整原因，最多 500 字" required /></label></fieldset>
          <div class="governance-actions"><span>基于版本 {{ account.revision }} 提交</span><ElButton type="primary" native-type="submit" :loading="saving && !decisionChange" :disabled="saving || loading || conflict || !!decisionChange">{{ uncertain ? '原样重试变更' : promotion ? '提交权限申请' : '保存账号变更' }}</ElButton></div>
        </form>
        <p v-else class="governance-hint">当前权限可查询此账号及历史。账号变更需要账号管理权限。</p>
        <section aria-label="账号变更历史" class="governance-history"><div class="governance-history-title"><h3><History :size="18" />变更与审批记录</h3><ElButton size="small" :disabled="locked || !!decisionChange" @click="load()">刷新账号与历史</ElButton></div>
          <p v-if="!history.length && !loading" class="governance-empty">暂无变更记录</p>
          <article v-for="change in history" :key="change.id" class="governance-record"><header><ElTag :type="change.state==='pending' ? 'warning' : change.state==='rejected' ? 'info' : 'success'" size="small">{{ stateName(change.state) }}</ElTag><span v-if="change.stale" class="governance-stale">版本已变化，无法批准</span><time>{{ time(change.created_at) }}</time></header>
            <p class="governance-transition">{{ roleName(change.previous_role) }} / {{ statusName(change.previous_status) }}<ArrowRight :size="15" />{{ roleName(change.role) }} / {{ statusName(change.status) }}</p>
            <p class="governance-reason">{{ change.reason }}</p><dl><div><dt>申请人</dt><dd>{{ change.actor_id }}</dd></div><div><dt>基准版本</dt><dd>{{ change.expected_revision }}</dd></div><template v-if="change.decided_by"><div><dt>处理人</dt><dd>{{ change.decided_by }}</dd></div><div><dt>处理说明</dt><dd>{{ change.decision_reason }}</dd></div><div><dt>处理时间</dt><dd>{{ time(change.decided_at!) }}</dd></div></template></dl>
            <div v-if="change.state==='pending' && canDecide" class="governance-actions"><small v-if="change.actor_id===identity.id">由另一位有审批权限的管理员处理</small><template v-else><ElButton size="small" :disabled="locked || !!decisionChange" @click="choose(change,'reject')">拒绝申请</ElButton><ElButton type="primary" plain size="small" :disabled="locked || !!decisionChange || change.stale" @click="choose(change,'approve')">批准申请</ElButton></template></div>
          </article><ElButton v-if="cursor" :disabled="locked || !!decisionChange" @click="load(true)">加载更早变更</ElButton>
        </section>
        <form v-if="decisionChange" class="governance-form" aria-label="独立审批" @submit.prevent="submit('decision')" novalidate><h3>{{ decisionKind==='approve' ? '批准权限申请' : '拒绝权限申请' }}</h3><p class="governance-hint">申请编号：{{ decisionChange.id }}</p><fieldset :disabled="locked"><legend class="sr-only">审批说明</legend><label for="account-decision-reason">审批原因<textarea id="account-decision-reason" v-model="decisionReason" maxlength="500" rows="3" required /></label></fieldset><div class="governance-actions"><ElButton :disabled="saving || uncertain || conflict" @click="decisionChange=null">取消审批</ElButton><ElButton type="primary" native-type="submit" :disabled="saving || loading || conflict" :loading="saving">{{ uncertain ? '原样重试审批' : '确认' + (decisionKind==='approve' ? '批准' : '拒绝') }}</ElButton></div></form>
        <AccountSessionRevocations :api="api" :identity="identity" :account-id="account.id" @failure="error => emit('failure',error)" @revalidate="emit('revalidate')" />
      </template>
      <footer class="governance-footer"><span>写入与审批均保留审计记录</span><ElButton :disabled="saving" @click="close">关闭账号管理</ElButton></footer>
    </div>
  </ElDialog>
</template>

<style scoped>
.governance { color:#26364c; line-height:1.65; }
.governance h2,.governance h3,.governance p { margin:0; }
.governance h2 { font-size:19px; overflow-wrap:anywhere; }
.governance h3 { font-size:15px; display:flex; align-items:center; gap:8px; margin-bottom:10px; }
.governance-summary { display:flex; flex-wrap:wrap; align-items:center; gap:14px; padding:20px; background:#f3f7fc; border:1px solid #dde6f1; border-radius:12px; margin:12px 0 22px; }
.governance-icon { width:46px; height:46px; border-radius:12px; background:#e2ecfb; color:#2463af; display:grid; place-items:center; }
.governance-heading { flex:1; min-width:180px; }.governance-heading p,.governance-hint { color:#64748b; font-size:13px; }
.governance dl { margin:10px 0 0; font-size:12px; overflow-wrap:anywhere; }.governance-summary dl { width:100%; border-top:1px solid #dde6f1; padding-top:10px; }
.governance dl div { display:flex; gap:16px; padding:3px 0; }.governance dt { min-width:60px; color:#64748b; }.governance dd { margin:0; }
.governance-form { border:1px solid #dce5ef; border-radius:12px; padding:20px; margin:18px 0; }
.governance fieldset { border:0; padding:0; margin:14px 0; min-width:0; }.governance-fields { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
.governance label { display:flex; flex-direction:column; gap:6px; font-size:13px; font-weight:600; margin-top:12px; }
.governance select,.governance textarea { background:white; border:1px solid #cbd5e1; border-radius:7px; padding:9px 11px; font:inherit; color:#26364c; width:100%; box-sizing:border-box; }
.governance textarea { resize:vertical; min-height:88px; }.governance :is(select,textarea):focus-visible { outline:3px solid #94bffc; outline-offset:2px; }
.governance fieldset:disabled { opacity:.65; }.governance-warning { color:#8c5308; background:#fff8eb; border:1px solid #f5ddb5; padding:12px; border-radius:8px; font-size:13px; margin:14px 0!important; }
.governance-actions,.governance-history-title,.governance-footer { display:flex; align-items:center; flex-wrap:wrap; justify-content:space-between; gap:10px; }.governance-actions>span,.governance-footer>span { color:#64748b; font-size:12px; }
.governance-record { border-left:3px solid #d7e3f2; padding:16px; background:#f8fafc; border-radius:0 8px 8px 0; margin:12px 0; }.governance-record header { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:10px; }.governance-record time { color:#64748b; font-size:12px; margin-left:auto; }.governance-transition { display:flex; flex-wrap:wrap; align-items:center; gap:8px; font-size:13px; font-weight:600; }.governance-reason { white-space:pre-wrap; overflow-wrap:anywhere; font-size:13px; margin-top:8px!important; }.governance-record .governance-actions { justify-content:flex-end; margin-top:12px; }.governance-stale { color:#9b5c10; font-size:12px; }.governance-empty { text-align:center; padding:28px; color:#64748b; }
.governance-footer { border-top:1px solid #e2e8f0; padding-top:16px; margin-top:22px; }.sr-only { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
@media(max-width:560px) { .governance-fields { grid-template-columns:1fr; gap:0; }.governance-summary,.governance-form { padding:14px; }.governance dl div { flex-wrap:wrap; gap:4px; }.governance-record time { margin-left:0; } }
</style>
