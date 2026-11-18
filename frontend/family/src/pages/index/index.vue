<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue';
import { onShow, onHide } from '@dcloudio/uni-app';
import { ApiError, FamilyApi, operationId } from '../../api';
import type { Identity, Tenant, Student, EvaluationPolicy, EvaluationConsent, Device, Mistake, ReviewTask } from '../../api';
import { logoutPending, request } from '../../transport';
import HomeworkPanel from '../../components/HomeworkPanel.vue';
import DailyHomeworkPanel from '../../components/DailyHomeworkPanel.vue';
import DailyHomeworkSummary from '../../components/DailyHomeworkSummary.vue';
import MistakeBankPanel from '../../components/MistakeBankPanel.vue';
import ReviewSheetPanel from '../../components/ReviewSheetPanel.vue';
import ReportsPanel from '../../components/ReportsPanel.vue';
import ReviewPlanHistoryPanel from '../../components/ReviewPlanHistoryPanel.vue';
import TimetablePanel from '../../components/TimetablePanel.vue';
import FamilyButton from '../../components/FamilyButton.vue';
import FamilyInput from '../../components/FamilyInput.vue';
import ConsentCheck from '../../components/ConsentCheck.vue';
import LearningIcon from '../../components/LearningIcon.vue';
import AppDownload from '../../components/AppDownload.vue';
import LearningLoginSettings from '../../components/LearningLoginSettings.vue';
import { inNativeApp, nativeRequest } from '../../native-app';
import { authenticateLearningPasskey, supportsPasskeys, passkeyError } from '../../passkeys';
import learningDesk from '../../static/learning-desk.svg';
import { beijingDate } from '../../reports';

