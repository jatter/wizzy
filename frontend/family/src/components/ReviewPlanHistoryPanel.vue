<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue';
import { ApiError } from '../api';
import type { FamilyApi, ReportType, ReviewPlan, ReviewPlanSummary, ReviewTask } from '../api';
import { beijingDate, reportPeriod } from '../reports';
import FamilyButton from './FamilyButton.vue';

const props = defineProps<{ api: FamilyApi; student: string; guardian: boolean }>();
const emit = defineEmits<{ task: [task: ReviewTask]; reports: [] }>();
const generation = props.api.generation, student = props.student;
let active = true;
const valid = () => active && props.student === student && props.api.generation === generation;
const busy = ref(false), error = ref(''), loaded = ref(false);
const filter = ref<ReportType | ''>('');
const history = ref<ReviewPlanSummary[]>([]), next = ref<string | null>(null);
const plan = ref<ReviewPlan | null>(null);
const range = (p: ReviewPlanSummary) => p.period_start + ' — ' + (reportPeriod(p.period_start, p.type)?.last ?? p.period_end);
const kind = (p: ReviewPlanSummary) => p.type === 'weekly' ? '周复习计划' : '月复习计划';
const state = (t: ReviewTask | null) => !t ? '任务已不可访问' : !t.source_is_current ? '来源已更新' : t.state === 'awaiting_review' ? '已提交待核验' : t.state === 'open' ? '待复习' : '已关闭';
async function run(work: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true; error.value = '';
  try { await work(); } catch (e) {
    if (valid() && !(e instanceof ApiError && e.code === 'stale')) {
      plan.value = null;
      if (e instanceof ApiError && [401, 403, 404].includes(e.status)) {
        history.value = []; next.value = null; loaded.value = false;
      }
      error.value = e instanceof ApiError ? e.message : '读取未完成，请重试。';
    }
  } finally { if (valid()) busy.value = false; }
}
async function load(value: ReportType | '' = filter.value, more = false) {
  await run(async () => {
    const cursor = more ? next.value ?? undefined : undefined;
    if (!more) { filter.value = value; history.value = []; next.value = null; plan.value = null; loaded.value = false; }
    const page = await props.api.plans(student, value || undefined, cursor);
    if (!valid()) return;
    history.value = more ? [...history.value, ...page.items] : page.items;
    next.value = page.next_cursor; loaded.value = true;
  });
}
async function open(id: string) {
  await run(async () => {
    plan.value = null;
    const detail = await props.api.plan(id);
    if (valid()) plan.value = detail;
  });
}
onMounted(() => { void load(); });
onUnmounted(() => { active = false; });
</script>

