<script setup lang="ts">
import { onMounted, ref } from 'vue';
import type { FamilyApi, MistakeBankDetail, MistakeBankItem } from '../api';
import FamilyButton from './FamilyButton.vue';
import FamilyInput from './FamilyInput.vue';

const props = defineProps<{ api: FamilyApi; student: string; guardian: boolean }>();
const emit = defineEmits<{ count: [value: number] }>();
const items = ref<MistakeBankItem[]>([]), detail = ref<MistakeBankDetail | null>(null);
const selected = ref<string[]>([]), dueDays = ref(3), dueDate = ref(''), message = ref(''), loading = ref(false);

async function load() {
  loading.value = true;
  try {
    const page = await props.api.mistakeBank(props.student);
    items.value = page.items;
    emit('count', page.items.length);
    if (detail.value && !page.items.some(item => item.id === detail.value!.id)) detail.value = null;
  } finally { loading.value = false; }
}
async function open(item: MistakeBankItem) {
  detail.value = detail.value?.id === item.id ? null : await props.api.mistakeDetail(props.student, item.id);
}
function toggle(id: string) {
  selected.value = selected.value.includes(id) ? selected.value.filter(item => item !== id) : [...selected.value, id];
}
async function makeSheet() {
  message.value = '';
  const body = dueDate.value ? { item_ids: selected.value, due_date: dueDate.value } : { item_ids: selected.value, due_in_days: dueDays.value };
  const sheet = await props.api.createMistakeSheet(props.student, body);
  selected.value = [];
  dueDate.value = '';
  message.value = `已放进复习清单，${sheet.due_date} 再做。`;
}
async function dismiss(item: MistakeBankItem) {
  await props.api.dismissMistake(props.student, item.id);
  if (detail.value?.id === item.id) detail.value = null;
  selected.value = selected.value.filter(id => id !== item.id);
  await load();
}
onMounted(load);
</script>

<template>
  <view id="mistake-list" class="mistake-bank">
    <view class="section-head"><view><text class="section-title">错题回看</text><text class="muted">同一道题的每次提交都留在这里，方便对照。</text></view><text class="badge">{{ items.length }} 道</text></view>
    <text v-if="loading" class="muted">正在整理错题…</text>
    <view v-else-if="!items.length" class="panel empty-state"><text class="section-title">还没有错题</text><text class="muted">批改完成的错题会出现在这里。</text></view>
    <view v-for="item in items" :key="item.id" class="panel mistake-card">
      <view class="section-head">
        <view class="question-title"><text v-if="guardian" class="pick" @click="toggle(item.id)">{{ selected.includes(item.id) ? '☑' : '☐' }}</text><text class="question-stem">{{ item.stem }}</text></view>
        <text class="badge">{{ item.subject_label }}</text>
      </view>
      <text class="counts"><text class="incorrect-count">错 {{ item.incorrect_count }} 次</text><text class="correct-count">对 {{ item.correct_count }} 次</text><text class="muted">最近 {{ item.latest_day }}</text></text>
      <text v-if="item.guardian_note" class="notice">{{ item.guardian_note }}</text>
      <view class="button-row"><FamilyButton class="secondary compact" @click="open(item)">{{ detail?.id === item.id ? '收起明细' : '查看提交明细' }}</FamilyButton><FamilyButton v-if="guardian" class="text-button compact" @click="dismiss(item)">这题其实不用再练</FamilyButton></view>
      <view v-if="detail?.id === item.id" class="attempt-list">
        <text v-if="detail.summary" class="cheer">{{ detail.summary }}</text>
        <text v-for="(line, index) in detail.timeline" :key="index" class="attempt-row">{{ line }}</text>
        <view v-for="attempt in detail.attempts" :key="attempt.id" class="attempt-body"><text class="small muted">{{ attempt.question_number }} · {{ attempt.verdict_label }}</text><text>{{ attempt.reasoning || '这次没有记下解题思路。' }}</text></view>
      </view>
    </view>
    <view v-if="guardian && items.length" class="panel sheet-builder">
      <text class="section-title">过几天再做</text>
      <text class="muted">勾选上面的题，生成一份可以打印的复习卷。题目和答案分开。</text>
      <view class="button-row"><FamilyButton class="secondary compact" :class="{ chosen: dueDays === 3 && !dueDate }" @click="dueDays = 3; dueDate = ''">3 天后</FamilyButton><FamilyButton class="secondary compact" :class="{ chosen: dueDays === 7 && !dueDate }" @click="dueDays = 7; dueDate = ''">7 天后</FamilyButton></view>
      <text class="small muted">或指定日期</text>
      <FamilyInput v-model="dueDate" aria-label="复习日期" type="date" class="field" />
      <FamilyButton class="primary" :disabled="!selected.length" @click="makeSheet">生成复习卷（{{ selected.length }} 题）</FamilyButton>
      <text v-if="message" class="notice">{{ message }}</text>
    </view>
  </view>
</template>

<style scoped>
.mistake-bank { margin-top: 8px; }
.question-title { display: flex; align-items: flex-start; gap: 8px; min-width: 0; }
.pick { font-size: 18px; line-height: 1.4; }
.counts { display: flex; flex-wrap: wrap; gap: 8px 16px; margin: 8px 0; }
.incorrect-count { color: #9a433c; font-weight: 650; }
.correct-count { color: #1d6b58; font-weight: 650; }
.cheer { display: block; margin: 8px 0; font-weight: 650; color: #3659ce; }
.attempt-list { margin-top: 8px; }
.attempt-body { display: flex; flex-direction: column; gap: 4px; padding: 8px 0; border-top: 1px solid var(--line); }
.sheet-builder { margin-top: 16px; }
.field { margin: 8px 0 12px; }
</style>
