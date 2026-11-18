<script setup>
import {computed,ref} from 'vue';
defineProps({parent:Boolean});
const quality=ref('qualified'),reason=ref(''),invalid=ref(false),error=ref('');
const included=computed(()=>quality.value==='qualified'&&!invalid.value);
const dimensions=[{name:'字形结构',score:82,note:'“明”的左右两部分保持相近高度。'},{name:'字间距离',score:78,note:'两个字之间留出约半个字的空隙。'},{name:'行列整齐',score:86,note:'让每个字的底部靠近同一条基线。'}];
function invalidate(){if(!reason.value.trim()){error.value='请填写无效原因，便于追溯。';return;}error.value='';invalid.value=true;}
</script>
<template>
<div class="panel-heading"><div v-if="!parent"><span class="eyebrow">WRITING / 工整度反馈</span><h1>写得清楚，也是一种表达</h1><p>固定示例 · 语文方格纸 · 不进行学生排名。</p></div><label class="state-select">示例图片质量<select v-model="quality"><option value="qualified">清晰、支持的纸张</option><option value="unclear">模糊或光线不足</option><option value="unsupported">暂不支持的纸张</option></select></label></div>
<div class="dashboard-columns"><section class="panel exercise"><el-tag type="info">演示样本 WR-1025 · 非真实识别</el-tag><div class="writing-sample" aria-label="方格书写示意"><span v-for="char in '明天会更好'" :key="char">{{char}}</span></div><p class="muted">此处用排版展示书写建议，字形不是儿童笔迹或实际模型证据。</p>
 <div v-if="quality!=='qualified'" class="empty-state" role="status"><Icon name="camera" :size="40"/><h2>{{quality==='unclear'?'照片看不清，先不评分':'这种纸张暂不支持评分'}}</h2><p>请正对纸张、保持光线均匀并拍全字迹。当前样本不进入趋势，不显示分项分数。</p></div>
 <template v-else><div class="panel-heading"><h2>分项观察 · 示例</h2><el-tag :type="invalid?'warning':'success'">{{invalid?'已标记无效':'质量合格'}}</el-tag></div><div v-for="item in dimensions" :key="item.name" class="writing-dimension"><div><strong>{{item.name}}</strong><span>{{item.score}} / 100</span></div><meter min="0" max="100" :value="item.score" :aria-label="item.name"/><p>{{item.note}}</p></div></template>
</section><aside class="panel learning-aside"><span class="eyebrow">{{parent?'观察变化':'今天的一小步'}}</span><h3>{{parent?'比较同类纸张和任务':'先练习把字写清楚'}}</h3><p>先试着把“明”字左右写匀，再看看字与字的距离。维度只描述书写，不评价性格或学习能力。</p>
 <template v-if="parent"><div class="writing-trend" role="status"><strong>同类样本趋势 · 演示</strong><p>10/18：80 分（合格）</p><p>10/25：{{included?'84 分（合格）':'已排除，不参与统计'}}</p><p>10/26：模糊，已拒评</p><strong>{{included?'2 份有效样本 · 均值 82':'1 份有效样本 · 均值 80'}}</strong><p class="muted">固定综合示意值，非上方维度的加权计算；样本不足以判断长期趋势。</p></div>
 <template v-if="!invalid"><label class="state-select">无效原因<textarea v-model="reason" rows="3" maxlength="300" placeholder="例如：纸张类型识别错误"/></label><p v-if="error" role="alert" class="error-message">{{error}}</p><el-button type="warning" plain @click="invalidate">标记本次评分无效</el-button></template><p v-else role="status">无效依据：{{reason}}。保留原示例记录，已从趋势排除。</p></template>
 <p class="muted">真实图片质量检测、评分、版本与趋势服务仍需接入；不会将此示例用于模型准确率。</p>
</aside></div>
</template>
