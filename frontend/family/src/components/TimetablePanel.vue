<script setup lang="ts">
import { computed, onUnmounted, ref, shallowRef, watch } from 'vue';
import { ApiError, operationId } from '../api';
import type { FamilyApi, LessonChange, LessonFields, LessonInstance, OverrideBody, ScheduleConflict, Term, Timetable, TimetableBody, TimetableSlot } from '../api';
import { beijingDate } from '../reports';
import { activeChanges, dateValue, lessonError, shiftDate, templateError, termError, weekdays, weekStart } from '../timetable';
import FamilyButton from './FamilyButton.vue';
import FamilyInput from './FamilyInput.vue';
import LessonEditor from './LessonEditor.vue';

const props = defineProps<{ api: FamilyApi; student: string; guardian: boolean; visible: boolean }>();
const generation = props.api.generation, student = props.student;
let active = true, initialized = false;
const valid = () => active && props.student === student && props.api.generation === generation;
const busy = ref(false), error = ref(''), notice = ref(''), needsReload = ref(false);
const terms = ref<Term[]>([]), next = ref<string | null>(null), schedule = ref<Timetable | null>(null);
const creating = ref(false), termName = ref(''), start = ref(beijingDate()), end = ref(beijingDate()), startsOn = ref(1);
const editing = ref(false), draft = ref<TimetableSlot[]>([]), lessons = ref<LessonInstance[]>([]), from = ref('');
const adjustment = ref<{ slot_id: string; source_date: string; action: 'move' | 'cancel'; target_date: string; fields: LessonFields } | null>(null);
const reason = ref('');
const pending = shallowRef<{ kind: 'template'; body: TimetableBody } | { kind: 'override'; body: OverrideBody } | null>(null);
const warnings = ref<ScheduleConflict[]>([]);
const locked = computed(() => busy.value || needsReload.value || !!pending.value);
const dirty = computed(() => editing.value || !!adjustment.value || !!pending.value);
const days = computed(() => !from.value ? [] : Array.from({ length: 7 }, (_, i) => {
  const date = shiftDate(from.value, i), weekday = (new Date(dateValue(date)!).getUTCDay() + 6) % 7;
  return { date, label: weekdays[weekday], items: lessons.value.filter(row => row.date === date) };
}));
const changes = computed(() => activeChanges(schedule.value?.overrides ?? []));
const actionLabel = (action: LessonChange['action']) => ({ move: '调课', cancel: '停课', restore: '恢复' })[action];
const conflictLabel = (row: ScheduleConflict) => row.date + ' · ' + (row.reason === 'same_period' ? '节次重复' : '上课时间重叠');
const weekLabel = (slot: TimetableSlot) => ({ all: '每周', odd: '单周', even: '双周' })[slot.weeks];
function resetEdit() { editing.value = false; adjustment.value = null; pending.value = null; warnings.value = []; reason.value = ''; draft.value = []; }
async function run(work: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true; error.value = ''; notice.value = '';
  try { await work(); } catch (e) {
    if (!valid() || (e instanceof ApiError && e.code === 'stale')) return;
    error.value = e instanceof ApiError ? e.message : '操作未完成，请重试。';
  } finally { if (valid()) busy.value = false; }
}
async function readWeek(term: Term, value: string) {
  const week = weekStart(value, term.week_starts_on);
  const result = await props.api.lessons(term.id, week, shiftDate(week, 6));
  if (!valid()) return;
  if (schedule.value?.term.id !== term.id || result.revision !== schedule.value.term.revision) {
    needsReload.value = true; lessons.value = []; throw new Error('revision');
  }
  from.value = week; lessons.value = result.items;
}
async function loadTerm(id: string, value?: string) {
  const result = await props.api.timetable(id);
  if (!valid()) return;
  schedule.value = result; lessons.value = []; needsReload.value = true;
  resetEdit();
  const today = beijingDate();
  await readWeek(result.term, value ?? (today >= result.term.start_date && today <= result.term.end_date ? today : result.term.start_date));
  if (valid()) needsReload.value = false;
}
async function loadTerms(more = false) {
  const result = await props.api.terms(student, more ? next.value ?? undefined : undefined);
  if (!valid()) return;
  terms.value = more ? [...terms.value, ...result.items] : result.items; next.value = result.next_cursor;
  if (!more) {
    const today = beijingDate();
    const term = result.items.find(row => row.start_date <= today && today <= row.end_date) ?? result.items.at(-1);
    if (term) await loadTerm(term.id);
    else { schedule.value = null; lessons.value = []; needsReload.value = false; }
    initialized = true;
  }
}
function createTerm() { run(async () => {
  const body = { name: termName.value.trim(), start_date: start.value, end_date: end.value, week_starts_on: startsOn.value };
  const message = termError(body); if (message) { error.value = message; return; }
  let result: Term;
  try { result = await props.api.createTerm(student, body); } catch (e) {
    if (valid() && e instanceof ApiError && (e.status === 0 || e.status >= 500)) { needsReload.value = true; creating.value = false; notice.value = '创建结果尚未确认，请刷新学期列表后核对，避免重复创建。'; }
    throw e;
  }
  if (!valid()) return;
  terms.value.push(result); creating.value = false; termName.value = '';
  await loadTerm(result.id); notice.value = '学期已创建，可以添加每周课程。';
}); }
function editTemplate() { if (!schedule.value) return; resetEdit(); draft.value = schedule.value.slots.map(row => ({ ...row })); editing.value = true; }
function addSlot() { draft.value.push({ id: operationId(), weekday: 1, weeks: 'all', period: 1, subject: '', start_time: null, end_time: null, room: null }); }
async function commit(command: NonNullable<typeof pending.value>, confirmed = false) {
  if (!schedule.value) return;
  try {
    const body = { ...command.body, confirm_conflicts: confirmed };
    const result = command.kind === 'template'
      ? await props.api.saveTimetable(schedule.value.term.id, body as TimetableBody)
      : await props.api.changeLesson(body as OverrideBody);
    if (!valid()) return;
    schedule.value = result; resetEdit(); needsReload.value = true;
    await readWeek(result.term, from.value); if (!valid()) return;
    needsReload.value = false; notice.value = '课表已保存。';
  } catch (e) {
    if (!valid()) return;
    if (e instanceof ApiError && e.code === 'schedule_conflict') {
      pending.value = command; warnings.value = e.conflicts; error.value = '存在课程冲突，请核对下方日期后决定是否保留。'; return;
    }
    pending.value = null; warnings.value = [];
    if (e instanceof ApiError && e.code === 'active_override_conflict') { error.value = '该课程仍有单次调停课，请先恢复对应调整，再修改或删除每周模板课程。'; return; }
    if (e instanceof ApiError && (e.status === 409 || e.status === 0 || e.status >= 500)) {
      needsReload.value = true;
      error.value = e.status === 409 ? '课表已被其他操作更新。请放弃草稿并刷新课表，核对最新内容后重新编辑。' : '保存结果尚未确认。请刷新课表核对后再编辑，避免重复操作。'; return;
    }
    throw e;
  }
}
function saveTemplate() { run(async () => {
  const message = templateError(draft.value); if (message) { error.value = message; return; }
  await commit({ kind: 'template', body: { expected_revision: schedule.value!.term.revision, slots: draft.value.map(row => ({ ...row })), confirm_conflicts: false } });
}); }
function adjust(row: LessonInstance, action: 'move' | 'cancel') {
  resetEdit(); adjustment.value = { slot_id: row.slot_id, source_date: row.source_date, action, target_date: row.date, fields: { period: row.period, subject: row.subject, start_time: row.start_time, end_time: row.end_time, room: row.room } };
}
function submitAdjustment() { run(async () => {
  const row = adjustment.value!, term = schedule.value!.term;
  if (!reason.value.trim()) { error.value = '请填写调整原因。'; return; }
  if (row.action === 'move') {
    const message = lessonError(row.fields);
    if (message) { error.value = message; return; }
    if (dateValue(row.target_date) === null || row.target_date < term.start_date || row.target_date > term.end_date) { error.value = '调课日期必须在当前学期内。'; return; }
  }
  await commit({ kind: 'override', body: { term_id: term.id, expected_revision: term.revision, slot_id: row.slot_id, source_date: row.source_date, action: row.action, reason: reason.value.trim(), confirm_conflicts: false,
    ...(row.action === 'move' ? { target_date: row.target_date, replacement: { ...row.fields } } : {}) } });
}); }
function restore(row: LessonChange) { run(async () => {
  if (!reason.value.trim()) { error.value = '请填写恢复原因。'; return; }
  await commit({ kind: 'override', body: { term_id: schedule.value!.term.id, expected_revision: schedule.value!.term.revision, slot_id: row.slot_id, source_date: row.source_date, action: 'restore', reason: reason.value.trim(), confirm_conflicts: false } });
}); }
watch(() => props.visible, visible => { if (visible && !initialized) run(() => loadTerms()); }, { immediate: true });
onUnmounted(() => { active = false; resetEdit(); });
</script>

