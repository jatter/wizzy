<script setup lang="ts">
import { computed, onUnmounted, ref, shallowReactive, watch } from 'vue';
import { ApiError, operationId } from '../api';
import type { FamilyApi, LearningReport, ReportSource, ReportType, ReviewPlan, ReviewPlanBody } from '../api';
import { accuracy, beijingDate, reportPeriod } from '../reports';
import FamilyButton from './FamilyButton.vue';
import FamilyInput from './FamilyInput.vue';

const props = defineProps<{ api: FamilyApi; student: string; visible: boolean }>();
const emit = defineEmits<{ learning: [] }>();
const generation = props.api.generation, student = props.student;
let active = true, initialized = false;
const valid = () => active && props.student === student && props.api.generation === generation;
const busy = ref(false), error = ref('');
const type = ref<ReportType>('weekly'), date = ref(beijingDate());
const period = computed(() => reportPeriod(date.value, type.value));
const history = ref<LearningReport[]>([]), next = ref<string | null>(null);
const filter = ref<ReportType | ''>('');
const report = ref<LearningReport | null>(null), sources = ref<ReportSource[]>([]), nextSource = ref<string | null>(null);
const plans = ref<Record<string, ReviewPlan>>({});
const pending = shallowReactive(new Map<string, { body: ReviewPlanBody; key: string }>());
const plan = computed(() => report.value ? plans.value[report.value.id] : undefined);
const pendingPlan = computed(() => !!report.value && !plan.value && pending.has(report.value.id));
const range = (r: LearningReport) => reportPeriod(r.period_start, r.type);
const kindLabel = (s: ReportSource) => ({ original: '原始作答', correction: '订正记录', review: '复习记录' })[s.kind];
const verdictLabel = (s: ReportSource) => s.kind === 'review' ? '待核验' : ({ correct: '正确', incorrect: '错误', pending_review: '待确认', unanswered: '未作答', unsupported: '暂不支持' }[s.snapshot.verdict] ?? '待确认');
async function run(work: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true; error.value = '';
  try { await work(); } catch (e) {
    if (valid() && !(e instanceof ApiError && e.code === 'stale')) error.value = e instanceof ApiError ? e.message : '操作未完成，请重试。';
  } finally { if (valid()) busy.value = false; }
}
async function loadHistory(value: ReportType | '' = filter.value, more = false) {
  const result = await props.api.reports(student, value || undefined, more ? next.value ?? undefined : undefined);
  if (!valid()) return;
  history.value = more ? [...history.value, ...result.items] : result.items;
  next.value = result.next_cursor; filter.value = value;
}
async function open(id: string) {
  const [detail, page] = await Promise.all([props.api.report(id), props.api.reportSources(id)]);
  if (!valid()) return;
  report.value = detail; sources.value = page.items; nextSource.value = page.next_cursor;
}
async function generate() {
  const selectedPeriod = period.value;
  if (!selectedPeriod) return;
  await run(async () => {
    const result = await props.api.generateReport(student, type.value, selectedPeriod.start);
    if (!valid()) return;
    await open(result.id); if (valid()) await loadHistory();
  });
}
async function rebuild() {
  if (!report.value) return;
  const id = report.value.id;
  await run(async () => {
    const result = await props.api.rebuildReport(id);
    if (!valid()) return;
    await open(result.id); if (valid()) await loadHistory();
  });
}
async function moreSources() {
  if (!report.value || !nextSource.value) return;
  const id = report.value.id, cursor = nextSource.value;
  await run(async () => {
    const result = await props.api.reportSources(id, cursor);
    if (!valid() || report.value?.id !== id) return;
    sources.value = [...sources.value, ...result.items]; nextSource.value = result.next_cursor;
  });
}
async function generatePlan() {
  const current = report.value;
  if (!current || current.stale || plan.value) return;
  if (!pending.has(current.id)) pending.set(current.id, { key: operationId(), body: {
    student_id: student, report_id: current.id, type: current.type, period_start: current.period_start,
    max_questions: 20, max_minutes: 20, minutes_per_question: 2,
  } });
  const frozen = pending.get(current.id)!;
  await run(async () => {
    const result = await props.api.generatePlan(frozen.body, frozen.key);
    if (!valid()) return;
    plans.value[current.id] = result; pending.delete(current.id);
  });
}
async function refreshPlan() {
  const current = plan.value, id = report.value?.id;
  if (!current || !id) return;
  await run(async () => {
    const result = await props.api.plan(current.id);
    if (valid()) plans.value[id] = result;
  });
}
watch(() => props.visible, visible => {
  if (visible && !initialized) void run(async () => { await loadHistory(); if (valid()) initialized = true; });
}, { immediate: true });
onUnmounted(() => { active = false; pending.clear(); });
</script>

