<script setup lang="ts">
import { ref, watch } from 'vue';
import type { HomeworkFields } from '../daily-homework-types';
import { subjects, kinds } from '../daily-homework';
import FamilyButton from './FamilyButton.vue';
import FamilyInput from './FamilyInput.vue';
const props = withDefaults(defineProps<{ modelValue: HomeworkFields; prefix?: string; disabled?: boolean }>(), { prefix: '' });
const emit = defineEmits<{ 'update:modelValue': [value: HomeworkFields] }>();
function change(key: keyof HomeworkFields, value: unknown) { emit('update:modelValue', { ...props.modelValue, [key]: value }); }
function locate(key: string, value: string) { change('locator', { ...props.modelValue.locator, [key]: value || null }); }
function range(value: string | string[] | null | undefined) { return Array.isArray(value) ? value.join('、') : value ?? ''; }
const dueDate = ref(''), dueTime = ref('');
let emittedDue = props.modelValue.due_at;
function readDue(value: string | null) {
  if (!value || !Number.isFinite(Date.parse(value))) { dueDate.value = ''; dueTime.value = ''; return; }
  const local = new Date(Date.parse(value) + 8 * 3600000).toISOString();
  dueDate.value = local.slice(0,10); dueTime.value = local.slice(11,16);
}
readDue(props.modelValue.due_at);
watch(() => props.modelValue.due_at, value => { if (value !== emittedDue) { readDue(value); emittedDue = value; } });
function due(date: string, time: string) {
  dueDate.value = date; dueTime.value = date ? time : '';
  emittedDue = date ? time ? date + 'T' + time + ':00+08:00' : date + 'T' : null;
  change('due_at', emittedDue);
}
</script>
<template>
  <view class="homework-fields">
    <text class="field-label">科目</text><view class="button-row"><FamilyButton v-for="(label, key) in subjects" :key="key" class="secondary compact" :class="{ selected: modelValue.subject === key }" :aria-pressed="modelValue.subject === key" :aria-label="prefix + '科目' + label" :disabled="disabled" @click="change('subject', key)">{{ label }}</FamilyButton></view>
    <text class="field-label">类型</text><view class="button-row"><FamilyButton v-for="(label, key) in kinds" :key="key" class="secondary compact" :class="{ selected: modelValue.item_type === key }" :aria-pressed="modelValue.item_type === key" :aria-label="prefix + '类型' + label" :disabled="disabled" @click="change('item_type', key)">{{ label }}</FamilyButton></view>
    <text class="field-label">作业内容</text><FamilyInput :model-value="modelValue.title" :aria-label="prefix + '作业内容'" :disabled="disabled" maxlength="1000" @update:model-value="change('title', $event)" />
    <text class="field-label">原文</text><FamilyInput :model-value="modelValue.raw_text" :aria-label="prefix + '作业原文'" :disabled="disabled" multiline maxlength="4000" @update:model-value="change('raw_text', $event)" />
    <view class="locator-grid">
      <view><text class="field-label">书本简称</text><FamilyInput :model-value="modelValue.locator.book_alias ?? ''" :aria-label="prefix + '书本简称'" :disabled="disabled" maxlength="150" @update:model-value="locate('book_alias', $event)" /></view>
      <view><text class="field-label">课次</text><FamilyInput :model-value="modelValue.locator.lesson ?? ''" :aria-label="prefix + '课次'" :disabled="disabled" maxlength="150" @update:model-value="locate('lesson', $event)" /></view>
      <view><text class="field-label">页码范围（保留原文）</text><FamilyInput :model-value="range(modelValue.locator.pages)" :aria-label="prefix + '页码范围'" :disabled="disabled" maxlength="200" @update:model-value="locate('pages', $event)" /></view>
      <view><text class="field-label">单元范围</text><FamilyInput :model-value="range(modelValue.locator.units)" :aria-label="prefix + '单元范围'" :disabled="disabled" maxlength="200" @update:model-value="locate('units', $event)" /></view>
      <view><text class="field-label">题号范围</text><FamilyInput :model-value="modelValue.locator.question_range ?? ''" :aria-label="prefix + '题号范围'" :disabled="disabled" maxlength="200" @update:model-value="locate('question_range', $event)" /></view>
      <view><text class="field-label">次数原文</text><FamilyInput :model-value="modelValue.locator.repetition_raw ?? ''" :aria-label="prefix + '次数原文'" :disabled="disabled" maxlength="100" @update:model-value="locate('repetition_raw', $event)" /></view>
    </view>
    <view class="locator-grid"><view><text class="field-label">截止日期（可选）</text><FamilyInput :model-value="dueDate" type="date" :aria-label="prefix + '截止日期'" :disabled="disabled" @update:model-value="due($event, dueTime)" /></view><view><text class="field-label">截止时刻</text><FamilyInput :model-value="dueTime" type="time" :aria-label="prefix + '截止时刻'" :disabled="disabled || !dueDate" @update:model-value="due(dueDate, $event)" /></view></view><text class="small muted">截止日期和时刻按上海时间记录，不填写则不标记逾期。</text>
    <text class="small muted">“2页”不等于第2页；1+1、U1等按原要求保留。准备事项单独统计。</text>
  </view>
</template>
<style scoped>
.homework-fields { display:flex; flex-direction:column; gap:10px; min-width:0; }.locator-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }.locator-grid>view { min-width:0; }.selected { border-color:#087e80; background:#e6f4f0; }.field-label { margin-top:8px; }@media(max-width:480px){.locator-grid{grid-template-columns:minmax(0,1fr)}}
</style>
