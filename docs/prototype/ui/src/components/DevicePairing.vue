<script setup>
import {computed,onBeforeUnmount,ref} from 'vue';
const props=defineProps({bound:Boolean});
const emit=defineEmits(['bind','revoke']);
const code=ref(''),deadline=ref(Date.now()+300000),now=ref(Date.now()),attempts=ref(0),state=ref(props.bound?'used':'ready'),message=ref(''),confirmRevoke=ref(false);
const remaining=computed(()=>Math.max(0,Math.ceil((deadline.value-now.value)/1000)));
const clock=computed(()=>`${Math.floor(remaining.value/60)}:${String(remaining.value%60).padStart(2,'0')}`);
const locked=computed(()=>attempts.value>=3);
const timer=setInterval(()=>now.value=Date.now(),1000);
onBeforeUnmount(()=>clearInterval(timer));
function bind(){
 message.value='';
 if(state.value==='used'){message.value='配对码已使用，请让家长生成新码';return;}
 if(!remaining.value){message.value='配对码已过期，请让家长生成新码';return;}
 if(locked.value){message.value='尝试次数已达上限，请让家长生成新码';return;}
 attempts.value++;
 if(code.value!=='246810'){message.value=locked.value?'尝试次数已达上限，请让家长生成新码':`配对码不匹配，还可尝试 ${3-attempts.value} 次`;return;}
 state.value='used';code.value='';emit('bind');
}
function fresh(){deadline.value=Date.now()+300000;now.value=Date.now();attempts.value=0;state.value='ready';code.value='';message.value='已模拟生成新码：246810';}
function revoke(){confirmRevoke.value=false;message.value='设备授权已撤销，本次学习内容已清空';emit('revoke');}
</script>
<template>
<PageHead eyebrow="SAFE START / 设备与授权" title="让家长陪你连接小知" description="一个设备只进入一个孩子的学习空间。以下配对过程使用本地演示数据。"/>
<div class="pair-layout">
 <section class="panel pairing-card">
  <template v-if="bound">
   <span class="pair-symbol"><Icon name="shield" :size="36"/></span><el-tag type="success">已绑定 · 演示</el-tag><h2>这是林同学的学习空间</h2><p>三年级 · 家长已授权 · 当前浏览器</p>
   <dl class="pair-details"><div><dt>设备名称</dt><dd>家庭平板 · 演示设备</dd></div><div><dt>可见内容</dt><dd>林同学的作业、订正、复习与预习</dd></div><div><dt>授权边界</dt><dd>不展示其他孩子或家庭管理信息</dd></div></dl>
   <van-button type="primary" @click="emit('bind')">进入我的学习空间</van-button><van-button plain @click="confirmRevoke=true">演示家长撤销设备授权</van-button>
   <div v-if="confirmRevoke" class="hint-box revoke-confirm"><div><h3>确认撤销这个演示设备？</h3><p>照片、作答草稿和检查中的任务会清空。重新进入需要新配对码。</p><div class="button-row"><van-button type="danger" @click="revoke">确认撤销授权</van-button><van-button plain @click="confirmRevoke=false">保留授权</van-button></div></div></div>
  </template>
  <template v-else>
   <span class="pair-symbol"><Icon name="link" :size="36"/></span><h2>输入家长给你的配对码</h2><p>请在家长端生成一次性配对码，再在这里输入。</p>
   <label class="pair-code-label" for="pair-code">6 位配对码</label><input id="pair-code" v-model="code" class="pair-code" inputmode="numeric" autocomplete="off" maxlength="6" placeholder="输入 6 位数字" @keyup.enter="bind"/>
   <div class="pair-code-meta"><span>有效期 <strong>{{clock}}</strong></span><span>已尝试 {{attempts}} / 3 次</span></div>
   <p v-if="!remaining" role="status" class="error-message">配对码已过期</p><p v-if="state==='used'" role="status" class="error-message">配对码已使用</p><p v-if="locked" role="status" class="error-message">尝试次数已达上限</p>
   <van-button type="primary" :disabled="locked||!remaining||state==='used'" @click="bind">连接我的学习空间</van-button>
  </template>
  <p v-if="message" role="status" class="pair-feedback">{{message}}</p>
 </section>
 <aside class="panel learning-aside"><span class="eyebrow">从家长授权开始</span><h3>连接后，只看见自己的学习内容。</h3><p>配对码默认 5 分钟有效，只能使用一次。正式产品由服务端验证、限制尝试并撤销旧会话。</p><div class="aside-line"/><details class="demo-controls"><summary>原型状态演示</summary><p>当前默认已绑定，便于直接浏览。撤销后可体验首次配对；固定示例码为 246810。</p><div class="button-row"><el-button @click="fresh">模拟家长生成新配对码</el-button><el-button @click="deadline=0;now=Date.now()">模拟码过期</el-button><el-button @click="state='used'">模拟码已使用</el-button></div></details></aside>
</div>
</template>