<template>
  <view class="timetable-panel">
    <view v-if="error" class="notice warning" role="alert">{{ error }}</view>
    <view v-if="notice" class="notice" role="status">{{ notice }}</view>
    <view class="panel term-panel">
      <view class="section-head"><view><text class="eyebrow">A WEEK IN FOCUS</text><text class="section-title">学习课表</text><text class="muted">按学期安排每周课程，单次变化会保留调整记录。</text></view><FamilyButton class="secondary" :disabled="busy || dirty" @click="run(() => loadTerms())">刷新学期列表</FamilyButton></view>
      <view class="button-row term-list"><FamilyButton v-for="term in terms" :key="term.id" :class="['secondary', { chosen: schedule?.term.id === term.id }]" :disabled="busy || dirty || creating" @click="run(() => loadTerm(term.id))">{{ term.name }}</FamilyButton><FamilyButton v-if="next" class="secondary" :disabled="busy || dirty" @click="run(() => loadTerms(true))">更多学期</FamilyButton></view>
      <text v-if="!terms.length && !busy" class="empty-copy">还没有学期。{{ guardian ? '先创建一个学期，再安排每周课程。' : '请家长先创建学期和课程。' }}</text>
      <FamilyButton v-if="guardian && !creating" class="primary" :disabled="locked || dirty" @click="creating = true">创建学期</FamilyButton>
      <view v-if="guardian && creating" class="editor">
        <text class="section-title">新学期</text><text class="muted">学期日期和每周起始日创建后固定；单双周从包含开学日的第一周开始计算。</text>
        <view class="form-grid"><view><text>学期名称</text><FamilyInput v-model="termName" aria-label="学期名称" maxlength="80" :disabled="busy" /></view><view><text>开始日期</text><FamilyInput v-model="start" aria-label="学期开始日期" placeholder="2026-09-01" :disabled="busy" /></view><view><text>结束日期</text><FamilyInput v-model="end" aria-label="学期结束日期" placeholder="2027-01-31" :disabled="busy" /></view></view>
        <text class="field-label">每周起始日</text><view class="button-row"><FamilyButton v-for="(day, i) in weekdays" :key="day" :class="['secondary', { chosen: startsOn === i + 1 }]" :disabled="busy" @click="startsOn = i + 1">{{ day }}开始</FamilyButton></view>
        <view class="button-row"><FamilyButton class="primary" :disabled="busy" @click="createTerm">保存学期</FamilyButton><FamilyButton class="secondary" :disabled="busy" @click="creating = false">取消创建</FamilyButton></view>
      </view>
    </view>
    <template v-if="schedule">
      <view class="panel">
        <view class="section-head"><view><text class="section-title">{{ schedule.term.name }}</text><text class="muted">{{ schedule.term.start_date }} — {{ schedule.term.end_date }} · {{ weekdays[schedule.term.week_starts_on - 1] }}起始 · 北京时间</text></view><FamilyButton class="secondary" :disabled="busy" @click="run(() => loadTerm(schedule!.term.id, from || undefined))">{{ dirty ? '放弃草稿并刷新课表' : '刷新课表' }}</FamilyButton></view>
        <view v-if="needsReload" class="notice warning">请刷新课表核对最新内容后继续。</view>
        <view v-if="schedule.conflicts.length" class="conflicts"><text class="section-title">当前课表存在冲突</text><text v-for="(row, i) in schedule.conflicts" :key="i">{{ conflictLabel(row) }}</text></view>
        <view class="week-nav"><FamilyButton class="secondary" :disabled="locked || dirty || !from || from <= schedule.term.start_date" @click="run(() => readWeek(schedule!.term, shiftDate(from, -7)))">上一周</FamilyButton><text>{{ from }} — {{ from ? shiftDate(from, 6) : '' }}</text><FamilyButton class="secondary" :disabled="locked || dirty || !from || shiftDate(from, 7) > schedule.term.end_date" @click="run(() => readWeek(schedule!.term, shiftDate(from, 7)))">下一周</FamilyButton></view>
        <view v-if="!needsReload" class="week-agenda">
          <view v-for="day in days" :key="day.date" class="agenda-day"><view class="day-heading"><text class="day-name">{{ day.label }}</text><text class="muted">{{ day.date.slice(5) }}</text></view><text v-if="!day.items.length" class="empty-copy">{{ day.date < schedule.term.start_date || day.date > schedule.term.end_date ? '学期外' : '没有安排课程' }}</text><view v-for="row in day.items" :key="row.id" class="lesson-card"><view class="section-head"><text class="subject">{{ row.subject }}</text><text class="badge">第 {{ row.period }} 节</text></view><text class="muted">第 {{ row.week_number }} 周 · {{ row.start_time ? row.start_time + '–' + row.end_time : '未设置时间' }}{{ row.room ? ' · ' + row.room : '' }}</text><text v-if="row.overridden" class="change-note">由 {{ row.source_date }} 调整至此</text><view v-if="guardian" class="button-row"><FamilyButton class="secondary" :disabled="locked || dirty || creating" :aria-label="'调课 ' + row.source_date + ' ' + row.subject" @click="adjust(row, 'move')">调课</FamilyButton><FamilyButton class="secondary" :disabled="locked || dirty || creating" :aria-label="'停课 ' + row.source_date + ' ' + row.subject" @click="adjust(row, 'cancel')">停课</FamilyButton></view></view></view>
        </view>
      </view>
      <view v-if="guardian && adjustment" class="panel editor">
        <text class="section-title">{{ adjustment.action === 'move' ? '调整单次课程' : '停课确认' }}</text><text class="muted">原课程日期 {{ adjustment.source_date }}；只调整这一次课程。</text>
        <template v-if="adjustment.action === 'move'"><text class="field-label">调课日期</text><FamilyInput v-model="adjustment.target_date" aria-label="调课日期" :disabled="locked" /><LessonEditor v-model="adjustment.fields" prefix="调整课程" :disabled="locked" /></template>
        <text class="field-label">调整原因</text><FamilyInput v-model="reason" aria-label="调整原因" :disabled="locked" maxlength="1000" multiline />
        <view class="button-row"><FamilyButton class="primary" :disabled="locked" @click="submitAdjustment">{{ adjustment.action === 'move' ? '保存单次调课' : '确认单次停课' }}</FamilyButton><FamilyButton class="secondary" :disabled="busy" @click="resetEdit">取消调整</FamilyButton></view>
      </view>
      <view class="panel">
        <view class="section-head"><view><text class="section-title">每周课程模板</text><text class="muted">{{ schedule.slots.length }} 节 · 每周、单周与双周分别生效</text></view><FamilyButton v-if="guardian && !editing" class="secondary" :disabled="locked || dirty || creating" @click="editTemplate">编辑每周课程</FamilyButton></view>
        <template v-if="guardian && editing">
          <view v-for="(row, i) in draft" :key="row.id" class="slot-editor"><view class="section-head"><text class="section-title">课程 {{ i + 1 }}</text><FamilyButton class="secondary" :disabled="locked" @click="draft.splice(i, 1)">移除课程 {{ i + 1 }}</FamilyButton></view><view class="button-row"><FamilyButton v-for="(day, d) in weekdays" :key="day" :class="['secondary', { chosen: row.weekday === d + 1 }]" :aria-label="'课程' + (i + 1) + day" :disabled="locked" @click="row.weekday = d + 1">{{ day }}</FamilyButton></view><view class="button-row"><FamilyButton v-for="choice in (['all', 'odd', 'even'] as const)" :key="choice" :class="['secondary', { chosen: row.weeks === choice }]" :aria-label="'课程' + (i + 1) + ({ all: '每周', odd: '单周', even: '双周' })[choice]" :disabled="locked" @click="row.weeks = choice">{{ ({ all: '每周', odd: '单周', even: '双周' })[choice] }}</FamilyButton></view><LessonEditor :model-value="row" :prefix="'课程' + (i + 1)" :disabled="locked" @update:modelValue="Object.assign(row, $event)" /></view>
          <text v-if="!draft.length" class="empty-copy">保存空模板将清除每周课程；存在单次调停课时，请先恢复对应调整。</text><view class="button-row"><FamilyButton class="secondary" :disabled="locked || draft.length >= 100" @click="addSlot">添加课程</FamilyButton><FamilyButton class="primary" :disabled="locked" @click="saveTemplate">保存每周模板</FamilyButton><FamilyButton class="secondary" :disabled="busy" @click="resetEdit">取消编辑</FamilyButton></view>
        </template>
        <template v-else><text v-if="!schedule.slots.length" class="empty-copy">每周模板为空。</text><view v-for="row in schedule.slots" :key="row.id" class="template-row"><text>{{ weekdays[row.weekday - 1] }} · 第 {{ row.period }} 节 · {{ row.subject }}</text><text class="muted">{{ weekLabel(row) }} · {{ row.start_time ? row.start_time + '–' + row.end_time : '未设置时间' }}{{ row.room ? ' · ' + row.room : '' }}</text></view></template>
      </view>
      <view v-if="pending" class="panel conflict-confirm" role="alert"><text class="section-title">核对课程冲突</text><text class="muted">确认后将保留冲突课程。也可以返回编辑，调整日期、节次或时间。</text><text v-for="(row, i) in warnings" :key="i">{{ conflictLabel(row) }}</text><view class="button-row"><FamilyButton class="primary" :disabled="busy || needsReload" @click="run(() => commit(pending!, true))">确认保留冲突并保存</FamilyButton><FamilyButton class="secondary" :disabled="busy" @click="pending = null; warnings = []; error = ''">返回修改</FamilyButton></view></view>
      <view class="panel"><text class="section-title">单次调整记录</text><text v-if="!schedule.overrides.length" class="empty-copy">暂无调停课记录。</text>
        <view v-if="guardian && changes.length" class="restore-editor"><text class="muted">恢复后回到每周模板对应的原课程；如有新冲突，需要再次确认。</text><FamilyInput v-model="reason" aria-label="恢复原因" :disabled="locked || dirty" placeholder="填写恢复原因，再选择对应课程" maxlength="1000" /><view v-for="row in changes" :key="row.id" class="template-row"><text>{{ row.source_date }} · {{ schedule.slots.find(slot => slot.id === row.slot_id)?.subject }} · {{ actionLabel(row.action) }}{{ row.target_date ? '至 ' + row.target_date : '' }}</text><FamilyButton class="secondary" :disabled="locked || dirty || creating" :aria-label="'恢复 ' + row.source_date + ' ' + row.slot_id" @click="restore(row)">恢复原课程</FamilyButton></view></view>
        <view v-for="row in [...schedule.overrides].reverse()" :key="row.id" class="history-row"><text>{{ row.source_date }} · {{ actionLabel(row.action) }}{{ row.target_date ? '至 ' + row.target_date : '' }}</text><text class="muted">{{ row.reason }} · 版本 {{ row.revision }}</text></view>
      </view>
    </template>
  </view>
