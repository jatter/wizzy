<script setup>
import {ref} from 'vue';
import {showToast} from 'vant';
const state=ref('available'),answer=ref(''),done=ref(false);
function change(){answer.value='';done.value=false;}
</script>
<template>
<PageHead eyebrow="LOOK AHEAD / 明日预习" title="带着一个问题，迎接明天" description="数学 · 三年级上册 · 周长的应用 · 约 2 分钟"/>
<div class="preview-layout"><section class="panel exercise">
 <template v-if="state==='available'"><el-tag type="success">演示教材 · 第 1 页</el-tag><h2>为什么长方形的周长要乘 2？</h2><div class="lesson-facts"><div><span>学习目标</span><strong>用自己的话说明周长的含义</strong></div><div><span>关键词</span><strong>边框 · 四条边 · 两组相等的边</strong></div></div><p>观察书桌、书本或窗户：它们都有两条一样长的长边，也有两条一样长的宽边。</p><div class="hint-box"><Icon name="book"/><p>自检：你身边哪个物品的边框像长方形？想一想为什么要把四条边都算上。</p></div><van-field v-model="answer" label="我的发现" type="textarea" placeholder="写下一种物品，或一个想在课堂上问的问题" maxlength="300"/><van-button type="primary" @click="answer.trim()?done=true:showToast('先写下一点发现吧')">记录我的发现</van-button><div v-if="done" class="result-banner" role="status"><Icon name="tick"/><p>发现已记录。带着你的观察去上课吧！</p></div></template>
 <div v-else class="empty-state" role="status"><Icon :name="state==='expired'?'lock':'book'" :size="42"/><h2>{{state==='expired'?'这份教材的授权已到期':'还没有明天的章节'}}</h2><p>{{state==='expired'?'请家长更新教材授权后再生成新预习内容。历史学习记录仍然保留。':'请家长在课表与教材中确认教学进度。没有章节时，不生成示例页码或学习结论。'}}</p><van-button plain @click="state='available';change()">恢复演示教材</van-button></div>
</section><aside class="panel learning-aside"><span class="eyebrow">预习的依据</span><h3>每张预习卡都要有出处。</h3><p v-if="state==='available'">示例来源：演示教材《数学 · 三年级上册》、章节“周长的应用”、第 1 页。生产环境仅使用授权版本或家长录入课程描述。</p><p v-else>{{state==='missing'?'尚无可用章节与页码，等待家长设置教学进度。':'当前教材授权不可用，暂停展示教材内容和来源页。'}}</p><div class="aside-line"/><label class="state-select">演示教材状态<select v-model="state" @change="change"><option value="available">章节与授权可用</option><option value="missing">尚未设置章节</option><option value="expired">教材授权已到期</option></select></label><p class="muted">状态切换会清空本次预习输入；本原型未请求教材服务。</p></aside></div>
</template>
