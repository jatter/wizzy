<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { ApiError, operationId } from '../api';
import type { FamilyApi } from '../api';
import type { DailyItem, DailySubject, HomeworkDay, HomeworkFields as Fields, ScopeValue, WorkState } from '../daily-homework-types';
import { dailySummary, subjects, kinds, states, itemFields, recognizeHomeworkText } from '../daily-homework';
import { beijingDate } from '../homework-history';
import { privatePreview } from '../image-picker';
import FamilyButton from './FamilyButton.vue';
import FamilyInput from './FamilyInput.vue';
import HomeworkFields from './HomeworkFields.vue';
import HomeworkImportPanel from './HomeworkImportPanel.vue';
const props = defineProps<{ api: FamilyApi; student: string; guardian: boolean; visible: boolean }>();
const emit = defineEmits<{ changed: [] }>();
const student = props.student, generation = props.api.generation;
let active = true, loadVersion = 0;
const valid = () => active && props.student === student && props.api.generation === generation;
const date = ref(beijingDate()), day = ref<HomeworkDay | null>(null), backlog = ref<DailyItem[]>([]);
const working = ref(false), message = ref(''), stale = ref(false), filter = ref('all');
const screen = ref<'list' | 'edit' | 'import' | 'detail' | 'scope' | 'backlog'>('list');
const editing = ref<DailyItem | null>(null), form = ref<Fields | null>(null), reason = ref('');
const paste = ref(''), recognized = ref<Fields[]>([]), recognizedNotes = ref<string[]>([]), recognizeMessage = ref(''), activeDraft = ref(0), draftKeys = ref<string[]>([]);
const detail = ref<DailyItem | null>(null), previews = ref<{ id: string; url: string; release: () => void }[]>([]);
const scope = ref<Record<DailySubject, ScopeValue>>({ chinese: 'unknown', math: 'unknown', english: 'unknown', other: 'unknown' });
const pending = ref<{ label: string; run: () => Promise<unknown> } | null>(null);
const locked = computed(() => working.value || !!pending.value || stale.value);
const items = computed(() => (day.value?.items ?? []).filter(item => filter.value === 'all' || filter.value === 'pending' && !['completed','exempted'].includes(item.work_state) || filter.value === 'review' && item.work_state === 'submitted' || filter.value === 'verified' && item.verification_state === 'guardian_confirmed'));
const rows = computed(() => [...items.value].sort((a, b) => (['chinese','math','english','other'] as DailySubject[]).indexOf(a.subject) - (['chinese','math','english','other'] as DailySubject[]).indexOf(b.subject) || a.sort_order - b.sort_order));
const subjectLine = computed(() => (Object.keys(subjects) as DailySubject[]).map(subject => {
  const value = day.value?.subjects[subject], name = subjects[subject];
  return value === 'confirmed_none' ? name + '无作业' : value === 'has_items' ? name + '已有' : name + '未核对';
}).join(' · '));
function release() { previews.value.forEach(value => value.release()); previews.value = []; }
function report(error: unknown) { if (!valid() || error instanceof ApiError && error.code === 'stale') return; message.value = error instanceof Error ? error.message : '操作未完成，请重试。'; if (error instanceof ApiError && error.status === 409) { stale.value = true; message.value = '清单已变化。保留了当前编辑，请先复制需要的内容，再刷新清单核对。'; } }
async function work(action: () => Promise<void>) { if (working.value || !valid()) return; working.value = true; message.value = ''; try { await action(); } catch (error) { report(error); } finally { if (active) working.value = false; } }
async function load() {
  const version = ++loadVersion, selected = date.value;
  const [value, previous] = await Promise.all([props.api.homeworkDay(student, selected), props.api.homeworkBacklog(student)]);
  if (!valid() || version !== loadVersion || selected !== date.value) return;
  day.value = value; backlog.value = previous.items; scope.value = { ...value.subjects }; emit('changed');
}
function clearEditor() { form.value = null; editing.value = null; recognized.value = []; draftKeys.value = []; recognizedNotes.value = []; recognizeMessage.value = ''; paste.value = ''; }
async function refresh() { await work(async () => { await load(); stale.value = false; detail.value = null; release(); clearEditor(); screen.value = 'list'; }); }
async function attempt() {
  if (!pending.value) return;
  await work(async () => {
    try { await pending.value!.run(); }
    catch (error) { if (!(error instanceof ApiError && error.status === 0)) pending.value = null; throw error; }
    if (!valid()) return;
    pending.value = null; detail.value = null; release(); clearEditor(); screen.value = 'list'; await load();
  });
}
async function mutate(label: string, run: () => Promise<unknown>) { if (locked.value) return; pending.value = { label, run }; await attempt(); }
function back() {
  if (working.value || pending.value) return;
  if (day.value) scope.value = { ...day.value.subjects };
  screen.value = 'list'; detail.value = null; release(); clearEditor(); message.value = '';
}
function begin(item?: DailyItem) {
  if (locked.value) return;
  editing.value = item ?? null; form.value = itemFields(item); reason.value = ''; detail.value = null; release();
  paste.value = item?.raw_text || ''; recognized.value = []; draftKeys.value = []; recognizedNotes.value = []; recognizeMessage.value = ''; message.value = ''; activeDraft.value = 0;
  screen.value = 'edit';
}
function openImport() { if (locked.value) return; message.value = ''; screen.value = 'import'; }
function openScope() { if (locked.value || !day.value) return; message.value = ''; screen.value = 'scope'; }
function openBacklog() { if (locked.value) return; message.value = ''; screen.value = 'backlog'; }
function syncDraft() { if (form.value && recognized.value.length) recognized.value.splice(activeDraft.value, 1, itemFields(form.value)); }
function selectDraft(index: number) { if (locked.value || index === activeDraft.value) return; syncDraft(); activeDraft.value = index; form.value = itemFields(recognized.value[index]); }
function removeDraft(index: number) {
  if (locked.value) return;
  syncDraft(); recognized.value.splice(index, 1); draftKeys.value.splice(index, 1);
  if (!recognized.value.length) { activeDraft.value = 0; form.value = itemFields(); return; }
  activeDraft.value = Math.min(index, recognized.value.length - 1); form.value = itemFields(recognized.value[activeDraft.value]);
}
function recognize() {
  if (locked.value) return;
  const result = recognizeHomeworkText(paste.value, Number(date.value.slice(0, 4)) || new Date().getFullYear());
  recognizedNotes.value = result.notes; recognizeMessage.value = '';
  if (!result.items.length) { message.value = result.notes[0] || '没有识别出可保存的作业。'; return; }
  if (editing.value && result.items.length > 1) { message.value = '正在编辑一条作业，请一次只识别一项，或返回清单后新增。'; return; }
  message.value = '';
  if (result.items.length === 1) { form.value = itemFields(result.items[0]); recognized.value = []; draftKeys.value = []; recognizeMessage.value = '已填入对应栏位，请核对后再保存。'; return; }
  editing.value = null; recognized.value = result.items.map(item => itemFields(item)); draftKeys.value = result.items.map(() => operationId()); activeDraft.value = 0; form.value = itemFields(result.items[0]);
  recognizeMessage.value = `识别出 ${result.items.length} 项，已填入对应栏位。核对后可一次保存。`;
}
function dueReady(value: string | null) { return !value || (/(Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value))); }
async function save() {
  if (!form.value || !form.value.title.trim()) { message.value = '请填写作业内容。'; return; }
  const value = itemFields(form.value), key = operationId(), selected = date.value;
  if (!dueReady(value.due_at)) { message.value = '请完整填写截止日期和时刻，或清空截止日期。'; return; }
  const item = editing.value;
  if (item) { const payload = { ...value, expected_revision: item.revision, client_request_id: key, reason: reason.value }; await mutate('保存作业', () => props.api.updateHomework(item.id, payload)); }
  else { const payload = { ...value, client_request_id: key }; await mutate('添加作业', () => props.api.createHomework(student, selected, payload)); }
}
async function saveAll() {
  if (locked.value) return;
  syncDraft();
  const selected = date.value;
  const queue = recognized.value.map((item, index) => ({ fields: itemFields(item), key: draftKeys.value[index] || operationId() }));
  if (queue.some(entry => !entry.fields.title.trim())) { message.value = '还有作业内容是空的，请补全后再保存。'; return; }
  if (queue.some(entry => !dueReady(entry.fields.due_at))) { message.value = '请完整填写截止日期和时刻，或清空截止日期。'; return; }
  draftKeys.value = queue.map(entry => entry.key);
  await mutate('添加作业', async () => { for (const entry of queue) await props.api.createHomework(student, selected, { ...entry.fields, client_request_id: entry.key }); });
}
async function progress(item: DailyItem, state: WorkState) { const payload = { expected_revision: item.revision, client_request_id: operationId(), state }; await mutate('记录作业进度', () => props.api.progressHomework(item.id, payload)); }
async function verify(item: DailyItem, completed: boolean) { const payload = { expected_revision: item.revision, client_request_id: operationId(), completed, reason: reason.value }; await mutate('保存家长核验', () => props.api.verifyHomework(item.id, payload)); }
async function update(item: DailyItem, action: 'delete' | 'exempt' | 'restore') {
  if (!reason.value.trim()) { message.value = '请填写删除、免做或恢复的原因。'; return; }
  const payload = { expected_revision: item.revision, client_request_id: operationId(), reason: reason.value, ...(action === 'delete' ? { deleted: true } : { exempted: action === 'exempt' }) };
  await mutate('修改作业要求', () => props.api.updateHomework(item.id, payload));
}
async function coverage(complete: boolean) { if (!day.value) return; const payload = { expected_revision: day.value.revision, client_request_id: operationId(), subjects: { ...scope.value }, scope_state: complete ? 'complete' as const : 'incomplete' as const }; const selected = date.value; await mutate('确认清单范围', () => props.api.homeworkScope(student, selected, payload)); }
async function show(item: DailyItem) {
  await work(async () => {
    const value = await props.api.homeworkItem(item.id); if (!valid()) return;
    detail.value = value; reason.value = ''; release(); message.value = '';
    for (const source of value.sources) if (source.media_id) { const bytes = await props.api.mediaContent(source.media_id); if (!valid() || detail.value?.id !== item.id) return; previews.value.push({ id: source.id, ...privatePreview(bytes) }); }
    screen.value = 'detail';
  });
}
function localTime(value: string) { const parsed = Date.parse(value); if (!Number.isFinite(parsed)) return value; const local = new Date(parsed + 8 * 3600000).toISOString(); return local.slice(0, 10) + ' ' + local.slice(11, 16); }
function eventValue(value: Record<string, unknown> | null) {
  if (!value) return '';
  const parts: string[] = [];
  if (typeof value.title === 'string') parts.push(value.title);
  if (typeof value.work_state === 'string' && value.work_state in states) parts.push(states[value.work_state as WorkState]);
  if (typeof value.completed === 'boolean') parts.push(value.completed ? '确认完成' : '确认未完成');
  if (value.locator && typeof value.locator === 'object') parts.push(Object.values(value.locator).filter(Boolean).join(' · '));
  if (value.exempted === true) parts.push('免做'); if (value.exempted === false) parts.push('恢复应做');
  if (value.deleted === true) parts.push('删除');
  return parts.filter(Boolean).join(' · ');
}
async function selectDate(value: string) { if (locked.value) return; date.value = value; detail.value = null; release(); await work(load); }
async function openBacklogDate(value: string) { screen.value = 'list'; await selectDate(value); }
watch(form, value => { if (screen.value === 'edit' && value && recognized.value.length) recognized.value.splice(activeDraft.value, 1, itemFields(value)); });
watch(() => props.visible, value => { if (value && screen.value === 'list' && !pending.value) void work(load); });
onMounted(() => { if (props.visible) void work(load); });
onUnmounted(() => { active = false; loadVersion++; release(); });
</script>
<template>
  <view class="daily-homework">
    <view v-if="message" role="alert" class="notice error"><text>{{ message }}</text><FamilyButton v-if="stale" class="secondary compact" :disabled="working || !!pending" @click="refresh">刷新清单</FamilyButton></view>
    <view v-if="pending" class="notice warning"><text>保存结果尚未确认，当前内容已锁定，重试会使用同一操作编号。</text><FamilyButton class="secondary" :disabled="working" @click="attempt">重试{{ pending.label }}</FamilyButton></view>

    <view v-show="screen === 'list'" class="daily-list">
      <view class="panel daily-sheet" data-testid="daily-day">
        <view class="daily-toolbar">
          <view class="date-field"><text class="field-label">布置日期</text><FamilyInput class="field" :model-value="date" type="date" aria-label="作业日期" :disabled="locked" @update:model-value="selectDate" /></view>
          <FamilyButton class="secondary compact" :disabled="locked" @click="selectDate(beijingDate())">今天</FamilyButton>
          <FamilyButton class="secondary compact" :disabled="working || !!pending" @click="refresh">查询</FamilyButton>
          <view class="toolbar-gap"></view>
          <FamilyButton v-if="guardian" class="primary compact" :disabled="locked" @click="begin()">新增</FamilyButton>
          <FamilyButton v-if="guardian" class="secondary compact" aria-label="导入作业截图" :disabled="locked" @click="openImport">导入</FamilyButton>
        </view>
        <text class="daily-summary">{{ dailySummary(day) }}</text>
        <text v-if="day" class="small muted">家长已核验 {{ day.summary.verified }} 项 · 准备事项 {{ day.summary.preparation_completed }}/{{ day.summary.preparation_total }} 项 · {{ subjectLine }}</text>
        <text v-if="day?.pending_imports" class="notice warning">还有 {{ day.pending_imports }} 批来源待处理，清单待补充。</text>
        <text class="small muted">未标完成只表示没有完成记录。图片上的勾不会自动记成孩子已完成。</text>
      </view>
      <view class="daily-filters">
        <view class="button-row"><FamilyButton v-for="choice in [{value:'all',label:'全部作业'},{value:'pending',label:'未标完成'},{value:'review',label:'待家长检查'},{value:'verified',label:'已核验'}]" :key="choice.value" class="secondary compact" :aria-pressed="filter === choice.value" @click="filter = choice.value">{{ choice.label }}</FamilyButton></view>
        <view class="button-row"><FamilyButton v-if="guardian && day" class="secondary compact" :disabled="locked" @click="openScope">核对范围</FamilyButton><FamilyButton v-if="backlog.length" class="secondary compact" :disabled="locked" @click="openBacklog">历史待补 {{ backlog.length }}</FamilyButton></view>
      </view>
      <view class="daily-table">
        <view class="daily-row daily-head"><text>科目</text><text>内容</text><text>类型</text><text>状态</text><text>操作</text></view>
        <text v-if="day && !rows.length" class="empty-row">{{ filter === 'all' ? '这一天还没有录入作业。空白不是无作业。' : '当前筛选没有条目' }}</text>
        <view v-for="item in rows" :key="item.id" class="daily-row" data-testid="daily-item">
          <text class="cell subject">{{ subjects[item.subject] }}</text>
          <view class="cell title-cell"><text class="item-title">{{ item.title }}</text><text v-if="item.due_at" class="small muted">截止 {{ localTime(item.due_at) }}</text></view>
          <text class="cell">{{ kinds[item.item_type] }}</text>
          <text class="cell status">{{ states[item.work_state] }} · {{ item.verification_state === 'guardian_confirmed' ? '家长已核验' : '尚未经家长核验' }}{{ item.completion_actor === 'child' ? ' · 孩子标记' : item.completion_actor === 'guardian' ? ' · 家长标记' : '' }}</text>
          <view class="ops">
            <template v-if="item.work_state !== 'exempted'">
              <FamilyButton v-if="item.work_state === 'pending'" class="text-button row-action" :disabled="locked" @click="progress(item, 'in_progress')">开始</FamilyButton>
              <FamilyButton v-if="item.work_state !== 'completed'" class="text-button row-action" :disabled="locked" @click="progress(item, 'completed')">标记做完</FamilyButton>
              <FamilyButton v-if="item.work_state !== 'submitted'" class="text-button row-action" :disabled="locked" @click="progress(item, 'submitted')">请家长检查</FamilyButton>
              <FamilyButton v-if="item.work_state !== 'pending'" class="text-button row-action" :disabled="locked" @click="progress(item, 'pending')">重新打开</FamilyButton>
            </template>
            <FamilyButton class="text-button row-action" :disabled="locked" @click="show(item)">查看记录与来源</FamilyButton>
            <FamilyButton v-if="guardian" class="text-button row-action" :disabled="locked" @click="begin(item)">编辑作业</FamilyButton>
          </view>
        </view>
      </view>
    </view>

    <view v-show="screen === 'edit'" class="panel daily-editor daily-subpage">
      <view class="subhead"><FamilyButton class="text-button compact" :disabled="working || !!pending" @click="back">返回清单</FamilyButton><text class="section-title">{{ editing ? '编辑作业' : '新增作业' }}</text></view>
      <view class="paste-block">
        <text class="field-label">粘贴作业文字</text>
        <FamilyInput v-model="paste" class="paste-box" aria-label="作业文字" multiline maxlength="4000" :disabled="locked" placeholder="例如：语文：大册第15课、小册第15课、预习作文&#10;数学：练习册P13-P15&#10;英语：抄写U1单词3遍&#10;截止：11月16日 20:30" />
        <view class="button-row"><FamilyButton class="secondary compact" :disabled="locked || !paste.trim()" @click="recognize">识别填入</FamilyButton><text class="small muted">按文字填入科目、内容和页码。请核对后再保存。</text></view>
        <text v-if="recognizeMessage" role="status" class="small">{{ recognizeMessage }}</text>
        <text v-for="note in recognizedNotes" :key="note" class="small muted">{{ note }}</text>
      </view>
      <view v-if="recognized.length > 1" class="draft-list">
        <view v-for="(item, index) in recognized" :key="item.title + index" class="draft-row">
          <FamilyButton class="secondary compact draft-pick" :aria-pressed="activeDraft === index" :aria-label="'第' + (index + 1) + '项 ' + item.title" :disabled="locked" @click="selectDraft(index)">{{ subjects[item.subject] }} · {{ item.title }}</FamilyButton>
          <FamilyButton class="text-button row-action" :aria-label="'移除第' + (index + 1) + '项'" :disabled="locked" @click="removeDraft(index)">移除</FamilyButton>
        </view>
      </view>
      <HomeworkFields v-if="form" v-model="form" :disabled="locked" />
      <text v-if="editing?.verification_state === 'guardian_confirmed'" class="notice warning">修改要求后，需要家长重新核验。</text>
      <view class="button-row">
        <FamilyButton v-if="recognized.length > 1" class="primary" :disabled="locked" @click="saveAll">全部保存</FamilyButton>
        <FamilyButton v-else class="primary" :disabled="locked" @click="save">保存作业</FamilyButton>
        <FamilyButton class="secondary" :disabled="working || !!pending" @click="back">取消编辑</FamilyButton>
      </view>
    </view>

    <view v-show="screen === 'import'" class="daily-subpage">
      <view class="subhead"><FamilyButton class="text-button compact" :disabled="working || !!pending" @click="back">返回清单</FamilyButton><text class="section-title">导入作业截图</text></view>
      <HomeworkImportPanel v-if="guardian" :api="api" :student="student" :visible="visible && screen === 'import'" @published="work(load)" />
    </view>

    <view v-if="detail" v-show="screen === 'detail'" class="panel daily-detail daily-subpage">
      <view class="subhead"><FamilyButton class="text-button compact" :disabled="locked" @click="back">返回清单</FamilyButton><text class="section-title">{{ detail.title }}</text></view>
      <text>{{ detail.raw_text || '手工录入，未附原文' }}</text>
      <text class="small muted">定位 {{ Object.values(detail.locator).filter(Boolean).join(' · ') || '尚未填写' }}</text>
      <template v-if="guardian">
        <text class="field-label">核验、删除或免做原因</text>
        <FamilyInput v-model="reason" aria-label="作业处理原因" maxlength="500" :disabled="locked" />
        <view class="button-row">
          <FamilyButton v-if="detail.work_state !== 'exempted'" class="primary" :disabled="locked" @click="verify(detail, true)">家长确认完成</FamilyButton>
          <FamilyButton v-if="detail.work_state !== 'exempted'" class="secondary" :disabled="locked" @click="verify(detail, false)">家长确认未完成</FamilyButton>
          <FamilyButton class="secondary" :disabled="locked" @click="update(detail, detail.work_state === 'exempted' ? 'restore' : 'exempt')">{{ detail.work_state === 'exempted' ? '恢复为应做' : '标记免做' }}</FamilyButton>
          <FamilyButton class="text-button" :disabled="locked" @click="update(detail, 'delete')">删除作业</FamilyButton>
        </view>
      </template>
      <view v-for="source in detail.sources" :key="source.id" class="daily-source" data-testid="daily-source"><text class="small muted">来源：{{ source.source_label || '作业截图' }} · 布置日期 {{ detail.assigned_date }}</text><text>{{ source.raw_text }}</text><text v-if="source.source_mark" class="small muted">来源标记：{{ source.source_mark }}（不代表孩子完成）</text><image v-if="previews.find(image => image.id === source.id)" :src="previews.find(image => image.id === source.id)!.url" class="daily-source-image" mode="widthFix" /></view>
      <view v-for="event in detail.events" :key="event.id" class="daily-event"><text>{{ localTime(event.created_at) }} · {{ event.actor_audience === 'child' ? '孩子' : '家长' }} · {{ ({created:'录入',updated:'修改要求',progress:'记录进度',verified:'家长核验完成',confirmed_incomplete:'家长确认未完成',deleted:'删除'} as Record<string,string>)[event.action] ?? event.action }}</text><text v-if="event.reason">{{ event.reason }}</text><text v-if="eventValue(event.before_value)" class="small muted">原记录：{{ eventValue(event.before_value) }}</text><text v-if="eventValue(event.after_value)" class="small muted">本次记录：{{ eventValue(event.after_value) }}</text></view>
    </view>

    <view v-show="screen === 'scope'" class="panel daily-scope daily-subpage">
      <view class="subhead"><FamilyButton class="text-button compact" :disabled="working || !!pending" @click="back">返回清单</FamilyButton><text class="section-title">核对清单范围</text></view>
      <text class="muted">空白不是无作业。请确认所有科目及待处理截图后，再标记清单完整。</text>
      <view v-for="(label, subject) in subjects" :key="subject" class="scope-row"><text>{{ label }}</text><view class="button-row"><FamilyButton v-for="option in [{value:'unknown',label:'未核对'},{value:'has_items',label:'已有作业'},{value:'confirmed_none',label:'确认无作业'}]" :key="option.value" class="secondary compact" :aria-label="label + option.label" :aria-pressed="scope[subject] === option.value" :disabled="locked" @click="scope[subject] = option.value as ScopeValue">{{ option.label }}</FamilyButton></view></view>
      <view class="button-row"><FamilyButton class="primary" :disabled="locked" @click="coverage(true)">确认清单完整</FamilyButton><FamilyButton class="secondary" :disabled="locked" @click="coverage(false)">保存为待补充</FamilyButton></view>
    </view>

    <view v-show="screen === 'backlog'" class="panel daily-backlog daily-subpage">
      <view class="subhead"><FamilyButton class="text-button compact" :disabled="working || !!pending" @click="back">返回清单</FamilyButton><text class="section-title">历史待补</text></view>
      <text class="small muted">最多显示100项，仍属于原布置日期。未填截止时间不会自动标记逾期。</text>
      <FamilyButton v-for="item in backlog" :key="item.id" class="secondary compact backlog-row" :disabled="locked" @click="openBacklogDate(item.assigned_date)">{{ item.assigned_date }} · {{ subjects[item.subject] }} · {{ item.title }}</FamilyButton>
    </view>
  </view>
