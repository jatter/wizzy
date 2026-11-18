<script setup lang="ts">
import type { LessonFields } from '../api';
import FamilyInput from './FamilyInput.vue';
const props = defineProps<{ modelValue: LessonFields; prefix: string; disabled: boolean }>();
const emit = defineEmits<{ 'update:modelValue': [value: LessonFields] }>();
function update(field: keyof LessonFields, value: string) {
  emit('update:modelValue', { ...props.modelValue, [field]: field === 'period' ? Number(value) : field === 'subject' ? value : value || null });
}
</script>
<template>
  <view class="lesson-fields">
    <view><text>科目</text><FamilyInput :aria-label="prefix + '科目'" :model-value="modelValue.subject" :disabled="disabled" maxlength="80" @update:modelValue="update('subject', $event)" /></view>
    <view><text>节次（1–20）</text><FamilyInput :aria-label="prefix + '节次'" :model-value="String(modelValue.period)" :disabled="disabled" type="number" @update:modelValue="update('period', $event)" /></view>
    <view><text>上课时间（可选）</text><FamilyInput :aria-label="prefix + '上课时间'" :model-value="modelValue.start_time || ''" :disabled="disabled" placeholder="08:00" maxlength="5" @update:modelValue="update('start_time', $event)" /></view>
    <view><text>下课时间（可选）</text><FamilyInput :aria-label="prefix + '下课时间'" :model-value="modelValue.end_time || ''" :disabled="disabled" placeholder="08:40" maxlength="5" @update:modelValue="update('end_time', $event)" /></view>
    <view><text>教室（可选）</text><FamilyInput :aria-label="prefix + '教室'" :model-value="modelValue.room || ''" :disabled="disabled" maxlength="100" @update:modelValue="update('room', $event)" /></view>
  </view>
</template>
<style scoped>
.lesson-fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}.lesson-fields text{display:block;font-size:12px;color:#647887;margin-bottom:6px}.lesson-fields input{box-sizing:border-box;width:100%;min-height:42px;padding:10px;border:1px solid #dae5e9;border-radius:10px;background:#fff;color:#20374b;font:inherit}
</style>
