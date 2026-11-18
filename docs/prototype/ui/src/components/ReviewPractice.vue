<script setup>
import {ref} from 'vue';
const answer=ref(''),state=ref('open'),error=ref(''),hint=ref(false),events=ref([]);
function record(action){
 if(action==='提交作答'&&!answer.value.trim()){error.value='先写下自己的想法，也可以选择需要帮助或跳过。';return;}
 error.value='';events.value.push({action,answer:answer.value});state.value=action==='提交作答'?'submitted':action==='退出并保留草稿'?'paused':'open';
 if(action==='需要帮助')hint.value=true;
}
</script>
<template>
<PageHead eyebrow="RECALL / 到期复习" title="先自己回忆，再看提示" description="D+1 演示任务 · 1 道题 · 预计 2 分钟；本次会话可退出续做。"/>
<div class="exercise-layout"><section class="panel exercise">
 <el-tag :type="state==='submitted'?'warning':'info'">{{state==='submitted'?'已提交 · 待家长核对':state==='paused'?'草稿已暂存':'待复习'}}</el-tag>
 <h2>长方形花坛长 6 米、宽 4 米，围一圈需要多少米围栏？</h2><p class="muted">来源：演示作业 HW-1025 · 第 2 题 · 周长。固定变式题，不是真实个性化生成。</p>
 <template v-if="state==='paused'"><div class="empty-state"><Icon name="book" :size="40"/><h3>休息一下，下次接着来</h3><p>本次页面会话中保留输入，刷新后清空。</p><van-button type="primary" @click="state='open'">继续上次复习</van-button></div></template>
 <template v-else><van-field v-model="answer" label="我的复习作答" type="textarea" rows="3" maxlength="300" placeholder="先写下你的答案或思路" :disabled="state==='submitted'"/>
 <p v-if="error" role="alert" class="error-message">{{error}}</p>
 <div v-if="state==='open'" class="button-row"><van-button type="primary" @click="record('提交作答')">提交复习作答</van-button><van-button plain @click="record('需要帮助')">不会，需要帮助</van-button><van-button plain @click="record('跳过本题')">暂时跳过本题</van-button><van-button plain @click="record('退出并保留草稿')">退出并保留草稿</van-button></div>
 <div v-if="hint" class="hint-box"><Icon name="sparkles"/><p>先画一个长方形，把四条边的长度都标出来，再想一想“围一圈”。</p></div>
 <div v-if="state==='submitted'" class="result-banner" role="status"><Icon name="clipboard"/><div><h3>你的想法已记录，等待家长核对</h3><p>本原型不自动判分；提交不等于复习完成或已掌握，也不改变原始正确率。</p></div></div></template>
</section><aside class="panel learning-aside"><span class="eyebrow">每一次尝试都留痕</span><h3>需要帮助也可以说出来。</h3><p>跳过和求助会保留任务。作答前不提供完整答案；这里仅提供一条有限提示。</p><div class="attempt-history"><article v-for="(item,i) in events" :key="i"><strong>{{i+1}} · {{item.action}}</strong><p>{{item.answer||'尚未填写作答'}}</p></article><p v-if="!events.length">还没有尝试记录。</p></div></aside></div>
</template>
