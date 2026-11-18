<script setup lang="ts">
import { metricNames, type EvaluationSummary } from './evaluationRuns';
defineProps<{summary: EvaluationSummary}>();
const names = {coverage:'自动批改覆盖率',selective_accuracy:'自动批改正确率',false_correct_rate:'错题误放行率',false_incorrect_rate:'正确题误判率',incorrect_precision:'错题判定精确率',incorrect_recall:'错题检出率'};
const percent = (v: number | null) => v === null ? '无分母' : `${(v*100).toFixed(2)}%`;
const state = (v: string) => ({computed:'已计算',undefined_denominator:'无分母',insufficient_clusters:'簇数量不足',undefined_resamples:'重采样存在空分母'}[v] ?? v);
</script>
<template>
  <div class="table-scroll"><table class="run-metrics"><caption>指标与单侧 95% 置信界限</caption><thead><tr><th>指标</th><th>分子 / 分母</th><th>估计值</th><th>独立样本下 / 上界</th><th>保守下 / 上界</th><th>学生簇 / 页面簇</th></tr></thead><tbody>
    <tr v-for="key in metricNames" :key="key"><th>{{ names[key] }}</th><td>{{ summary.metrics[key].numerator }} / {{ summary.metrics[key].denominator }}</td><td>{{ percent(summary.metrics[key].estimate) }}</td><td>{{ percent(summary.metrics[key].one_sided_95.lower) }} / {{ percent(summary.metrics[key].one_sided_95.upper) }}</td><td>{{ percent(summary.metrics[key].conservative_one_sided_95.lower) }} / {{ percent(summary.metrics[key].conservative_one_sided_95.upper) }}</td><td><div v-for="kind in (['student','page'] as const)" :key="kind">{{ kind==='student' ? '学生' : '页面' }} {{ summary.metrics[key].clusters[kind].cluster_count }} · {{ state(summary.metrics[key].clusters[kind].status) }}<small>空分母重采样 {{ summary.metrics[key].clusters[kind].undefined_resamples }} 次；{{ percent(summary.metrics[key].clusters[kind].lower) }} / {{ percent(summary.metrics[key].clusters[kind].upper) }}</small></div></td></tr>
  </tbody></table></div>
</template>