const profile = ref<Identity | null>(null);
const loginSettings = ref(false), passkeysAvailable = ref(supportsPasskeys());
function publicEnd(): 'child' | 'parent' | null {
  let end: string | null = null;
  // #ifdef H5
  end = new URLSearchParams(location.search).get('end');
  // #endif
  return end === 'child' || end === 'parent' ? end : null;
}
const mode = ref<'login' | 'register' | 'child'>(publicEnd() === 'child' ? 'child' : 'login');
const parentCenter = ref(false), parentGate = ref(false), restoring = ref(false);
const tab = ref<'learning' | 'family' | 'privacy' | 'reports' | 'timetable' | 'capture' | 'history' | 'daily'>('learning');
const learningSection = ref('');
function chooseMode(value: 'login' | 'register' | 'child') {
  mode.value = value;
  password.value = '';
  loginCode.value = '';
  privacyAccepted.value = false;
  notice.value = '';
  // #ifdef H5
  const url = new URL(location.href);
  url.searchParams.delete('end');
  history.replaceState(null, '', url);
  // #endif
}
function openParentCenter() {
  if (child.value) { chooseMode('login'); parentGate.value = true; return; }
  parentCenter.value = true;
  tab.value = selected.value?.consent_active ? 'reports' : 'family';
}
function returnToLearning() {
  parentCenter.value = false; parentGate.value = false; password.value = ''; notice.value = ''; tab.value = 'learning';
  learningSection.value = '';
  scrollToSection('learning-top');
}
function openSignedInHome() {
  tab.value = !parentCenter.value && selected.value?.consent_active ? 'capture' : 'learning';
  learningSection.value = '';
}
async function startLearning() {
  if (!selected.value) return;
  const student = selected.value.id;
  await run(async () => {
    profile.value = await api.startLearning(student);
    clearStudent(); households.value = []; students.value = []; email.value = '';
    parentCenter.value = false;
    await enter();
    openSignedInHome();
    await rememberLearningDevice();
  });
}
function focusSection(id: string) {
  tab.value = id === 'homework-capture' ? 'capture' : 'learning';
  learningSection.value = id;
  scrollToSection(id);
}
function scrollToSection(id: string) {
  void nextTick(() => {
  // #ifdef H5
    document.getElementById(id)?.scrollIntoView({ block: 'start' });
  // #endif
  // #ifndef H5
    uni.pageScrollTo({ selector: '#' + id, duration: 0 });
  // #endif
  });
}
const email = ref(''), password = ref(''), privacyAccepted = ref(false), loginCode = ref(''), deviceName = ref('我的学习设备');
const familyName = ref(''), households = ref<Tenant[]>([]), students = ref<Student[]>([]), nextStudent = ref<string | null>(null);
const selected = ref<Student | null>(null), nickname = ref(''), grade = ref('3'), editing = ref(false);
const policy = ref<EvaluationPolicy | null>(null), evaluation = ref<EvaluationConsent | null>(null);
const serviceChecked = ref(false), evaluationChecked = ref(false), confirmWithdrawal = ref(false);
const devices = ref<Device[]>([]), pairCode = ref(''), pairUntil = ref(0), now = ref(Date.now());
const mistakes = ref<Mistake[]>([]), nextMistake = ref<string | null>(null), pendingUpdates = ref(0), bankCount = ref(0);
const tasks = ref<ReviewTask[]>([]), nextTask = ref<string | null>(null), currentTask = ref<ReviewTask | null>(null), answer = ref('');
const busy = ref(false), notice = ref(''), noticeKind = ref('info');
let epoch = 0, operation = 0, clock: ReturnType<typeof setInterval>, verification: ReturnType<typeof setInterval>;
const child = computed(() => profile.value?.audience === 'child');
const owner = computed(() => profile.value?.role === 'owner');
const family = computed(() => households.value.find(t => t.id === profile.value?.tenant_id)?.name ?? '家庭学习空间');
const remaining = computed(() => Math.max(0, Math.ceil((pairUntil.value - now.value) / 1000)));
const today = computed(() => beijingDate(now.value));
const dateLabel = computed(() => new Date(today.value + 'T00:00:00+08:00').toLocaleDateString('zh-CN', { timeZone: 'Asia/Shanghai', month: 'long', day: 'numeric', weekday: 'long' }));
const dueTasks = computed(() => tasks.value.filter(t => t.state === 'open' && t.source_is_current && t.due_date <= today.value).sort((a, b) => a.due_date.localeCompare(b.due_date)));
const awaitingTasks = computed(() => tasks.value.filter(t => t.state === 'awaiting_review'));
const api = new FamilyApi(request, () => { resetLocal(); inform('登录或配对已失效，请重新进入。', 'error'); });
function inform(message: string, kind = 'info') { notice.value = message; noticeKind.value = kind; }
function clearLearning() { mistakes.value = []; tasks.value = []; nextMistake.value = null; nextTask.value = null; pendingUpdates.value = 0; bankCount.value = 0; currentTask.value = null; answer.value = ''; }
function clearStudent() {
  epoch++; selected.value = null; clearLearning(); devices.value = []; policy.value = null; evaluation.value = null;
  pairCode.value = ''; pairUntil.value = 0; serviceChecked.value = false; evaluationChecked.value = false;
  confirmWithdrawal.value = false; editing.value = false; nickname.value = ''; grade.value = '3';
}
function resetLocal() {
  loginSettings.value = false;
  mode.value = profile.value?.audience === 'child' ? 'child' : 'login';
  operation++; busy.value = false; clearStudent(); profile.value = null; households.value = []; students.value = [];
  nextStudent.value = null; password.value = ''; email.value = ''; loginCode.value = ''; deviceName.value = '我的学习设备';
  familyName.value = ''; privacyAccepted.value = false; tab.value = 'learning';
  parentCenter.value = false; parentGate.value = false;
  learningSection.value = '';
}
async function run(work: () => Promise<void>) {
  if (busy.value) return;
  const mine = ++operation; busy.value = true; notice.value = '';
  try { await work(); } catch (error) {
    if (mine === operation && !(error instanceof ApiError && error.code === 'stale')) {
      inform(error instanceof ApiError ? error.message + (error.requestId ? ' 参考号：' + error.requestId : '') : '操作未完成，请重试。', 'error');
    }
  } finally { if (mine === operation) busy.value = false; }
}
async function loadStudent(student: Student) {
  const version = epoch, generation = api.generation;
  const valid = () => epoch === version && generation === api.generation && selected.value?.id === student.id;
  const fresh = await api.student(student.id);
  if (!valid()) return;
  selected.value = fresh;
  students.value = students.value.map(s => s.id === fresh.id ? fresh : s);
  if (!fresh.consent_active) clearLearning();
  if (!child.value) {
    const [p, e, d] = await Promise.all([api.policy(), api.evaluation(fresh.id), api.devices()]);
    if (!valid()) return;
    policy.value = p; evaluation.value = e; devices.value = d.items.filter(item => item.student_id === fresh.id);
  }
  if (fresh.consent_active) {
    const [m, t, bank] = await Promise.all([api.mistakes(fresh.id), api.tasks(fresh.id), api.mistakeBank(fresh.id)]);
    if (!valid()) return;
    mistakes.value = m.items; nextMistake.value = m.next_cursor; pendingUpdates.value = m.pending_updates;
    tasks.value = t.items; nextTask.value = t.next_cursor;
    bankCount.value = bank.items.length;
  }
}
async function chooseStudent(student: Student) { clearStudent(); selected.value = student; await loadStudent(student); }
async function enter() {
  if (child.value) {
    const s = await api.student(profile.value!.student_id!); students.value = [s]; await chooseStudent(s); return;
  }
  households.value = (await api.tenants()).items;
  if (!profile.value?.tenant_id && households.value.length === 1) profile.value = await api.activateTenant(households.value[0].id);
  if (!profile.value?.tenant_id) return;
  const page = await api.students(); students.value = page.items; nextStudent.value = page.next_cursor;
  if (page.items[0]) await chooseStudent(page.items[0]);
}
async function authenticate() {
  await run(async () => {
    const unlocking = parentGate.value;
    if (mode.value === 'register' && !privacyAccepted.value) { inform('请先阅读并勾选隐私说明。', 'error'); return; }
    try {
      profile.value = mode.value === 'child' ? await api.pair(loginCode.value.trim(), deviceName.value.trim()) :
        mode.value === 'register' ? await api.register(email.value.trim(), password.value) : await api.login(email.value.trim(), password.value);
    } finally { password.value = ''; loginCode.value = ''; }
    clearStudent(); students.value = []; households.value = []; nextStudent.value = null;
    await enter();
    parentGate.value = false;
    if (unlocking) openParentCenter();
    else openSignedInHome();
    if (child.value) await rememberLearningDevice();
  });
}
async function rememberLearningDevice() {
  if (!inNativeApp()) return;
  try { await nativeRequest('rememberDevice'); }
  catch { inform('已进入学习。自动登录尚未设置成功，可在“快捷登录”中重试。'); }
}
async function authenticatePasskey() {
  await run(async () => {
    if (parentGate.value) return;
    try { profile.value = await authenticateLearningPasskey(api); }
    catch (error) { inform(passkeyError(error), 'error'); return; }
    clearStudent(); students.value = []; households.value = []; nextStudent.value = null;
    await enter(); openSignedInHome();
  });
}
async function changeHousehold(tenantId: string) {
  await run(async () => {
    clearStudent(); students.value = []; nextStudent.value = null;
    profile.value = await api.activateTenant(tenantId); await enter();
  });
}
async function createHousehold() {
  await run(async () => { clearStudent(); students.value = []; profile.value = await api.createTenant(familyName.value.trim()); familyName.value = ''; await enter(); });
}
async function saveStudent() {
  await run(async () => {
    const value = editing.value ? await api.updateStudent(selected.value!, nickname.value.trim(), Number(grade.value)) :
      await api.createStudent(nickname.value.trim(), Number(grade.value));
    if (!students.value.some(s => s.id === value.id)) students.value = [...students.value, value];
    await chooseStudent(value); tab.value = 'privacy'; inform('学生资料已保存。');
  });
}
function editStudent() { if (!selected.value) return; nickname.value = selected.value.nickname; grade.value = String(selected.value.grade); editing.value = true; tab.value = 'family'; }
async function setService(granted: boolean) {
  await run(async () => {
    const s = selected.value!;
    if (granted && !serviceChecked.value) { inform('请先阅读并勾选学生作业服务说明。', 'error'); return; }
    await api.serviceConsent(s.id, granted); clearStudent(); selected.value = s;
    await loadStudent(s); inform(granted ? '作业服务授权已开启。可选评测仍需单独选择。' : '作业服务授权已撤回，孩子设备已失效，后续评测使用已停止。');
  });
}
async function setEvaluation(granted: boolean) {
  await run(async () => {
    if (!policy.value || !evaluation.value || (granted && !evaluationChecked.value)) { inform('请先阅读并单独勾选评测授权。', 'error'); return; }
    evaluation.value = (await api.decideEvaluation(selected.value!.id, evaluation.value, granted, policy.value, operationId())).state;
    evaluationChecked.value = false; inform(granted ? '可选评测授权已开启。' : '可选评测授权已撤回，不影响作业服务。');
  });
}
async function createPairing() { await run(async () => { pairCode.value = ''; const value = await api.pairing(selected.value!.id); pairUntil.value = Date.now() + value.expires_in_seconds * 1000; now.value = Date.now(); pairCode.value = value.code; }); }
async function revoke(device: Device) { await run(async () => { await api.revokeDevice(device.id); devices.value = devices.value.filter(d => d.id !== device.id); inform('设备访问已撤销。'); }); }
async function more(kind: 'students' | 'mistakes' | 'tasks') {
  await run(async () => {
    if (kind === 'students' && nextStudent.value) { const p = await api.students(nextStudent.value); students.value = [...students.value, ...p.items]; nextStudent.value = p.next_cursor; }
    if (kind === 'mistakes' && nextMistake.value) { const p = await api.mistakes(selected.value!.id, nextMistake.value); mistakes.value = [...mistakes.value, ...p.items]; nextMistake.value = p.next_cursor; pendingUpdates.value = p.pending_updates; }
    if (kind === 'tasks' && nextTask.value) { const p = await api.tasks(selected.value!.id, nextTask.value); tasks.value = [...tasks.value, ...p.items]; nextTask.value = p.next_cursor; }
  });
}
async function openTask(task: ReviewTask) {
  await run(async () => {
    currentTask.value = null; answer.value = '';
    const fresh = await api.task(task.id); currentTask.value = fresh; answer.value = fresh.draft.student_answer;
    focusSection('current-exercise');
  });
}
async function saveDraft() { await run(async () => { currentTask.value = await api.draft(currentTask.value!, answer.value); tasks.value = tasks.value.map(t => t.id === currentTask.value!.id ? currentTask.value! : t); inform('草稿已保存，尚未提交。'); }); }
async function submitAttempt(outcome: string) {
  await run(async () => {
    if (!currentTask.value) return;
    if (answer.value !== currentTask.value.draft.student_answer) currentTask.value = await api.draft(currentTask.value, answer.value);
    currentTask.value = await api.attempt(currentTask.value, outcome, operationId());
    tasks.value = tasks.value.map(t => t.id === currentTask.value!.id ? currentTask.value! : t);
    inform('复习记录已提交，等待核验。');
  });
}
async function logout() {
  if (inNativeApp()) { try { await nativeRequest('forgetDevice'); } catch { /* Server logout below also revokes the remembered device. */ } }
  const pending = api.logout(); resetLocal(); notice.value = '';
  chooseMode('login'); busy.value = true;
  try { await pending; if (!profile.value) inform('已安全退出。'); }
  catch { if (!profile.value) inform('本地已退出，服务端撤销未确认。请检查网络后重新登录。', 'error'); }
  finally { busy.value = false; }
}
async function verify() {
  if (!profile.value || busy.value) return;
  const version = epoch, generation = api.generation;
  try {
    const identity = await api.me();
    if (version !== epoch || generation !== api.generation) return;
    if (identity.tenant_id !== profile.value?.tenant_id || identity.student_id !== profile.value?.student_id || identity.role !== profile.value?.role) {
      api.clear(); resetLocal(); inform('账号权限已变化，请重新进入。', 'error'); return;
    }
    if (selected.value) {
      const fresh = await api.student(selected.value.id);
      if (version !== epoch || generation !== api.generation) return;
      selected.value = fresh;
      if (!fresh.consent_active) { clearLearning(); pairCode.value = ''; evaluation.value = null; }
    }
  } catch (error) {
    if (version === epoch && error instanceof ApiError && error.status !== 0) {
      clearStudent(); inform(error.message, 'error');
    }
  }
}
onShow(() => { void verify(); });
onHide(() => { pairCode.value = ''; });
onMounted(async () => {
  if (inNativeApp()) {
    try { const status = await nativeRequest('status'); passkeysAvailable.value = passkeysAvailable.value && status.passkeys === true; } catch { passkeysAvailable.value = false; }
  }
  clock = setInterval(() => { now.value = Date.now(); if (!remaining.value) pairCode.value = ''; }, 1000);
  verification = setInterval(() => { void verify(); }, 30000);
  // #ifdef H5
  restoring.value = true;
  await run(async () => {
    if (logoutPending()) {
      try { await api.logout(); } catch (error) { if (!(error instanceof ApiError && error.status === 401)) throw error; }
      return;
    }
    profile.value = await api.resume();
    if (profile.value) { await enter(); openSignedInHome(); }
  });
  restoring.value = false;
  // #endif
});
onUnmounted(() => { clearInterval(clock); clearInterval(verification); api.clear(); resetLocal(); });
</script>