</template>

<style scoped>
.timetable-panel{display:grid;gap:20px;min-width:0}.panel{background:#fff;border:1px solid #dae5e9;border-radius:20px;padding:24px;min-width:0}.section-head{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap}.section-title{display:block;font-size:18px;font-weight:700;color:#20374b;margin-bottom:7px}.eyebrow{display:block;color:#087e80;font-size:11px;letter-spacing:2px;margin-bottom:8px}.muted{display:block;color:#647887;font-size:13px;line-height:1.7;overflow-wrap:anywhere}.button-row{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.term-list{margin-bottom:16px}.primary,.secondary{border-radius:10px;min-height:40px;padding:9px 15px;font-size:13px;margin:0;line-height:1.5}.primary{background:#087e80;color:white}.secondary{background:#f3f7f8;color:#20374b;border:1px solid #dae5e9}.chosen{background:#e1f2ef;color:#087e80;border-color:#087e80}.notice{padding:16px;border-radius:12px;background:#e1f2ef;color:#20374b;font-size:14px;line-height:1.7}.warning,.conflicts,.conflict-confirm{background:#fff7e9;border-color:#efd6a4;color:#795415}.conflicts{padding:16px;margin-top:16px;border-radius:12px}.conflicts>text,.conflict-confirm>text{display:block;line-height:1.8}.week-nav{display:flex;gap:12px;justify-content:space-between;align-items:center;flex-wrap:wrap;margin:22px 0;font-size:13px;color:#647887}.week-agenda{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}.agenda-day{background:#f3f7f8;border-radius:14px;padding:14px;min-width:0}.day-heading{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #dae5e9;padding-bottom:12px}.day-name{font-weight:700;color:#20374b}.lesson-card{background:#fff;border-left:3px solid #087e80;border-radius:9px;padding:12px;margin-top:12px}.subject{font-weight:700;color:#20374b;overflow-wrap:anywhere}.badge{font-size:11px;color:#087e80}.change-note{display:block;font-size:12px;color:#087e80;margin-top:6px}.empty-copy{display:block;color:#647887;font-size:13px;padding:16px 0;line-height:1.7}.editor,.slot-editor{display:grid;gap:12px}.editor{margin-top:16px}.slot-editor{padding:18px 0;border-bottom:1px solid #dae5e9}.field-label,.form-grid text{display:block;color:#647887;font-size:13px;margin:8px 0}.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px}.editor input,.editor textarea,.restore-editor input{box-sizing:border-box;width:100%;padding:10px;border:1px solid #dae5e9;border-radius:10px;background:#fff;color:#20374b;font:inherit;min-height:42px}.editor textarea{height:90px}.template-row,.history-row{padding:14px 0;border-bottom:1px solid #edf2f4;font-size:14px;line-height:1.8;overflow-wrap:anywhere}.template-row>text,.history-row>text{display:block}.restore-editor{margin-top:15px;padding:15px;background:#f3f7f8;border-radius:12px}.restore-editor input{margin-top:10px}@media(max-width:520px){.panel{padding:18px}.week-agenda{grid-template-columns:1fr}.week-nav{gap:8px}.week-nav>text{order:-1;width:100%;text-align:center}.form-grid{grid-template-columns:1fr}}
</style>
