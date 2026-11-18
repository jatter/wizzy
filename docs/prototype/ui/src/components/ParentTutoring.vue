<script setup>
import {ref} from 'vue';
const level=ref(0),explanation=ref(false);
const hints=['可以请孩子用手指沿图形边框走一圈，说一说经过了几条边。','请孩子标出两条长边和两条宽边，比较哪些边一样长。','先把四条边分别相加，再试着用两组相同的长和宽表达。'];
</script>
<template>
<div class="exercise-layout"><section class="panel exercise"><el-tag>演示作业 HW-1025 · 第 2 题</el-tag><h2>长方形长 8 厘米、宽 5 厘米，周长是多少？</h2><p>孩子的机器原始作答：13 厘米；示例错因：只加了一条长边和一条宽边。</p><p class="muted">来源：固定演示题与讲解稿 v1。没有引用教师评价，也未请求 AI。</p><div class="hint-box"><Icon name="heart"/><p>先问问孩子“周长指哪里”，给他画图和解释的时间。</p></div><ol class="tutoring-hints"><li v-for="(hint,i) in hints.slice(0,level)" :key="i"><strong>提示 {{i+1}}</strong><p>{{hint}}</p></li></ol><el-button type="primary" :disabled="level===3" @click="level++">{{level===3?'本题 3 条提示已全部展示':'展开下一步提示'}}</el-button><p class="muted">每题最多 3 条固定提示；刷新恢复演示。正式辅导须由服务端执行次数与费用上限。</p></section><aside class="panel learning-aside"><span class="eyebrow">给家长的讲解</span><h3>把思路交还给孩子</h3><p>等待孩子先尝试，再根据需要逐步展开。完整讲解只在家长区域显示。</p><el-button plain @click="explanation=!explanation">{{explanation?'收起家长讲解':'查看家长讲解'}}</el-button><div v-if="explanation" class="hint-box"><p>周长是四条边长度的总和：8 + 5 + 8 + 5 = 26 厘米，也可写为（8 + 5）× 2。请孩子解释为什么要乘 2。</p></div><div class="aside-line"/><p>不提供开放聊天或教师意见推断。超出这道题的讲解范围，请结合教材与老师要求核对。</p></aside></div>
</template>