<template>
  <view class="reports-panel">
    <view class="panel report-builder">
      <view class="section-head"><view><text class="eyebrow">LEARNING JOURNAL</text><text class="section-title">把这一段学习，认真看一遍</text></view><text class="badge">北京时间</text></view>
      <text class="muted">按周或按月整理真实记录，再安排下一次复习。</text>
      <view class="report-controls">
        <view class="segmented"><FamilyButton :class="{ chosen: type === 'weekly' }" :disabled="busy" @click="type = 'weekly'">周报</FamilyButton><FamilyButton :class="{ chosen: type === 'monthly' }" :disabled="busy" @click="type = 'monthly'">月报</FamilyButton></view>
        <view class="period-field"><text class="field-label">选择周期内任意一天</text>
          <!-- #ifdef H5 -->
          <FamilyInput v-model="date" type="date" aria-label="报告日期" class="field" :disabled="busy" />
          <!-- #endif -->
          <!-- #ifndef H5 -->
          <picker mode="date" :value="date" start="2000-01-03" end="9998-12-31" :disabled="busy" @change="date = $event.detail.value"><view class="field">{{ date }}</view></picker>
          <!-- #endif -->
        </view>
        <FamilyButton class="primary" :disabled="busy || !period" @click="generate">生成学习报告</FamilyButton>
      </view>
      <text v-if="period" class="small muted">{{ period.start }} 至 {{ period.last }} · {{ type === 'weekly' ? '周一至周日' : '自然月' }}，统计截止于下一周期开始前。</text>
      <text v-else class="notice warning">请选择 2000–9998 年内的有效日期，且周报起点不早于 2000 年。</text>
    </view>
    <text v-if="error" class="notice error" role="alert">{{ error }}</text><text v-if="busy" class="muted" role="status">正在整理学习记录…</text>
    <view class="section-head list-heading"><view><text class="section-title">报告档案</text><text class="muted">保留每次生成的版本，可查看原始统计依据。</text></view><FamilyButton class="secondary compact" :disabled="busy" @click="run(() => loadHistory())">刷新报告列表</FamilyButton></view>
    <view class="segmented report-filters"><FamilyButton v-for="f in (['', 'weekly', 'monthly'] as const)" :key="f || 'all'" :class="{ chosen: filter === f }" :disabled="busy" @click="run(() => loadHistory(f))">{{ f === 'weekly' ? '只看周报' : f === 'monthly' ? '只看月报' : '全部报告' }}</FamilyButton></view>
    <view v-if="!history.length && !busy" class="panel empty-state"><text class="section-title">还没有学习报告</text><text class="muted">选择一个周期生成报告；没有记录的周期也会如实显示。</text></view>
    <view class="report-history"><FamilyButton v-for="r in history" :key="r.id" class="report-index" :class="{ selected: report?.id === r.id }" :disabled="busy" @click="run(() => open(r.id))"><view><text class="report-index-title">{{ r.type === 'weekly' ? '周报' : '月报' }} · {{ r.period_start }}</text><text class="small muted">版本 {{ r.revision }} · {{ r.source_count }} 条统计来源</text></view><text class="badge" :class="{ warning: r.stale }">{{ r.stale ? '来源已变化' : '查看报告' }}</text></FamilyButton></view>
    <FamilyButton v-if="next" class="secondary load-more" :disabled="busy" @click="run(() => loadHistory(filter, true))">加载更多报告</FamilyButton>
    <view v-if="report" class="panel report-folio">
      <view class="folio-heading"><view><text class="eyebrow">{{ report.type === 'weekly' ? 'WEEKLY' : 'MONTHLY' }} REVIEW</text><text class="folio-title">这一{{ report.type === 'weekly' ? '周' : '月' }}的学习足迹</text><text class="muted">{{ report.period_start }} 至 {{ range(report)?.last }} · 版本 {{ report.revision }}</text></view><FamilyButton class="secondary compact" :disabled="busy" @click="run(() => open(report!.id))">刷新报告状态</FamilyButton></view>
      <view v-if="report.stale" class="notice warning"><text>来源已变化，当前展示的是生成时的历史记录。重建后会保留旧版并打开最新版本。</text><FamilyButton class="secondary compact" :disabled="busy" @click="rebuild">重建学习报告</FamilyButton></view>
      <view class="report-total"><text class="total-number">{{ report.summary.submissions }}</text><view><text class="section-title">次作业提交</text><text class="small muted">提交数量用于记录练习，不代表掌握程度。</text></view></view>
      <view class="evidence-grid">
        <view class="evidence-block"><text class="section-title">原始作答</text><text class="accuracy">{{ accuracy(report.summary.original.accuracy) }}</text><text class="small muted">正确 {{ report.summary.original.correct }} / 有效判定 {{ report.summary.original.effective }} 题</text><text class="body-copy">错误 {{ report.summary.original.incorrect }} · 待确认 {{ report.summary.original.pending_review }}</text><text class="small muted">收录 {{ report.summary.original.accepted }} · 未作答 {{ report.summary.original.unanswered }} · 暂不支持 {{ report.summary.original.unsupported }}</text></view>
        <view class="evidence-block"><text class="section-title">订正记录</text><text class="accuracy">{{ accuracy(report.summary.corrections.accuracy) }}</text><text class="small muted">正确 {{ report.summary.corrections.correct }} / 已判定 {{ report.summary.corrections.correct + report.summary.corrections.incorrect }} 次</text><text class="body-copy">尝试 {{ report.summary.corrections.attempts }} · 错误 {{ report.summary.corrections.incorrect }}</text><text class="small muted">待确认 {{ report.summary.corrections.pending_review }} 次，未计入正确率。</text></view>
        <view class="evidence-block"><text class="section-title">复习记录</text><text class="accuracy">{{ report.summary.reviews.attempts }}<text class="unit"> 次尝试</text></text><text class="body-copy">已提交待核验 {{ report.summary.reviews.awaiting_review }} 次</text><text class="small muted">需要帮助 {{ report.summary.reviews.needs_help }} · 跳过 {{ report.summary.reviews.skipped }} · 已完成 {{ report.summary.reviews.completed }}</text><text class="small muted">提交作答仍需核验，不作为独立做对的证据。</text></view>
      </view>
      <view class="weak-evidence"><text class="section-title">薄弱知识点</text><text class="muted">当前证据不足，暂不生成知识点薄弱结论。可先回看下方题目与作答记录。</text></view>
      <view class="section-head list-heading"><view><text class="section-title">统计来源</text><text class="muted">{{ report.source_count }} 条记录，按生成时的内容留存。</text></view></view>
      <text v-if="!sources.length" class="empty-copy">本周期没有可纳入统计的题目、订正或复习记录。</text>
      <view v-for="s in sources" :key="s.id" class="source-row"><view class="section-head"><text class="badge">{{ kindLabel(s) }}</text><text class="small muted">{{ verdictLabel(s) }}{{ s.snapshot.verdict_provenance === 'guardian' ? ' · 家长确认' : '' }}</text></view><text class="question-stem">{{ s.snapshot.stem || (s.kind === 'correction' ? '对应原题的订正记录' : '题目记录') }}</text><text class="body-copy">作答：{{ s.snapshot.student_answer_raw || s.snapshot.student_answer || '未填写文字作答' }}</text><text v-if="s.kind === 'review'" class="small muted">{{ s.snapshot.outcome === 'needs_help' ? '已请求帮助' : s.snapshot.outcome === 'skipped' ? '本次已跳过' : '已提交作答' }} · 待核验</text><text v-if="s.snapshot.source_is_current === false" class="small muted">生成时，题目来源已更新。</text></view>
      <FamilyButton v-if="nextSource" class="secondary load-more" :disabled="busy" @click="moreSources">加载更多统计来源</FamilyButton>
      <view class="report-plan"><view class="section-head"><text class="section-title">安排下一次复习</text><text class="badge">预计时长</text></view><text class="muted">优先安排逾期和重复出错的题目。每题按 2 分钟估算，总计不超过 20 分钟，最多 10 题；与学习页共用复习任务。</text>
        <FamilyButton v-if="!plan" class="primary" :disabled="busy || report.stale" @click="generatePlan">{{ pendingPlan ? '重试生成复习计划' : '生成复习计划' }}</FamilyButton>
        <text v-if="pendingPlan" class="small muted">上次请求尚未确认；重试使用同一份计划请求，避免重复生成。</text>
        <template v-if="plan"><text class="plan-summary">{{ plan.question_count }} 题 · 预计 {{ plan.estimated_minutes }} 分钟</text><text v-if="plan.stale" class="notice warning">计划来源已变化，请核对任务并基于最新报告安排复习。</text><text v-if="!plan.entries.length" class="empty-copy">当前没有符合条件的待复习题目。</text><view v-for="entry in plan.entries" :key="entry.task_id" class="plan-entry"><text class="task-number">{{ entry.position }}</text><view><text class="body-copy">{{ entry.task?.stem || '任务已不可访问' }}</text><text class="small muted">{{ entry.priority === 'overdue' ? '优先：已逾期' : entry.priority === 'repeated_error' ? '优先：重复出错' : '待复习' }} · {{ entry.due_date }} · {{ entry.task?.state === 'awaiting_review' ? '已提交待核验' : entry.task?.state === 'open' ? '待复习' : '已关闭或不可访问' }}</text></view></view><view class="button-row"><FamilyButton class="secondary" :disabled="busy" @click="refreshPlan">刷新复习计划</FamilyButton><FamilyButton class="primary" :disabled="busy" @click="emit('learning')">去学习页查看任务</FamilyButton></view></template>
        <text class="small muted">已生成的计划可在学习页的“复习计划历史”中重新打开。预计时长不是实际学习用时。</text>
      </view>
    </view>
  </view>