<template>
  <view class="family-app" :class="{ 'student-space': child, 'senior-space': selected && selected.grade > 6 }">
    <view class="topbar"><view class="brand"><text class="brand-mark">知</text><view><text class="brand-name">小知</text><text class="brand-caption">把每一次练习，变成一点进步</text></view></view><view v-if="profile && !restoring" class="top-actions"><FamilyButton v-if="parentCenter || parentGate" class="text-button" :disabled="busy" @click="returnToLearning">返回学习</FamilyButton><FamilyButton v-else class="text-button" :disabled="busy" @click="openParentCenter">家长中心</FamilyButton><FamilyButton v-if="child && !parentGate" class="text-button" :disabled="busy" @click="loginSettings = !loginSettings">快捷登录</FamilyButton><FamilyButton class="text-button" :disabled="busy" @click="logout">退出登录</FamilyButton></view></view>
    <view v-if="notice" class="notice" :class="noticeKind" role="alert" aria-live="polite">{{ notice }}</view>
    <view v-if="restoring" class="session-loading" role="status">正在恢复学习空间…</view>
    <view v-if="!restoring && (!profile || parentGate)" class="welcome-layout">
      <view class="welcome-story">
        <text class="welcome-label"><LearningIcon name="book" /> 小知学习空间</text>
        <view class="welcome-title"><text>每一个弄懂的瞬间，</text><text class="welcome-accent">都算进步。</text></view>
        <view class="welcome-description"><text>拍下认真完成的作业，理清一道错题，</text><text>再把学过的知识记牢一点。</text></view>
        <image :src="learningDesk" class="welcome-illustration" mode="aspectFit" aria-hidden="true" />
        <view class="welcome-steps"><text>独立尝试</text><LearningIcon name="arrow" /><text>回看订正</text><LearningIcon name="arrow" /><text>温故知新</text></view>
      </view>
      <view class="auth-panel panel">
        <view class="auth-heading-icon"><LearningIcon :name="mode === 'child' ? 'book' : 'shield'" /></view>
        <text class="auth-role">{{ parentGate ? '家长验证' : mode === 'child' ? '学生学习' : '家庭账号' }}</text>
        <text class="page-title">{{ parentGate ? '进入家长中心' : mode === 'child' ? '准备好，开始学习' : mode === 'register' ? '创建你的家庭账号' : '欢迎回到小知' }}</text><text class="muted">{{ parentGate ? '验证家长账号后，管理孩子的学习。' : mode === 'child' ? '输入家长给你的配对码，就能打开自己的学习桌。' : mode === 'register' ? '为孩子建立专属的学习空间。' : '登录后，继续陪伴孩子的每一点进步。' }}</text>
        <template v-if="mode !== 'child'"><text class="field-label">用户名</text><FamilyInput v-model="email" aria-label="用户名" class="field" placeholder="例如：liyuhan" type="text" autocomplete="username" autocapitalize="none" :spellcheck="false" maxlength="254" :disabled="busy" /><text class="field-label">登录密码</text><FamilyInput v-model="password" aria-label="登录密码" class="field" :placeholder="mode === 'register' ? '设置密码（至少 8 位）' : '请输入密码'" :autocomplete="mode === 'register' ? 'new-password' : 'current-password'" password maxlength="128" :disabled="busy" @keydown.enter="!busy && email.trim() && password && (mode !== 'register' || (password.length >= 8 && privacyAccepted)) && authenticate()" />
          <view v-if="mode === 'register'" class="policy-copy"><text class="section-title">隐私说明 · 2026-10-25</text><text>用户名用于账号识别，学生昵称和年级用于家庭学习管理。学生作业处理须创建档案后另行授权；离线评测另设可选授权，默认关闭。孩子设备可随时撤销。</text><ConsentCheck v-model="privacyAccepted" label="我已阅读并同意上述隐私说明" :disabled="busy" /></view>
        </template>
        <template v-else><text class="field-label">一次性配对码</text><FamilyInput v-model="loginCode" aria-label="一次性配对码" class="field pairing-input" placeholder="输入或粘贴配对码" autocomplete="one-time-code" autocapitalize="none" :spellcheck="false" maxlength="128" :disabled="busy" @keydown.enter="!busy && loginCode.trim() && authenticate()" /><view class="pairing-help"><LearningIcon name="shield" /><text>请家长在“家长中心 → 授权与设备”生成配对码。配对码 5 分钟内有效。</text></view></template>
        <FamilyButton class="primary wide" :disabled="busy || (mode === 'child' ? !loginCode.trim() : !email.trim() || password.length < (mode === 'register' ? 8 : 1) || (mode === 'register' && !privacyAccepted))" @click="authenticate">{{ busy ? '正在进入…' : parentGate ? '登录家长中心' : mode === 'child' ? '连接并开始学习' : mode === 'register' ? '创建账号' : '登录' }}</FamilyButton>
        <FamilyButton v-if="!parentGate && mode !== 'register' && passkeysAvailable" class="secondary wide" :disabled="busy" @click="authenticatePasskey">免密进入学习</FamilyButton>
        <view v-if="!parentGate" class="auth-links"><FamilyButton class="text-button" :disabled="busy" @click="chooseMode(mode === 'login' ? 'register' : 'login')">{{ mode === 'login' ? '创建账号' : '返回登录' }}</FamilyButton><FamilyButton v-if="mode === 'login'" class="text-button" :disabled="busy" @click="chooseMode('child')">连接孩子的学习设备</FamilyButton></view>
        <FamilyButton v-else class="text-button wide" :disabled="busy" @click="returnToLearning">继续学习</FamilyButton>
        <AppDownload v-if="!parentGate" />
        <text class="small muted auth-hint">{{ mode === 'child' ? '连接成功后，有效登录期内可直接回到学习桌。' : '在自己的设备上保持登录，共用设备请在使用后退出。' }}</text>
      </view>
    </view>
    <LearningLoginSettings v-if="profile && child && loginSettings && !parentGate" :key="profile.student_id!" :api="api" @close="loginSettings = false" />
    <view v-if="profile && !restoring && !parentGate" class="workspace">
      <view class="sidebar">
        <view class="household-label"><text v-if="selected" class="learner-avatar">{{ selected.nickname.slice(0, 1) }}</text><text class="family-name">{{ parentCenter ? '家长中心' : selected ? selected.nickname : family }}</text><text class="muted small">{{ parentCenter ? family : selected ? selected.grade + ' 年级 · 我的学习空间' : '每一点进步，都值得记录' }}</text></view>
        <view v-if="!child && (parentCenter || !profile.tenant_id) && households.length > 1" class="household-list"><FamilyButton v-for="h in households" :key="h.id" class="household-option" :class="{ chosen: h.id === profile.tenant_id }" :disabled="busy || h.id === profile.tenant_id" @click="changeHousehold(h.id)">{{ h.name }}</FamilyButton></view>
        <view v-if="!child && (parentCenter || students.length > 1)" class="student-index"><text class="field-label">{{ parentCenter ? '孩子的学习档案' : '切换孩子' }}</text><FamilyButton v-for="s in students" :key="s.id" class="student-option" :class="{ chosen: selected?.id === s.id }" :disabled="busy" @click="run(() => chooseStudent(s))"><text class="avatar">{{ s.nickname.slice(0, 1) }}</text><view><text class="student-name">{{ s.nickname }}</text><text class="small">{{ s.grade }} 年级 · {{ s.consent_active ? '已授权' : '待授权' }}</text></view><text v-if="selected?.id === s.id" class="selected-dot">●</text></FamilyButton><FamilyButton v-if="nextStudent" class="text-button" :disabled="busy" @click="more('students')">加载更多学生</FamilyButton></view>
        <view class="side-nav" role="navigation" aria-label="学习导航">
          <FamilyButton v-if="!parentCenter && selected?.consent_active" :class="{ chosen: tab === 'capture' }" :aria-current="tab === 'capture' ? 'page' : undefined" :disabled="busy" @click="focusSection('homework-capture')"><LearningIcon name="camera" /><text>拍作业</text></FamilyButton>
          <FamilyButton v-if="selected?.consent_active" :class="{ chosen: tab === 'daily' }" :disabled="busy" @click="tab = 'daily'"><LearningIcon name="check" /><text>每日作业</text></FamilyButton>
          <FamilyButton :class="{ chosen: tab === 'learning' && !learningSection }" :aria-current="tab === 'learning' && !learningSection ? 'page' : undefined" :disabled="busy" @click="returnToLearning"><LearningIcon name="home" /><text>学习页</text></FamilyButton>
          <FamilyButton v-if="!parentCenter && selected?.consent_active" :class="{ chosen: tab === 'history' }" :disabled="busy" @click="tab = 'history'"><LearningIcon name="book" /><text>作业记录</text></FamilyButton>
          <FamilyButton v-if="!parentCenter && selected?.consent_active" :class="{ chosen: tab === 'learning' && learningSection === 'mistake-list' }" :disabled="busy" @click="focusSection('mistake-list')"><LearningIcon name="book" /><text>错题回看</text></FamilyButton>
          <FamilyButton v-if="!parentCenter && selected?.consent_active" :class="{ chosen: tab === 'learning' && ['review-tasks', 'current-exercise'].includes(learningSection) }" :disabled="busy" @click="focusSection('review-tasks')"><LearningIcon name="review" /><text>复习清单</text></FamilyButton>
          <FamilyButton :class="{ chosen: tab === 'timetable' }" :disabled="busy" @click="tab = 'timetable'"><LearningIcon name="calendar" /><text>学习课表</text></FamilyButton>
          <FamilyButton v-if="!child && parentCenter" :class="{ chosen: tab === 'reports' }" :disabled="busy" @click="tab = 'reports'"><LearningIcon name="review" /><text>学习报告</text></FamilyButton>
          <FamilyButton v-if="!child && parentCenter" :class="{ chosen: tab === 'family' }" :disabled="busy" @click="tab = 'family'"><LearningIcon name="home" /><text>家庭与学生</text></FamilyButton>
          <FamilyButton v-if="!child && parentCenter" :class="{ chosen: tab === 'privacy' }" :disabled="busy" @click="tab = 'privacy'"><LearningIcon name="shield" /><text>授权与设备</text></FamilyButton>
        </view>
        <view class="side-note"><text class="small">每个孩子都有自己的节奏。</text><text class="small muted">一起把练习变成成长。</text></view>
      </view>
      <view id="learning-top" class="content">
        <view v-if="!['capture', 'history'].includes(tab)" class="page-heading" :class="{ 'compact-page': tab === 'daily' }"><view><text class="eyebrow">{{ tab === 'learning' ? dateLabel : parentCenter ? '家长中心' : '我的学习安排' }}</text><text class="page-title">{{ tab === 'daily' ? '每日作业' : tab === 'timetable' ? '我的学习课表' : tab === 'reports' ? '学习报告' : tab === 'family' ? '家庭与学生' : tab === 'privacy' ? '授权与设备' : tab === 'learning' && learningSection === 'mistake-list' ? '错题回看' : tab === 'learning' && ['review-tasks', 'current-exercise'].includes(learningSection) ? '复习清单' : selected ? selected.nickname + '，今天从这里开始' : '开启家庭学习空间' }}</text><text class="muted">{{ tab === 'timetable' ? '查看每天的课程，提前准备下一次学习。' : tab === 'reports' ? '按周期回看真实记录，安排下一步学习。' : tab === 'learning' ? '一次专注，一点进步。按自己的节奏来。' : tab === 'family' ? '建立学生档案，陪伴不同阶段的学习。' : tab === 'daily' ? '按日期核对清单，新增和导入分开办理。' : '作业服务和可选评测分别授权，随时可以撤回。' }}</text></view><view class="button-row"><FamilyButton v-if="!child && selected?.consent_active" class="primary compact" :disabled="busy" @click="startLearning">开始学习</FamilyButton><FamilyButton v-if="selected" class="secondary compact" :disabled="busy" @click="run(() => loadStudent(selected!))">刷新内容</FamilyButton></view></view>
        <view v-if="busy" class="loading" role="status">正在同步，请稍候…</view>
        <template v-if="!profile.tenant_id && !child"><view class="panel form-panel"><text class="section-title">先创建或选择一个家庭</text><text class="muted">家庭成员将按授权范围访问孩子的学习记录。</text><text class="field-label">家庭名称</text><FamilyInput v-model="familyName" aria-label="家庭名称" class="field" placeholder="例如：小知一家" maxlength="80" /><FamilyButton class="primary" :disabled="busy || !familyName.trim()" @click="createHousehold">创建家庭</FamilyButton></view></template>
        <template v-else-if="tab === 'family' && !child">
          <view class="two-columns"><view v-if="owner" class="panel form-panel"><text class="section-title">{{ editing ? '编辑学生资料' : '新增学生档案' }}</text><text class="muted">使用昵称即可。年级用于匹配学习内容。</text><text class="field-label">学生昵称</text><FamilyInput v-model="nickname" aria-label="学生昵称" class="field" placeholder="请输入学生昵称" maxlength="40" :disabled="busy" /><text class="field-label">年级（1–12）</text><FamilyInput v-model="grade" aria-label="年级" class="field" placeholder="请输入年级" type="number" :disabled="busy" /><FamilyButton class="primary" :disabled="busy || !nickname.trim() || !Number.isInteger(Number(grade)) || Number(grade) < 1 || Number(grade) > 12" @click="saveStudent">{{ editing ? '保存学生资料' : '创建学生档案' }}</FamilyButton><FamilyButton v-if="editing" class="text-button" :disabled="busy" @click="editing = false; nickname = ''; grade = '3'">改为新增学生</FamilyButton></view><view class="panel"><text class="section-title">{{ selected ? selected.nickname : '学生档案' }}</text><template v-if="selected"><view class="detail-row"><text class="muted">当前年级</text><text>{{ selected.grade }} 年级</text></view><view class="detail-row"><text class="muted">作业服务</text><text>{{ selected.consent_active ? '已授权' : '未授权' }}</text></view><FamilyButton v-if="owner" class="secondary" :disabled="busy" @click="editStudent">编辑当前学生</FamilyButton></template><text v-else class="empty-copy">创建第一个档案后，再为孩子开启作业服务。</text><text v-if="!owner" class="muted">学生资料由家庭管理员维护。你可以管理自己有权访问的学生授权。</text></view></view>
        </template>
        <template v-else-if="tab === 'privacy' && !child && selected">
          <view class="two-columns"><view class="panel"><view class="section-head"><text class="section-title">学生作业服务</text><text class="badge" :class="selected.consent_active ? 'success' : 'warning'">{{ selected.consent_active ? '已授权' : '待授权' }}</text></view><text class="body-copy">为 {{ selected.nickname }} 处理上传的作业、批改结果、错题与复习记录。开启孩子设备前需要监护人同意。撤回后停止处理，正在进行的任务会取消，已有孩子设备和未使用配对码立即失效。</text><text class="small muted">隐私版本及儿童规则版本：2026-10-25</text><template v-if="!selected.consent_active"><ConsentCheck v-model="serviceChecked" label="我作为监护人，同意该学生使用上述作业服务" :disabled="busy" /><FamilyButton class="primary" :disabled="busy || !serviceChecked" @click="setService(true)">开启作业服务授权</FamilyButton></template><template v-else><FamilyButton v-if="!confirmWithdrawal" class="danger-text" :disabled="busy" @click="confirmWithdrawal = true">撤回作业服务授权</FamilyButton><view v-else class="confirm-box"><text>撤回后孩子设备会失效，需要重新授权及配对才能继续。</text><view class="button-row"><FamilyButton class="danger" :disabled="busy" @click="setService(false)">确认撤回服务授权</FamilyButton><FamilyButton class="secondary" :disabled="busy" @click="confirmWithdrawal = false">保留授权</FamilyButton></view></view></template></view>
            <view class="panel"><view class="section-head"><text class="section-title">离线批改评测</text><text class="badge">可选 · {{ evaluation?.active ? '已授权' : '未授权' }}</text></view><text v-if="policy" class="body-copy">{{ policy.text }}</text><text v-else class="muted">授权说明尚未加载，请刷新后再操作。</text><text v-if="policy" class="small muted">授权版本：{{ policy.version }}</text><template v-if="evaluation && !evaluation.active"><ConsentCheck v-model="evaluationChecked" label="我自愿单独授权该学生参与上述离线评测" :disabled="busy || !selected.consent_active" /><FamilyButton class="secondary" :disabled="busy || !selected.consent_active || !evaluationChecked || !policy" @click="setEvaluation(true)">保存可选评测授权</FamilyButton></template><FamilyButton v-if="evaluation?.active" class="danger-text" :disabled="busy" @click="setEvaluation(false)">撤回可选评测授权</FamilyButton><text class="small muted">拒绝或撤回评测授权不影响已开启的作业服务。重新开启服务不会自动恢复旧评测授权。</text></view></view>
          <view class="panel device-panel"><view class="section-head"><view><text class="section-title">孩子的学习设备</text><text class="muted">一次性配对码 5 分钟有效；只可使用一次。</text></view><FamilyButton class="primary compact" :disabled="busy || !selected.consent_active" @click="createPairing">生成孩子配对码</FamilyButton></view><view v-if="pairCode && remaining" class="pairing-box"><text class="small">在孩子设备打开小知，点击“连接孩子的学习设备”并输入</text><text class="pair-code" data-testid="pair-code">{{ pairCode }}</text><text class="small">剩余 {{ remaining }} 秒 · 离开应用后隐藏</text><FamilyButton class="text-button" @click="pairCode = ''">隐藏配对码</FamilyButton></view><text v-if="!devices.length" class="empty-copy">暂无已配对设备。开启服务授权后，可以生成配对码。</text><view v-for="d in devices" :key="d.id" class="device-row"><view><text class="section-title">{{ d.device_name }}</text><text class="small muted">有效至 {{ d.expires_at.slice(0, 10) }}</text></view><FamilyButton class="danger-text compact" :disabled="busy" @click="revoke(d)">撤销设备访问</FamilyButton></view></view>
        </template>
        <template v-else-if="['timetable', 'capture', 'history'].includes(tab) && selected?.consent_active"></template>
        <template v-else-if="tab === 'reports' && !child && selected?.consent_active"></template>
        <template v-else-if="tab === 'daily' && selected?.consent_active"></template>
        <template v-else-if="tab === 'learning' && selected?.consent_active">
          <template v-if="!learningSection">
          <view class="study-focus">
            <view class="study-focus-copy">
              <text class="focus-label"><text class="focus-dot" /> 今日学习桌</text>
              <view class="focus-title"><text>{{ dueTasks.length ? '从一道题开始，' : '把认真写下的，' }}</text><text>{{ dueTasks.length ? '把思路理清楚。' : '变成真正弄懂的。' }}</text></view>
              <text class="focus-description">{{ dueTasks.length ? '有到期的复习等着你。先回想，再写下自己的解题过程。' : '先独立完成作业，再拍下题目和作答，一起回看值得再想一想的地方。' }}</text>
              <FamilyButton v-if="child && dueTasks[0]" class="primary focus-action" :disabled="busy" @click="openTask(dueTasks[0])">继续复习</FamilyButton>
              <FamilyButton v-else class="primary focus-action" :disabled="busy" @click="focusSection('homework-capture')">{{ child ? '拍一份作业' : '整理一份作业' }}</FamilyButton>
              <text class="focus-footnote">{{ child ? '不急着看答案，先相信自己的思考。' : '作答由孩子提交，家长核对后再安排复习。' }}</text>
            </view>
            <image :src="learningDesk" class="focus-illustration" mode="aspectFit" aria-hidden="true" />
          </view>
          <DailyHomeworkSummary :key="'daily-summary-' + selected.id" :api="api" :student="selected.id" @open="tab = 'daily'" />
          <view class="learning-overview" role="region" aria-label="学习概况">
            <FamilyButton class="overview-item" :disabled="busy" @click="focusSection('review-tasks')"><view class="overview-icon"><LearningIcon name="review" /></view><view><view class="overview-value"><text class="overview-number">{{ dueTasks.length }}{{ nextTask ? '+' : '' }} </text><text>题待复习</text></view><text class="small muted">到期且可以继续的任务</text></view></FamilyButton>
            <FamilyButton class="overview-item" :disabled="busy" @click="focusSection('mistake-list')"><view class="overview-icon mint"><LearningIcon name="book" /></view><view><view class="overview-value"><text class="overview-number">{{ bankCount }} </text><text>道错题</text></view><text class="small muted">回看原题与自己的作答</text></view></FamilyButton>
            <FamilyButton class="overview-item" :disabled="busy" @click="focusSection('review-tasks')"><view class="overview-icon amber"><LearningIcon name="check" /></view><view><view class="overview-value"><text class="overview-number">{{ awaitingTasks.length }}{{ nextTask ? '+' : '' }} </text><text>题待核对</text></view><text class="small muted">已提交，等待家长核验</text></view></FamilyButton>
          </view>
          <text class="overview-caption">{{ nextTask ? '基于已加载记录，还有更多复习待查看。' : '以上为当前已加载的学习记录。' }}</text>
          <view class="route-grid" aria-label="学习路线">
            <FamilyButton class="route-card" :disabled="busy" @click="focusSection('homework-capture')"><view class="route-icon"><LearningIcon name="camera" /></view><view><text class="section-title">拍作业</text><text class="muted">拍清题目，留下完整思路</text></view><LearningIcon name="arrow" /></FamilyButton>
            <FamilyButton class="route-card" :disabled="busy" @click="focusSection('mistake-list')"><view class="route-icon mint"><LearningIcon name="book" /></view><view><text class="section-title">错题订正</text><text class="muted">回看原题，找到卡住的一步</text></view><LearningIcon name="arrow" /></FamilyButton>
            <FamilyButton class="route-card" :disabled="busy" @click="focusSection('review-tasks')"><view class="route-icon amber"><LearningIcon name="review" /></view><view><text class="section-title">到期复习</text><text class="muted">隔一段时间，再试着做一遍</text></view><LearningIcon name="arrow" /></FamilyButton>
          </view>
          <ReviewPlanHistoryPanel :key="'plans-' + selected.id" :api="api" :student="selected.id" :guardian="!child" @task="openTask" @reports="tab = 'reports'" />
          </template>
          <MistakeBankPanel v-else-if="learningSection === 'mistake-list'" :api="api" :student="selected.id" :guardian="!child" @count="bankCount = $event" />
          <template v-else-if="['review-tasks', 'current-exercise'].includes(learningSection)">
          <view id="review-tasks" class="section-head list-heading"><view><text class="section-title">复习清单</text><text class="muted">提交后的作答仍需核验，不代表已经掌握。</text></view><text class="badge">{{ tasks.length }} 条已加载</text></view>
          <view v-if="!tasks.length" class="panel empty-state"><text class="empty-icon">✓</text><text class="section-title">还没有复习任务</text><text class="muted">{{ child ? '家长加入复习后，会出现在这里。' : '已有的复习任务会留在这里。新的再做卷从错题回看生成。' }}</text></view>
          <view class="task-grid"><view v-for="(t, index) in tasks" :key="t.id" class="panel task-card"><view class="section-head"><text class="task-number">复习 {{ String(index + 1).padStart(2, '0') }}</text><text class="badge" :class="!t.source_is_current ? 'warning' : ''">{{ !t.source_is_current ? '来源已更新' : t.state === 'awaiting_review' ? '已提交待核验' : t.state !== 'open' ? '已关闭' : t.attempts.length ? '已提交待核验' : '待复习' }}</text></view><text class="question-stem">{{ t.stem }}</text><text class="small muted">计划日期 {{ t.due_date }}</text><FamilyButton class="secondary" :disabled="busy" @click="openTask(t)">{{ child ? '打开复习' : '查看复习记录' }}</FamilyButton></view></view><FamilyButton v-if="nextTask" class="secondary load-more" :disabled="busy" @click="more('tasks')">加载更多复习</FamilyButton>
          <view v-if="currentTask" id="current-exercise" class="panel exercise">
            <view class="section-head"><view><text class="eyebrow">一次认真思考</text><text class="section-title">{{ child ? '写下我的思路' : '复习记录' }}</text></view><FamilyButton class="text-button compact" :disabled="busy" @click="currentTask = null; answer = ''">收起复习</FamilyButton></view>
            <text class="question-stem">{{ currentTask.stem }}</text>
            <text v-if="!currentTask.source_is_current" class="notice warning">原题已更新，本次复习不可继续提交。请让家长核对错题。</text>
            <template v-if="child">
              <text class="field-label">我的答案与思考过程</text>
              <FamilyInput multiline v-model="answer" aria-label="我的复习思路" class="answer-field" placeholder="可以先写：这道题已知什么？我准备怎样做？" maxlength="4000" :disabled="busy || !currentTask.source_is_current || currentTask.state !== 'open'" />
              <view v-if="currentTask.state === 'open'" class="exercise-actions"><view class="button-row"><FamilyButton class="primary" :disabled="busy || !answer.trim() || !currentTask.source_is_current" @click="submitAttempt('submitted')">{{ busy ? '正在保存…' : '提交复习记录' }}</FamilyButton><FamilyButton class="secondary" :disabled="busy || !currentTask.source_is_current" @click="saveDraft">保存草稿</FamilyButton><FamilyButton class="text-button" :disabled="busy || !currentTask.source_is_current" @click="submitAttempt('needs_help')">我需要帮助</FamilyButton></view><text class="small muted">暂时没想明白也没关系，可以保存思路或请家长帮忙。</text></view>
              <view v-if="currentTask.state === 'awaiting_review'" class="submission-feedback" role="status"><LearningIcon name="check" /><view><text class="section-title">{{ currentTask.attempts.at(-1)?.outcome === 'needs_help' ? '已记下你的求助' : '这次复习已记录' }}</text><text class="muted">{{ currentTask.attempts.at(-1)?.outcome === 'needs_help' ? '保留你的思路，和家长一起看看卡在了哪一步。' : '你的答案已保存，等待家长核对。核对后再看看下一步怎么练。' }}</text><FamilyButton class="secondary compact" :disabled="busy" @click="currentTask = null; answer = ''; focusSection('review-tasks')">返回复习清单</FamilyButton></view></view>
            </template>
            <template v-else><text class="body-copy">{{ currentTask.draft.student_answer || '孩子还没有保存草稿。' }}</text><text class="small muted">复习作答由孩子的配对设备提交。</text></template>
            <text v-for="a in currentTask.attempts" :key="a.id" class="attempt-row">{{ a.outcome === 'submitted' ? '已提交作答' : a.outcome === 'needs_help' ? '已请求帮助' : '本次已跳过' }} · 待核验</text>
          </view>
          <ReviewSheetPanel :key="'sheets-' + selected.id" :api="api" :student="selected.id" :guardian="!child" />
          </template>
        </template>
        <view v-else class="panel empty-state"><text class="empty-icon">知</text><text class="section-title">{{ selected ? '先为孩子开启作业服务' : '先建立孩子的学习档案' }}</text><text class="muted">{{ selected ? '监护人授权后，即可配对孩子设备、查看错题与复习。' : '创建学生档案，开始管理家庭学习。' }}</text><FamilyButton v-if="!child" class="primary" :disabled="busy" @click="parentCenter = true; tab = selected ? 'privacy' : 'family'">{{ selected ? '查看授权与设备' : '创建学生档案' }}</FamilyButton></view>
        <DailyHomeworkPanel v-if="selected?.consent_active" :key="'daily-' + selected.id" v-show="tab === 'daily'" :api="api" :student="selected.id" :guardian="!child" :visible="tab === 'daily'" />
        <TimetablePanel v-if="selected?.consent_active" :key="'timetable-' + selected.id" v-show="tab === 'timetable'" :api="api" :student="selected.id" :guardian="!child" :visible="tab === 'timetable'" />
        <ReportsPanel v-if="!child && selected?.consent_active" :key="'reports-' + selected.id" v-show="tab === 'reports'" :api="api" :student="selected.id" :visible="tab === 'reports'" @learning="returnToLearning(); run(() => loadStudent(selected!))" />
        <HomeworkPanel v-if="selected?.consent_active" id="homework-capture" :key="selected.id" v-show="tab === 'capture' || tab === 'history'" :api="api" :student="selected.id" :guardian="!child" :visible="tab === 'capture' || tab === 'history'" :section="tab === 'capture' ? 'capture' : 'history'" @navigate="tab = $event" />
      </view>
    </view>
    <view class="footer"><text>小知 · 家庭学习空间</text><text>学习记录仅向有权访问的家庭成员开放</text></view>
  </view>
</template>
