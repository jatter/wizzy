<script setup lang="ts">
import { onMounted, ref } from 'vue';
import type { FamilyApi, MistakeSheet } from '../api';
import FamilyButton from './FamilyButton.vue';

const props = defineProps<{ api: FamilyApi; student: string; guardian: boolean }>();
const sheets = ref<MistakeSheet[]>([]), message = ref('');

async function load() { sheets.value = (await props.api.mistakeSheets(props.student)).items; }
async function download(sheet: MistakeSheet, kind: 'questions' | 'answers') {
  message.value = '';
  const bytes = await props.api.mistakeSheetPdf(sheet.id, kind);
  // #ifdef H5
  const blob = new Blob([bytes], { type: 'application/pdf' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = (kind === 'answers' ? '答案卷-' : '题目卷-') + sheet.due_date + '.pdf';
  link.click();
  URL.revokeObjectURL(url);
  // #endif
  // #ifndef H5
  message.value = '请在浏览器里打开家庭端后再打印。';
  // #endif
}
onMounted(load);
defineExpose({ load });
</script>

<template>
  <view class="review-sheets">
    <view class="section-head"><view><text class="section-title">打印再做</text><text class="muted">过几天把前面的错题印出来，看是不是真的会了。</text></view><text class="badge">{{ sheets.length }} 份</text></view>
    <view v-if="!sheets.length" class="panel empty-state"><text class="section-title">还没有复习卷</text><text class="muted">在错题回看里勾选题目，就可以生成一份再做的卷子。</text></view>
    <view v-for="sheet in sheets" :key="sheet.id" class="panel sheet-card">
      <view class="section-head"><text class="section-title">{{ sheet.due_date }} · {{ sheet.items.length }} 题</text><text class="badge">{{ sheet.state }}</text></view>
      <text v-for="item in sheet.items" :key="item.id" class="attempt-row">{{ item.returned ? '已交回' : '还没再做' }} · {{ item.stem }}</text>
      <view v-if="guardian" class="button-row"><FamilyButton class="secondary compact" @click="download(sheet, 'questions')">打印题目</FamilyButton><FamilyButton class="secondary compact" @click="download(sheet, 'answers')">打印答案</FamilyButton></view>
    </view>
    <text v-if="message" class="notice">{{ message }}</text>
  </view>
</template>

<style scoped>
.review-sheets { margin-top: 28px; }
.sheet-card { margin-top: 12px; }
</style>
