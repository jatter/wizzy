<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, shallowRef } from 'vue';
import { ElButton, ElInput, ElTag, ElTable, ElTableColumn, ElDialog } from 'element-plus';
import 'element-plus/es/components/button/style/css';
import 'element-plus/es/components/input/style/css';
import 'element-plus/es/components/tag/style/css';
import 'element-plus/es/components/table/style/css';
import 'element-plus/es/components/dialog/style/css';
import { ArrowRight, ChevronDown, ChevronLeft, ChevronRight, Fingerprint, KeyRound, LogOut, RefreshCw, ShieldCheck, ScrollText, LockKeyhole, Settings, Cpu, BookOpen, Database, X } from 'lucide-vue-next';
import ModelConfig from './ModelConfig.vue';
import PromptConfig from './PromptConfig.vue';
import EvaluationDatasets from './EvaluationDatasets.vue';
import TextbookCatalog from './TextbookCatalog.vue';
import AccountGovernance from './AccountGovernance.vue';
import PermissionScope from './PermissionScope.vue';
import { permissionBrief } from './permissions';
import { ApiError, PlatformApi, type Account, type AuditEvent, type Identity, type Role } from './api';

type View = 'session' | 'accounts' | 'roles' | 'audit' | 'models' | 'prompts' | 'textbooks' | 'evaluation';
const modelConfig = ref<InstanceType<typeof ModelConfig> | null>(null);
const promptConfig = ref<InstanceType<typeof PromptConfig> | null>(null);
const textbookCatalog = ref<InstanceType<typeof TextbookCatalog> | null>(null);
const evaluationDatasets = ref<InstanceType<typeof EvaluationDatasets> | null>(null);
const api = new PlatformApi();
const platformHome = import.meta.env.BASE_URL;
const identity = shallowRef<Identity | null>(null);
const view = ref<View>('session');
const tabs = ref<View[]>(['session']);
const expanded = reactive<Record<string, boolean>>({system:true,'model-group':true});
const selectedAccount = ref<string | null>(null);
const email = ref('');
const password = ref('');
const captcha = ref('');
const captchaId = ref('');
const captchaImage = ref('');
const authenticating = ref(false);
const loading = ref(false);
const notice = ref('');
const error = shallowRef<ApiError | null>(null);
const accounts = ref<Account[]>([]);
const events = ref<AuditEvent[]>([]);
const roles = ref<Role[]>([]);
const selectedRoleName = ref<string | null>(null);
const cursors = ref<(string | undefined)[]>([undefined]);
const pageIndex = ref(0);
const nextCursor = ref<string | null>(null);
const confirmAll = ref(false);
const narrow = ref(false);
let requestVersion = 0;
let narrowQuery: MediaQueryList | undefined;
function syncNarrow() { narrow.value = !!narrowQuery?.matches; }
let expiryTimer: ReturnType<typeof setInterval>;
const roleName = (name: string) => ({security_admin:'安全管理员',operator:'平台运营',evaluation_annotator:'评测标注员',evaluation_adjudicator:'评测裁决员'}[name] ?? name);
const actionName = (name: string) => ({'login':'登录成功','logout':'退出登录','logout_all':'退出全部会话','access.denied':'权限校验拒绝','accounts.change':'账号变更申请','accounts.approve':'批准账号变更','accounts.reject':'拒绝账号变更','model_configs.create':'新增模型','model_configs.update':'编辑模型','model_configs.delete':'删除模型','model_configs.test':'模型连接测试','grading_prompts.create':'新增提示词','grading_prompts.update':'编辑提示词','grading_prompts.delete':'删除提示词'}[name] ?? name);
const formatTime = (value: string) => new Date(value).toLocaleString('zh-CN', {hour12:false});
const can = (permission: string) => !!identity.value?.permissions.includes(permission);
const navigation = computed(() => [
  {key:'session' as View,label:'账号与会话',group:'system',show:true},
  {key:'accounts' as View,label:'平台账号',group:'system',show:can('platform.accounts.read')},
  {key:'roles' as View,label:'角色权限',group:'system',show:true},
  {key:'audit' as View,label:'安全审计',group:'system',show:can('platform.audit.read')},
  {key:'models' as View,label:'模型配置',group:'models',show:can('platform.models.read')},
  {key:'prompts' as View,label:'提示词管理',group:'models',show:can('platform.models.read')},
  {key:'textbooks' as View,label:'教材管理',icon:BookOpen,show:can('platform.textbooks.read')},
  {key:'evaluation' as View,label:'评测数据集',icon:Database,show:can('platform.evaluation.read')},
].filter(item => item.show));
const menu = computed(() => {
  const system = navigation.value.filter(item => item.group === 'system');
  const models = navigation.value.filter(item => item.group === 'models');
  return [
    {key:'system',label:'系统管理',icon:Settings,children:system},
    ...(models.length ? [{key:'model-group',label:'模型管理',icon:Cpu,children:models}] : []),
    ...navigation.value.filter(item => !item.group).map(item => ({...item,children:undefined})),
  ];
});
const labelOf = (key: View) => navigation.value.find(item => item.key === key)?.label ?? '账号与会话';
const title = computed(() => labelOf(view.value));
const selectedRole = computed(() => roles.value.find(item => item.name === selectedRoleName.value) ?? null);
function pickRole(row: { name?: string }) {
  if (row.name) selectedRoleName.value = row.name;
}
function clearRows() { accounts.value=[]; events.value=[]; roles.value=[]; nextCursor.value=null; }
function clearUi(message = '') {
  requestVersion++; selectedAccount.value=null;
  identity.value=null; view.value='session'; tabs.value=['session']; loading.value=false; confirmAll.value=false;
  clearRows(); error.value=null; notice.value=message;
  cursors.value=[undefined]; pageIndex.value=0;
  void loadCaptcha();
}
function handleError(cause: unknown) {
  const issue = cause instanceof ApiError ? cause : new ApiError(502);
  if (issue.code === 'stale') return;
  if (issue.status === 401) { api.clear(); clearUi('会话已失效，请重新登录。'); }
  else {
    if (issue.code === 'invalid_session') clearUi();
    error.value=issue;
  }
}
async function loadCaptcha() {
  try {
    const challenge = await api.captcha();
    captchaId.value = challenge.captcha_id;
    captchaImage.value = challenge.image;
    captcha.value = '';
  } catch {
    captchaId.value = '';
    captchaImage.value = '';
  }
}
async function signIn() {
  if (authenticating.value) return;
  authenticating.value=true; error.value=null; notice.value='';
  try {
    const me = await api.login({email:email.value.trim(),password:password.value,captcha_id:captchaId.value,captcha:captcha.value});
    identity.value=me; view.value='session'; tabs.value=['session']; email.value='';
  } catch (cause) {
    if (!(cause instanceof ApiError && cause.code === 'stale')) error.value=cause instanceof ApiError ? cause : new ApiError(502);
    void loadCaptcha();
  } finally { password.value=''; captcha.value=''; authenticating.value=false; }
}
async function load() {
  if (!identity.value) return;
  const version = ++requestVersion;
  loading.value=true; error.value=null; clearRows();
  try {
    const me = await api.me();
    if (version !== requestVersion) return;
    identity.value=me;
    tabs.value=tabs.value.filter(key => navigation.value.some(item => item.key === key));
    if (!tabs.value.includes('session')) tabs.value.unshift('session');
    if (!navigation.value.some(item => item.key === view.value)) {
      selectedAccount.value=null; view.value='session'; cursors.value=[undefined]; pageIndex.value=0;
      notice.value='账号权限已更新，已返回当前会话。';
    }
    const cursor = cursors.value[pageIndex.value];
    if (view.value === 'accounts') {
      const result = await api.accounts(cursor);
      if (version !== requestVersion) return;
      accounts.value=result.items; nextCursor.value=result.next_cursor;
    } else if (view.value === 'audit') {
      const result = await api.audit(cursor);
      if (version !== requestVersion) return;
      events.value=result.items; nextCursor.value=result.next_cursor;
    } else if (view.value === 'roles') {
      const result = await api.roles();
      if (version !== requestVersion) return;
      roles.value=result.items;
      if (!result.items.some(item => item.name === selectedRoleName.value)) {
        selectedRoleName.value = result.items.find(item => item.name === me.role)?.name ?? result.items[0]?.name ?? null;
      }
    }
  } catch (cause) { if (version === requestVersion) handleError(cause); }
  finally { if (version === requestVersion) loading.value=false; }
}
function navigate(next: View) {
  if (!tabs.value.includes(next)) tabs.value.push(next);
  selectedAccount.value=null; view.value=next; notice.value=''; cursors.value=[undefined]; pageIndex.value=0; void load();
}
function closeTab(key: View) {
  if (key === 'session') return;
  const index = tabs.value.indexOf(key);
  tabs.value=tabs.value.filter(item => item !== key);
  if (view.value === key) navigate(tabs.value[Math.max(0,index-1)] ?? 'session');
}
function refresh() { if (view.value === 'evaluation') void evaluationDatasets.value?.refresh(); if (view.value === 'textbooks') void textbookCatalog.value?.refresh(); if (view.value === 'models') void modelConfig.value?.refresh(); if (view.value === 'prompts') void promptConfig.value?.refresh(); cursors.value=[undefined]; pageIndex.value=0; void load(); }
function turnPage(direction: number) {
  if (loading.value || (direction < 0 && !pageIndex.value) || (direction > 0 && !nextCursor.value)) return;
  if (direction > 0) cursors.value[pageIndex.value+1]=nextCursor.value!;
  pageIndex.value+=direction; void load();
}
async function signOut(all = false) {
  if (authenticating.value) return;
  authenticating.value=true; clearUi();
  try { await api.logout(all); notice.value=all ? '已退出全部平台会话。' : '已退出当前平台会话。'; }
  catch { notice.value='本地会话已清除，服务端撤销未确认。请检查网络，重新登录后退出全部会话；必要时联系安全管理员。'; }
  finally { authenticating.value=false; }
}
function revalidate() { if (identity.value) void load(); }
function visible() { if (document.visibilityState === 'visible') revalidate(); }
onMounted(() => {
  narrowQuery = window.matchMedia('(max-width:640px)');
  syncNarrow();
  narrowQuery.addEventListener('change', syncNarrow);
  void loadCaptcha();
  window.addEventListener('focus',revalidate);
  document.addEventListener('visibilitychange',visible);
  expiryTimer=setInterval(() => {
    if (identity.value && Date.now() >= Date.parse(identity.value.expires_at)) {
      api.clear(); clearUi('会话已到期，请重新登录。');
    }
  },1000);
});
onUnmounted(() => {
  narrowQuery?.removeEventListener('change', syncNarrow);
  api.clear(); clearInterval(expiryTimer);
  window.removeEventListener('focus',revalidate); document.removeEventListener('visibilitychange',visible);
});
</script>

