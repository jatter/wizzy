<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { HomeworkPageResult } from '../api';
import FamilyButton from './FamilyButton.vue';
import star from '../static/icons/star.svg';
import sprout from '../static/icons/sprout.svg';

const props = defineProps<{ position: number; original?: string; result?: HomeworkPageResult; pending: boolean }>();
const subjects: Record<string, string> = { math: '数学', english: '英语', chinese: '语文', mixed: '多学科', unknown: '学科待确认' };
const verdicts = { correct: '正确', incorrect: '错误', unanswered: '未作答', unclear: '待核对' };
const praise = ['你真棒', '这题稳稳的', '写得很清楚', '小知给你一颗星'];
const cheer = ['继续加油，弄懂这一步就进步了', '差一点点，我们再看一眼', '没关系，慢慢来', '这题有点绕，先看你写的那一步'];
type Filter = 'incorrect' | 'correct' | 'all';
const filter = ref<Filter>('incorrect');
const openSolutions = ref<string[]>([]);
const spellingOnly = computed(() => props.result?.questions.some(question => question.grading_basis === 'spelling_only') ?? false);
const vocabulary = computed(() => props.result?.questions.some(question => question.item_type === 'word' || question.item_type === 'phrase') ?? false);
watch(() => [props.position, props.result?.counts.incorrect, props.result?.counts.correct, props.result?.counts.unclear, props.result?.counts.unanswered, spellingOnly.value], () => {
  const counts = props.result?.counts;
  filter.value = counts && !counts.incorrect && (counts.unclear || counts.unanswered || spellingOnly.value) ? 'all' : 'incorrect';
  openSolutions.value = [];
}, { immediate: true });

const shown = computed(() => {
  const questions = props.result?.questions ?? [];
  return filter.value === 'all' ? questions : questions.filter(question => question.verdict === filter.value);
});
const mood = computed(() => {
  const counts = props.result?.counts;
  if (!counts || !shown.value.length) return '';
  if (filter.value === 'incorrect') return `这 ${counts.incorrect} 题先看一看，弄懂了就赚到啦`;
  if (filter.value === 'correct') return spellingOnly.value ? `已通过 ${counts.correct} 项检查，请结合检查范围核对` : `做对了 ${counts.correct} 题，给自己点个赞`;
  return '';
});
const extraNote = computed(() => {
  const counts = props.result?.counts;
  if (!counts || filter.value === 'all') return '';
  const parts = [counts.unanswered ? `${counts.unanswered} 题未作答` : '', counts.unclear ? `${counts.unclear} 题待核对` : ''].filter(Boolean);
  return parts.length ? `还有 ${parts.join('、')}，在「全部」里对着原图看。` : '';
});

function line(number: string, verdict: 'correct' | 'incorrect') {
  const lines = verdict === 'correct' ? praise : cheer;
  let hash = 0;
  for (let i = 0; i < number.length; i++) hash = (hash + number.charCodeAt(i) * (i + 1)) % 997;
  return lines[hash % lines.length];
}
function toggle(number: string) {
  openSolutions.value = openSolutions.value.includes(number) ? openSolutions.value.filter(item => item !== number) : [...openSolutions.value, number];
}
function enlarge(url: string) { uni.previewImage({ urls: [url], current: url }); }
</script>

