<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue';
import type { FamilyApi } from '../api';
import type { HomeworkDay } from '../daily-homework-types';
import { dailySummary } from '../daily-homework';
import { beijingDate } from '../homework-history';
import FamilyButton from './FamilyButton.vue';
const props = defineProps<{ api: FamilyApi; student: string }>();
const emit = defineEmits<{ open: [] }>();
const day = ref<HomeworkDay | null>(null), backlog = ref(0), failed = ref(false);
const student = props.student, generation = props.api.generation; let active = true;
onMounted(async () => {
  try { const [value, previous] = await Promise.all([props.api.homeworkDay(student, beijingDate()), props.api.homeworkBacklog(student)]); if (active && generation === props.api.generation && student === props.student) { day.value = value; backlog.value = previous.items.length; } }
  catch { if (active) failed.value = true; }
});
onUnmounted(() => { active = false; });
</script>
<template><view class="panel daily-home-summary"><view><text class="section-title">今天的作业清单</text><text class="muted">{{ failed ? '暂时未能读取清单，可打开后刷新。' : dailySummary(day) }}</text><text v-if="backlog" class="small muted">历史还有 {{ backlog }} 项未标完成，最多统计100项</text></view><FamilyButton class="secondary" @click="emit('open')">查看每日作业</FamilyButton></view></template>
<style scoped>.daily-home-summary{display:flex;align-items:center;justify-content:space-between;gap:16px}.daily-home-summary>view{display:flex;flex-direction:column;gap:8px}@media(max-width:480px){.daily-home-summary{align-items:flex-start;flex-direction:column}}</style>