<template>
  <view class="panel plan-history" :aria-busy="busy" aria-label="复习计划历史">
    <view class="section-head plan-heading"><view><text class="eyebrow">我的复习安排</text><text class="section-title">复习计划历史</text><text class="muted">按周或按月回看安排，继续上一次的练习。</text></view><FamilyButton class="secondary compact" :disabled="busy" @click="load()">刷新计划列表</FamilyButton></view>
    <view class="segmented plan-filters" aria-label="计划周期筛选"><FamilyButton :class="{ active: filter === '' }" :aria-pressed="filter === ''" :disabled="busy" @click="load('')">全部计划</FamilyButton><FamilyButton :class="{ active: filter === 'weekly' }" :aria-pressed="filter === 'weekly'" :disabled="busy" @click="load('weekly')">周计划</FamilyButton><FamilyButton :class="{ active: filter === 'monthly' }" :aria-pressed="filter === 'monthly'" :disabled="busy" @click="load('monthly')">月计划</FamilyButton></view>
    <text v-if="error" class="notice error" role="alert">{{ error }}</text>
    <text v-if="busy" class="muted" role="status">正在读取计划…</text>
    <view v-if="loaded && !history.length" class="plan-empty"><text class="section-title">{{ filter ? '这个周期还没有复习计划' : '还没有复习计划' }}</text><text class="muted">{{ guardian ? '到学习报告选择周报或月报，即可安排下一次复习。' : '家长安排复习后，可以在这里查看计划。' }}</text><FamilyButton v-if="guardian" class="secondary compact" :disabled="busy" @click="emit('reports')">去学习报告安排复习</FamilyButton></view>
    <view class="plan-history-grid"><FamilyButton v-for="p in history" :key="p.id" class="plan-index" :class="{ selected: plan?.id === p.id }" :aria-pressed="plan?.id === p.id" :aria-label="'查看' + kind(p) + ' ' + range(p)" :disabled="busy" @click="open(p.id)"><view class="section-head"><text class="plan-kind">{{ kind(p) }}</text><text class="plan-open">查看计划 →</text></view><text class="plan-period">{{ range(p) }}</text><text class="plan-metrics">{{ p.question_count }} 题 <text class="muted">· 预计 {{ p.estimated_minutes }} 分钟</text></text><text class="small muted">{{ beijingDate(Date.parse(p.created_at)) }} 生成 · 北京时间</text></FamilyButton></view>
    <FamilyButton v-if="next" class="secondary load-more" :disabled="busy" @click="load(filter, true)">加载更多计划</FamilyButton>
    <view v-if="plan" class="plan-detail" aria-label="复习计划详情"><view class="section-head plan-heading"><view><text class="section-title">{{ kind(plan) }}</text><text class="muted">{{ range(plan) }} · 北京时间</text></view><FamilyButton class="text-button compact" :disabled="busy" @click="plan = null">收起计划详情</FamilyButton></view><text class="plan-detail-total">{{ plan.question_count }} 题 · 预计 {{ plan.estimated_minutes }} 分钟</text><text v-if="plan.stale" class="notice warning">计划来源已变化，请核对当前任务，或请家长根据最新报告重新安排。</text><text v-if="!plan.entries.length" class="muted">生成时没有符合条件的待复习题目。</text><view v-for="entry in plan.entries" :key="entry.task_id" class="history-task"><text class="task-number">{{ String(entry.position).padStart(2, '0') }}</text><view class="history-task-body"><view class="section-head"><text class="badge">{{ state(entry.task) }}</text><text class="small muted">{{ entry.due_date }}</text></view><text class="question-stem">{{ entry.task?.stem || '当前无法读取这道题，请刷新计划。' }}</text><text class="small muted">{{ entry.priority === 'overdue' ? '安排依据：生成时已逾期' : entry.priority === 'repeated_error' ? '安排依据：重复出错' : '安排依据：待复习任务' }}</text><FamilyButton v-if="entry.task" class="secondary compact" :disabled="busy" @click="emit('task', entry.task)">{{ guardian ? '查看复习记录' : '打开复习' }}</FamilyButton></view></view><FamilyButton class="secondary compact" :disabled="busy" @click="open(plan.id)">刷新计划详情</FamilyButton></view>
    <text class="small muted">{{ guardian ? '题量与预计时长记录生成时的安排，预计时长不是实际学习用时。打开详情会读取当前任务状态；已提交的作答仍需核验，不代表已经掌握。' : '预计时长不是实际学习用时。提交后请等待家长核对。' }}</text>
  </view>
</template>

<style scoped>
.plan-history{border-top:4px solid var(--teal);display:flex;flex-direction:column;gap:20px}.plan-heading{align-items:flex-start;gap:16px}.plan-heading>view{display:flex;flex-direction:column;gap:8px;min-width:0}.plan-filters{align-self:flex-start}.plan-history-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.plan-index{display:flex;flex-direction:column;gap:10px;padding:20px;border:1px solid var(--line);border-radius:12px;background:#fff;text-align:left;white-space:normal;line-height:1.5;min-width:0}.plan-index.selected{border-color:var(--teal);background:#eff8f7}.plan-index .section-head{width:100%;gap:8px}.plan-kind{font-weight:650;color:var(--ink)}.plan-open{font-size:12px;color:var(--teal)}.plan-period{font-size:14px;color:var(--muted);overflow-wrap:anywhere}.plan-metrics{font-size:19px;font-weight:650;color:var(--ink)}.plan-metrics .muted{font-size:14px;font-weight:400}.plan-empty{background:var(--paper);border-radius:10px;padding:22px;display:flex;flex-direction:column;align-items:flex-start;gap:12px}.plan-detail{border-top:1px solid var(--line);padding-top:24px;display:flex;flex-direction:column;gap:18px}.plan-detail-total{font-size:22px;font-weight:650;color:var(--teal)}.history-task{display:flex;align-items:flex-start;gap:14px;padding:18px 0;border-bottom:1px solid var(--line)}.history-task-body{min-width:0;flex:1;display:flex;flex-direction:column;align-items:flex-start;gap:12px}.history-task-body .section-head{width:100%;gap:8px;flex-wrap:wrap}.history-task-body .question-stem{margin:0;overflow-wrap:anywhere}
@media(max-width:760px){.plan-history-grid{grid-template-columns:1fr}.plan-heading{flex-wrap:wrap}.plan-index{padding:16px}.plan-filters{max-width:100%}.plan-filters .family-button{padding:9px 12px}.plan-detail-total{font-size:20px}}
</style>