<template>
  <view class="image-result" :aria-label="'第' + position + '张作业详情'">
    <view class="original-column">
      <view class="section-head"><text class="section-title">第 {{ position }} 张 · 原图</text><text v-if="result" class="badge">{{ subjects[result.subject] }}</text></view>
      <image v-if="original" :src="original" mode="widthFix" class="original-image" :alt="'已上传作业第' + position + '张原图'" @click="enlarge(original)" />
      <text v-else class="notice">原图正在加载，可使用“刷新处理状态”重试。</text>
    </view>
    <view class="answers-column">
      <text class="section-title">批改结果</text>
      <template v-if="result">
        <view v-if="result.questions.length" class="verdict-tabs" role="tablist" aria-label="本张批改结果">
          <FamilyButton class="verdict-tab incorrect" :class="{ selected: filter === 'incorrect' }" role="tab" :aria-selected="filter === 'incorrect'" @click="filter = 'incorrect'">错题({{ result.counts.incorrect }})</FamilyButton>
          <FamilyButton class="verdict-tab correct" :class="{ selected: filter === 'correct' }" role="tab" :aria-selected="filter === 'correct'" @click="filter = 'correct'">{{ spellingOnly ? '通过' : '对题' }}({{ result.counts.correct }})</FamilyButton>
          <FamilyButton class="verdict-tab" :class="{ selected: filter === 'all' }" role="tab" :aria-selected="filter === 'all'" @click="filter = 'all'">全部</FamilyButton>
        </view>
        <view v-if="mood" class="mood" :class="filter"><image class="mood-icon" :src="filter === 'correct' ? star : sprout" aria-hidden="true" /><text>{{ mood }}</text></view>
        <text v-if="filter === 'all'" class="small muted">按本张已识别的{{ vocabulary ? '题目与词条' : '小题' }}统计，模型判断供参考。</text>
        <text v-if="spellingOnly" class="notice">部分词条仅检查拼写，拼写未见错误不代表听写正确；听写目标、词义、漏写与顺序仍需参考材料核对。</text>
        <text v-if="extraNote" class="small muted">{{ extraNote }}</text>
        <text v-if="result.note" class="notice">{{ result.note }}</text>
        <text v-if="!result.questions.length" class="notice">本张未识别到可批改题目，请核对原图。</text>
        <view v-else-if="!shown.length" class="empty-verdict" :class="filter === 'correct' ? 'correct' : 'clear'">
          <template v-if="filter === 'incorrect' && !result.counts.incorrect && !result.counts.unclear && !result.counts.unanswered && !spellingOnly">
            <image class="mood-icon" :src="star" aria-hidden="true" />
            <text class="section-title">这张没有错题，你真棒</text>
            <FamilyButton class="text-button compact" @click="filter = 'correct'">去看看做对的题</FamilyButton>
          </template>
          <template v-else-if="filter === 'correct' && !result.counts.correct && !result.counts.unclear && !result.counts.unanswered">
            <image class="mood-icon" :src="sprout" aria-hidden="true" />
            <text class="section-title">这张还没有做对的题</text>
            <FamilyButton class="text-button compact" @click="filter = 'incorrect'">先看错题，一题一题来</FamilyButton>
          </template>
          <text v-else class="notice">这一栏暂时没有可展开的题目，可到「全部」查看。</text>
        </view>
        <view v-for="question in shown" :key="question.number" class="answer-question" :class="question.verdict">
          <view class="section-head">
            <view class="question-title">
              <image v-if="question.verdict === 'correct' || question.verdict === 'incorrect'" class="verdict-icon" :src="question.verdict === 'correct' ? star : sprout" aria-hidden="true" />
              <text class="field-label">{{ question.number }}</text>
            </view>
            <text class="badge">{{ question.grading_basis === 'spelling_only' ? { correct: '拼写未见错误', incorrect: '拼写错误', unclear: '待核对', unanswered: '未作答' }[question.verdict] : verdicts[question.verdict] }}</text>
          </view>
          <text v-if="question.grading_basis !== 'spelling_only' && (question.verdict === 'correct' || question.verdict === 'incorrect')" class="cheer" :class="question.verdict">{{ line(question.number, question.verdict) }}</text>
          <text class="small muted">原作答</text><text class="answer-text">{{ question.student_answer || '未识别到作答' }}</text>
          <template v-if="filter !== 'correct' || openSolutions.includes(question.number)">
            <text class="field-label answer-label">{{ question.item_type === 'word' || question.item_type === 'phrase' ? '检查依据' : '解题思路' }}</text><text class="answer-text">{{ question.reasoning || '信息不足，暂不能判断。' }}</text>
            <template v-if="question.steps.length"><text class="field-label answer-label">{{ question.item_type === 'word' || question.item_type === 'phrase' ? '订正建议' : '解题过程' }}</text><text v-for="(step, index) in question.steps" :key="index" class="answer-text solution-step">{{ index + 1 }}. {{ step }}</text></template>
          </template>
          <FamilyButton v-if="filter === 'correct' && (question.steps.length || question.reasoning)" class="text-button compact" @click="toggle(question.number)">{{ question.item_type === 'word' || question.item_type === 'phrase' ? (openSolutions.includes(question.number) ? '收起检查依据' : '查看检查依据') : (openSolutions.includes(question.number) ? '收起解题过程' : '想再看看怎么做的') }}</FamilyButton>
          <template v-if="question.answer"><text class="field-label answer-label">{{ question.grading_basis === 'spelling_only' ? '参考拼写' : '正确答案' }}</text><text class="answer-text final-answer">{{ question.answer }}</text></template>
        </view>
      </template>
      <text v-else class="notice">{{ pending ? '正在逐张识别学科并批改，结果返回后会自动显示。' : '这份记录尚无可核对的逐图统计。可在下方查看已保存的原始批改记录。' }}</text>
    </view>
  </view>