<template>
  <div v-if="!identity" class="login-layout">
    <section class="login-story" aria-label="小知平台介绍">
      <a class="brand" :href="platformHome" aria-label="小知平台首页"><span class="brand-mark">知</span><span>小知 <small>WIZZY</small></span></a>
      <div class="story-main">
        <span class="eyebrow"><ShieldCheck :size="16" /> 平台安全工作区</span>
        <h1>让每一次学习，<br>都值得被信任。</h1>
        <p>从身份验证到权限核对，<br>为小知的日常运营建立清晰、安全的边界。</p>
        <div class="trust-map" aria-hidden="true"><div class="trust-center"><ShieldCheck :size="36" /><span>小知平台</span></div><span class="trust-node node-one"><Fingerprint :size="18" /> 身份验证</span><span class="trust-node node-two"><KeyRound :size="18" /> 角色权限</span><span class="trust-node node-three"><ScrollText :size="18" /> 安全审计</span></div>
      </div>
      <div class="story-foot"><LockKeyhole :size="14" /> 独立平台身份 · 验证码登录 · 最小权限</div>
    </section>
    <main class="login-panel">
      <div class="login-card">
        <div class="section-icon"><Fingerprint :size="24" /></div>
        <span class="eyebrow muted">小知 · 平台管理</span>
        <h2>登录平台工作台</h2>
        <p class="subtext">使用平台邮箱、密码和验证码登录。<br>家庭端账号不能用于此工作区。</p>
        <div v-if="notice" class="notice" role="status">{{ notice }}</div>
        <div v-if="error" class="error-box" role="alert"><strong>{{ error.message }}</strong><small v-if="error.requestId">请求标识：{{ error.requestId }}</small></div>
        <form @submit.prevent="signIn">
          <label for="email">平台邮箱</label>
          <ElInput id="email" v-model="email" type="email" autocomplete="username" required maxlength="320" placeholder="请输入平台邮箱" :disabled="authenticating" />
          <label for="password">登录密码</label>
          <ElInput id="password" v-model="password" type="password" autocomplete="current-password" required maxlength="256" placeholder="请输入登录密码" :disabled="authenticating" />
          <label for="captcha">验证码 <span>图中 4 位字母或数字</span></label>
          <div class="captcha-row">
            <ElInput id="captcha" v-model="captcha" autocomplete="off" spellcheck="false" pattern="[A-Za-z0-9]{4}" minlength="4" maxlength="4" required placeholder="请输入验证码" :disabled="authenticating" />
            <button type="button" class="captcha-refresh" :disabled="authenticating" aria-label="刷新图片" @click="loadCaptcha">
              <img v-if="captchaImage" :src="captchaImage" alt="" width="140" height="48" />
              <span v-else>点击刷新</span>
            </button>
          </div>
          <ElButton class="login-button" type="primary" native-type="submit" :loading="authenticating">登录工作台 <ArrowRight :size="17" /></ElButton>
        </form>
        <p class="login-help"><LockKeyhole :size="14" /> 会话仅保留于当前页面，刷新后需重新登录。</p>
        <div class="login-contact">无法登录？请联系组织安全管理员。<br>不要把密码或验证码发给他人。</div>
        <nav class="sibling-ends" aria-label="家庭学习入口"><a href="/">进入家庭学习空间</a></nav>
      </div>
      <footer>小知平台管理 <span>·</span> 仅限已授权人员使用</footer>
    </main>
  </div>
  <div v-else class="erp-shell">
    <a class="skip-link" href="#main-content">跳转至主要内容</a>
    <header class="erp-header">
      <div class="erp-header-left">
        <a class="erp-brand" :href="platformHome" aria-label="小知平台首页"><span class="erp-brand-mark">知</span><strong>小知</strong></a>
        <div class="erp-crumb">平台管理 <ChevronRight :size="14" /><strong>{{ title }}</strong></div>
      </div>
      <div class="erp-header-right">
        <ElTag type="success" effect="plain" size="small">{{ roleName(identity.role) }}</ElTag>
        <ElButton size="small" :disabled="loading" @click="refresh"><RefreshCw :size="13" :class="{spinning:loading}" />刷新</ElButton>
        <button type="button" class="erp-header-btn" @click="signOut(false)"><LogOut :size="14" />退出登录</button>
      </div>
    </header>
    <div class="erp-main">
      <aside class="erp-sidebar">
        <nav aria-label="平台导航">
          <template v-for="entry in menu" :key="entry.key">
            <template v-if="entry.children">
              <button type="button" class="erp-menu-group" :aria-expanded="!!expanded[entry.key]" @click="expanded[entry.key]=!expanded[entry.key]"><component :is="entry.icon" :size="15" /><span>{{ entry.label }}</span><ChevronDown :size="14" class="erp-caret" :class="{collapsed:!expanded[entry.key]}" /></button>
              <div v-show="expanded[entry.key]" class="erp-submenu">
                <button v-for="item in entry.children" :key="item.key" type="button" class="erp-menu-item" :class="{active:view===item.key}" :aria-current="view===item.key ? 'page' : undefined" @click="navigate(item.key)">{{ item.label }}</button>
              </div>
            </template>
            <button v-else type="button" class="erp-menu-item erp-menu-top" :class="{active:view===entry.key}" :aria-current="view===entry.key ? 'page' : undefined" @click="navigate(entry.key as View)"><component :is="entry.icon" :size="15" /><span>{{ entry.label }}</span></button>
          </template>
        </nav>
      </aside>
      <div class="erp-body">
        <div class="erp-tabs" role="tablist" aria-label="已打开页面">
          <div v-for="key in tabs" :key="key" class="erp-tab" :class="{active:view===key}">
            <button type="button" role="tab" :aria-selected="view===key" @click="view===key ? undefined : navigate(key)">{{ labelOf(key) }}</button>
            <button v-if="key!=='session'" type="button" class="erp-tab-close" :aria-label="`关闭${labelOf(key)}`" @click="closeTab(key)"><X :size="12" /></button>
          </div>
        </div>
        <main id="main-content" class="erp-content" tabindex="-1" :aria-busy="loading">
          <h1 class="sr-only">{{ title }}</h1>
          <div v-if="notice" class="erp-alert erp-alert-info" role="status">{{ notice }}</div>
          <div v-if="error" class="erp-alert" role="alert"><strong>{{ error.message }}</strong><small v-if="error.requestId">请求标识：{{ error.requestId }}</small><ElButton link type="primary" @click="load">重试</ElButton></div>
          <AccountGovernance v-if="view==='accounts'" :api="api" :identity="identity" :account-id="selectedAccount" @close="selectedAccount=null" @failure="handleError" @changed="load" @revalidate="load" />
          <ModelConfig v-if="view==='models'" ref="modelConfig" :api="api" :identity="identity" @failure="handleError" @revalidate="load" />
          <PromptConfig v-else-if="view==='prompts'" ref="promptConfig" :api="api" :identity="identity" @failure="handleError" @revalidate="load" />
          <EvaluationDatasets v-else-if="view==='evaluation'" ref="evaluationDatasets" :api="api" :identity="identity" @failure="handleError" @revalidate="load" />
          <TextbookCatalog v-else-if="view==='textbooks'" ref="textbookCatalog" :api="api" :identity="identity" @failure="handleError" @revalidate="load" />
          <template v-else-if="!error">
            <div v-if="view==='session'" class="erp-page">
              <div class="erp-toolbar">
                <ElButton size="small" @click="confirmAll=true"><LogOut :size="13" />退出全部会话</ElButton>
              </div>
              <table class="erp-desc">
                <caption>当前平台账号</caption>
                <tbody>
                  <tr><th>平台邮箱</th><td>{{ identity.email }}</td><th>角色</th><td>{{ roleName(identity.role) }} <ElTag type="success" size="small">已验证</ElTag></td></tr>
                  <tr><th>账号 ID</th><td class="mono">{{ identity.id }}</td><th>身份范围</th><td>平台后台（独立于家庭身份）</td></tr>
                  <tr><th>会话 ID</th><td class="mono">{{ identity.session_id }}</td><th>会话到期</th><td>{{ formatTime(identity.expires_at) }}</td></tr>
                  <tr><th>验证方式</th><td>登录密码 + 图形验证码</td><th>凭据保存</th><td>仅当前页面内存，不持久化</td></tr>
                </tbody>
              </table>
              <section class="perm-panel" aria-labelledby="session-permissions">
                <div class="perm-head"><h2 id="session-permissions">授权范围</h2><span class="muted">{{ identity.permissions.length }} 项</span></div>
                <p class="perm-lead">按功能说明当前账号可以做的事。系统标识默认收起，需要核对时再打开。</p>
                <PermissionScope :permissions="identity.permissions" variant="granted" />
              </section>
            </div>
            <div v-else class="erp-page">
              <template v-if="view==='roles'">
                <p class="perm-lead">点一行查看该角色可以操作的功能。列表先给结论，明细里绿色为已授予，灰色虚线为未授予。</p>
                <ElTable :data="roles" border stripe size="small" row-key="name" highlight-current-row :current-row-key="selectedRoleName ?? undefined" :empty-text="loading ? '加载中…' : '暂无角色'" class="erp-table role-table" @row-click="pickRole">
                  <ElTableColumn type="index" label="序号" width="56" align="center" />
                  <ElTableColumn label="角色名称" width="168"><template #default="{row}"><span class="role-name"><button type="button" class="role-pick" :aria-pressed="selectedRoleName===row.name" @click="pickRole(row)">{{ roleName(row.name) }}</button><ElTag v-if="identity.role===row.name" size="small" effect="plain">当前</ElTag></span></template></ElTableColumn>
                  <ElTableColumn v-if="!narrow" prop="name" label="角色标识" width="190"><template #default="{row}"><code>{{ row.name }}</code></template></ElTableColumn>
                  <ElTableColumn v-if="!narrow" label="权限数" width="72" align="center"><template #default="{row}">{{ row.permissions.length }}</template></ElTableColumn>
                  <ElTableColumn label="能做什么" min-width="140"><template #default="{row}"><span class="perm-summary">{{ permissionBrief(row.permissions) }}</span></template></ElTableColumn>
                </ElTable>
                <section v-if="selectedRole" class="perm-panel" aria-labelledby="role-permission-heading">
                  <div class="perm-head"><h2 id="role-permission-heading">{{ roleName(selectedRole.name) }}</h2><code>{{ selectedRole.name }}</code><span class="muted">{{ selectedRole.permissions.length }} 项</span></div>
                  <PermissionScope :permissions="selectedRole.permissions" variant="matrix" />
                </section>
              </template>
              <ElTable v-else-if="view==='accounts'" :data="accounts" border stripe size="small" row-key="id" :empty-text="loading ? '加载中…' : '暂无平台账号'" class="erp-table">
                <ElTableColumn type="index" label="序号" width="56" align="center" />
                <ElTableColumn prop="email" label="平台邮箱" min-width="230" show-overflow-tooltip />
                <ElTableColumn label="角色" width="130"><template #default="{row}">{{ roleName(row.role) }}</template></ElTableColumn>
                <ElTableColumn label="状态" width="90" align="center"><template #default="{row}"><ElTag :type="row.status==='active' ? 'success' : 'info'" size="small">{{ row.status==='active' ? '正常' : row.status==='disabled' ? '停用' : row.status }}</ElTag></template></ElTableColumn>
                <ElTableColumn label="创建时间" width="170"><template #default="{row}">{{ formatTime(row.created_at) }}</template></ElTableColumn>
                <ElTableColumn label="操作" width="120" align="center" fixed="right"><template #default="{row}"><ElButton link type="primary" size="small" @click="selectedAccount=row.id">管理与历史</ElButton></template></ElTableColumn>
              </ElTable>
              <ElTable v-else :data="events" border stripe size="small" row-key="id" :empty-text="loading ? '加载中…' : '暂无安全审计事件'" class="erp-table">
                <ElTableColumn type="index" label="序号" width="56" align="center" />
                <ElTableColumn label="事件" width="170"><template #default="{row}"><strong>{{ actionName(row.action) }}</strong><small class="table-code">{{ row.action }}</small></template></ElTableColumn>
                <ElTableColumn label="操作账号 ID" min-width="280"><template #default="{row}"><code>{{ row.actor_id ?? '—' }}</code></template></ElTableColumn>
                <ElTableColumn label="请求标识" min-width="300"><template #default="{row}"><code>{{ row.request_id }}</code></template></ElTableColumn>
                <ElTableColumn label="发生时间" width="170"><template #default="{row}">{{ formatTime(row.created_at) }}</template></ElTableColumn>
              </ElTable>
              <div v-if="view!=='roles'" class="erp-footer">
                <span>第 {{ pageIndex+1 }} 页 · 本页 {{ view==='accounts' ? accounts.length : events.length }} 条 · 每页最多 50 条</span>
                <div class="erp-pager"><ElButton size="small" :disabled="!pageIndex || loading" @click="turnPage(-1)"><ChevronLeft :size="13" />上一页</ElButton><span class="page-number">{{ pageIndex+1 }}</span><ElButton size="small" :disabled="!nextCursor || loading" @click="turnPage(1)">下一页<ChevronRight :size="13" /></ElButton></div>
              </div>
            </div>
          </template>
        </main>
      </div>
    </div>
    <ElDialog v-model="confirmAll" title="退出全部平台会话？" width="min(440px, 90vw)" :close-on-click-modal="false"><p>此账号在所有设备上的平台会话都会失效，当前页面也将退出。此操作不影响家庭端身份。</p><template #footer><ElButton @click="confirmAll=false">取消</ElButton><ElButton type="primary" @click="signOut(true)">确认退出全部</ElButton></template></ElDialog>
  </div>
</template>