</template>
<style scoped>
.daily-homework { display:flex; flex-direction:column; gap:10px; min-width:0; }
.daily-homework :deep(.panel) { padding:12px 14px; margin-bottom:0; }
.daily-sheet, .daily-editor, .daily-detail, .daily-scope, .daily-source, .paste-block { display:flex; flex-direction:column; gap:8px; }
.daily-toolbar, .daily-filters, .subhead, .scope-row, .draft-row { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.daily-toolbar { align-items:flex-end; }
.date-field { width:168px; max-width:100%; }
.date-field :deep(.field) { min-height:36px; height:36px; padding:0 8px; }
.daily-homework :deep(.field-label) { margin:0 0 4px; }
.toolbar-gap { flex:1; min-width:8px; }
.daily-summary { font-size:15px; font-weight:700; }
.daily-filters { justify-content:space-between; }
.daily-table { border:1px solid var(--line); border-radius:10px; background:#fff; overflow:hidden; }
.daily-row { display:grid; grid-template-columns:52px minmax(0,1.4fr) 64px minmax(140px,.9fr) minmax(160px,auto); gap:4px 8px; align-items:center; padding:7px 10px; border-top:1px solid var(--line); min-width:0; }
.daily-head { border-top:0; background:#f3f6fb; color:#526077; font-size:12px; font-weight:650; }
.cell, .item-title, .empty-row { min-width:0; overflow-wrap:anywhere; }
.item-title { display:block; font-weight:650; }
.title-cell { display:flex; flex-direction:column; gap:2px; }
.subject { font-weight:700; }
.status { font-size:12px; line-height:1.45; color:#526077; }
.ops { display:flex; flex-wrap:wrap; justify-content:flex-end; gap:0 2px; }
.daily-homework :deep(.row-action) { min-height:28px; padding:2px 6px; font-size:12px; line-height:1.4; }
.empty-row { display:block; padding:14px 12px; color:var(--muted); }
.subhead { margin-bottom:4px; }
.paste-box { width:100%; min-height:76px; max-height:140px; border:1px solid #cbd5e4; border-radius:8px; padding:8px 10px; background:#fff; resize:vertical; }
.draft-list { display:flex; flex-direction:column; gap:4px; }
.draft-pick { justify-content:flex-start; text-align:left; white-space:normal; }
.scope-row { justify-content:space-between; }
.daily-event { display:flex; flex-direction:column; padding:8px 0; border-top:1px solid var(--line); gap:4px; }
.daily-source-image { width:100%; max-width:640px; }
.backlog-row { justify-content:flex-start; text-align:left; white-space:normal; }
.daily-editor :deep(.homework-fields) { gap:4px; }
.daily-editor :deep(.field-label) { margin:6px 0 4px; }
.daily-editor :deep(.locator-grid) { gap:8px; }
@media (max-width:720px) {
  .daily-head { display:none; }
  .daily-row { grid-template-columns:minmax(0,1fr); padding:8px 10px; }
  .ops { justify-content:flex-start; }
  .toolbar-gap { display:none; }
}
</style>