</template>

<style scoped>
.image-result .notice { display: block; line-height: 1.8; }
.image-result { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 32px; margin-top: 24px; align-items: start; }
.original-column, .answers-column { min-width: 0; }
.original-image { display: block; width: 100%; margin-top: 16px; border: 1px solid var(--line); border-radius: 10px; }
.verdict-tabs { display: flex; gap: 8px; margin: 16px 0 12px; }
.verdict-tab { flex: 1; text-align: center; border: 1px solid var(--line); background: #fff; border-radius: 10px; font-weight: 650; }
.verdict-tab.incorrect { color: #bc4942; }
.verdict-tab.correct { color: #237667; }
.verdict-tab.selected { background: var(--blue-soft); border-color: var(--primary); color: var(--primary); }
.verdict-tab.incorrect.selected { background: #fff6f4; border-color: #e3b2ab; color: #9a433c; }
.verdict-tab.correct.selected { background: #f1faf6; border-color: #b7d7cb; color: #1d6b58; }
.mood, .empty-verdict { display: flex; align-items: center; gap: 10px; }
.mood { margin: 4px 0 12px; padding: 12px 14px; border-radius: 12px; font-weight: 650; line-height: 1.6; }
.mood.incorrect { background: #fff4f1; color: #9a433c; }
.mood.correct, .empty-verdict.clear, .empty-verdict.correct { background: #eef8f3; color: #1d6b58; }
.empty-verdict { flex-wrap: wrap; margin-top: 8px; padding: 16px; border-radius: 12px; }
.empty-verdict .section-title { color: inherit; }
.mood-icon, .verdict-icon { width: 22px; height: 22px; flex-shrink: 0; }
.question-title { display: flex; align-items: center; gap: 8px; min-width: 0; }
.cheer { display: block; margin: 2px 0 10px; font-weight: 650; line-height: 1.6; }
.cheer.correct { color: #1d6b58; }
.cheer.incorrect { color: #9a433c; }
.answer-question { border-top: 1px solid var(--line); padding: 20px 0; margin-top: 16px; }
.answer-question.incorrect { border-top: 2px solid #db8c81; }
.answer-question.correct { border-top: 2px solid #9dcebc; }
.answer-text { display: block; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.85; margin-top: 6px; }
.answer-label { display: block; margin-top: 16px; }
.final-answer { background: var(--blue-soft); padding: 12px; border-radius: 8px; }
.solution-step { margin-top: 8px; }
@media (max-width: 800px) { .image-result { grid-template-columns: 1fr; gap: 24px; } }
</style>