</template>

<style scoped>
.reports-panel{display:flex;flex-direction:column;gap:16px}.report-builder{border-top:4px solid #087e80}.report-controls{display:flex;align-items:flex-end;flex-wrap:wrap;gap:16px;margin:22px 0 14px}.period-field{flex:1;min-width:180px}.report-filters{align-self:flex-start}.report-history{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.report-index{display:flex;align-items:center;justify-content:space-between;gap:12px;text-align:left;padding:18px;background:white;border:1px solid #dae5e9;border-radius:12px;line-height:1.5;white-space:normal}.report-index.selected{border-color:#087e80;background:#eff8f7}.report-index-title{display:block;font-weight:650;color:#20374b}.report-folio{padding:32px}.folio-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:20px}.folio-title{display:block;font-size:28px;font-weight:700;letter-spacing:-1px;margin:8px 0 10px}.report-total{display:flex;align-items:center;gap:20px;padding:28px 0;border-bottom:1px solid #dae5e9}.total-number{font-size:64px;font-weight:650;color:#087e80;line-height:1}.evidence-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));margin:24px 0}.evidence-block{padding:8px 20px;display:flex;flex-direction:column;gap:10px;border-left:1px solid #dae5e9}.evidence-block:first-child{padding-left:0;border-left:0}.accuracy{display:block;font-size:29px;font-weight:650;color:#087e80;line-height:1.4}.unit{font-size:14px;color:#647887;font-weight:400}.weak-evidence{background:#f3f7f8;padding:20px;border-radius:10px;display:flex;flex-direction:column;gap:8px}.source-row{padding:20px 0;border-bottom:1px solid #dae5e9}.source-row .body-copy{white-space:pre-wrap;overflow-wrap:anywhere}.report-plan{margin-top:28px;border-top:2px solid #087e80;padding-top:22px;display:flex;flex-direction:column;gap:14px}.plan-summary{font-size:22px;font-weight:650;color:#087e80}.plan-entry{display:flex;align-items:flex-start;gap:14px;padding:12px 0;border-bottom:1px solid #dae5e9}.plan-entry>view{min-width:0;overflow-wrap:anywhere}.report-folio .notice{display:flex;flex-wrap:wrap;align-items:center;gap:12px;margin-top:16px}
@media(max-width:760px){.report-history,.evidence-grid{grid-template-columns:1fr}.report-folio{padding:20px}.folio-heading{flex-direction:column}.folio-title{font-size:24px}.evidence-block,.evidence-block:first-child{border-left:0;border-bottom:1px solid #dae5e9;padding:18px 0}.report-index{padding:14px}.report-controls>.primary{width:100%}.accuracy{font-size:26px}}
</style>
