<script setup>
import {computed,onBeforeUnmount,ref} from 'vue';
const enabled=ref(true),elapsed=ref(0),acknowledged=ref(false),started=ref(Date.now());
const due=computed(()=>enabled.value&&!acknowledged.value&&elapsed.value>=1200);
const remaining=computed(()=>{const s=Math.max(0,1200-elapsed.value);return `${String(Math.floor(s/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`;});
const timer=setInterval(()=>{elapsed.value=Math.floor((Date.now()-started.value)/1000);},1000);
onBeforeUnmount(()=>clearInterval(timer));
</script>
<template>
<section class="rest-strip" aria-label="学习休息提醒"><Icon name="heart" :size="23"/><div><strong>给眼睛一点休息时间</strong><p>{{!enabled?'提醒已关闭，可以继续使用。':acknowledged?'本次提醒已完成，按自己的节奏学习。':`本次学习满 20 分钟提醒 · 还剩 ${remaining}`}}</p></div><label><input v-model="enabled" type="checkbox"/>开启休息提醒</label><button v-if="enabled&&!acknowledged" class="text-action" @click="started=Date.now()-1200000;elapsed=1200">演示到时提醒</button></section>
<section v-if="due" class="panel rest-message" role="status"><Icon name="heart" :size="30"/><div><h2>认真学了一会儿，休息一下吧</h2><p>看看远处、喝口水。提醒不会阻止你继续使用。</p></div><van-button type="primary" plain @click="acknowledged=true">知道了，继续学习</van-button><van-button plain @click="enabled=false">关闭本次提醒</van-button></section>
</template>
