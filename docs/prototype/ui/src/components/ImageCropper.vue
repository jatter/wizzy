<script setup>
import {computed,nextTick,onBeforeUnmount,ref,watch} from 'vue';
const props=defineProps({photo:Object});const emit=defineEmits(['close','apply']);
const canvas=ref(),ready=ref(false),error=ref(''),saving=ref(false),x=ref(5),y=ref(5),width=ref(90),height=ref(90);let generation=0;
onBeforeUnmount(()=>generation++);
const valid=computed(()=>[x.value,y.value,width.value,height.value].every(Number.isFinite)&&x.value>=0&&y.value>=0&&width.value>0&&height.value>0&&x.value+width.value<=100&&y.value+height.value<=100);
watch(()=>props.photo,async(photo)=>{
 const current=++generation;ready.value=false;error.value='';x.value=5;y.value=5;width.value=90;height.value=90;
 if(!photo)return;await nextTick();const image=new Image();
 image.onload=()=>{if(current!==generation||!canvas.value)return;const turn=((photo.rotation%360)+360)%360;const c=canvas.value;c.width=turn%180?image.naturalHeight:image.naturalWidth;c.height=turn%180?image.naturalWidth:image.naturalHeight;const ctx=c.getContext('2d');ctx.translate(c.width/2,c.height/2);ctx.rotate(turn*Math.PI/180);ctx.drawImage(image,-image.naturalWidth/2,-image.naturalHeight/2);ready.value=true;};
 image.onerror=()=>{if(current===generation)error.value='无法读取这张图片，请重新选择';};image.src=photo.url;
},{immediate:true});
async function save(){
 if(!valid.value||!ready.value||saving.value)return;saving.value=true;error.value='';const current=generation,photo=props.photo;const source=canvas.value;const result=document.createElement('canvas');
 result.width=Math.max(1,Math.round(source.width*width.value/100));result.height=Math.max(1,Math.round(source.height*height.value/100));
 result.getContext('2d').drawImage(source,source.width*x.value/100,source.height*y.value/100,source.width*width.value/100,source.height*height.value/100,0,0,result.width,result.height);
 const blob=await new Promise(resolve=>result.toBlob(resolve,'image/png'));saving.value=false;
 if(current!==generation)return;if(!blob){error.value='裁剪失败，请重试';return;}if(blob.size>10*1024*1024){error.value='裁剪后的图片超过 10 MiB，请缩小裁剪区域';return;}emit('apply',{photo,blob,width:result.width,height:result.height});
}
</script>
<template><el-dialog :model-value="!!photo" title="裁剪作业照片" width="700px" @close="emit('close')"><p>保留题目与作答区域。旋转会应用到裁剪后的图片，保存后可继续编辑。</p><div class="crop-preview"><canvas ref="canvas"/><div v-if="ready" class="crop-selection" :style="{left:`${x}%`,top:`${y}%`,width:`${width}%`,height:`${height}%`}"/></div><div class="crop-fields"><label v-for="item in [{key:'x',name:'左侧',value:x},{key:'y',name:'顶部',value:y},{key:'width',name:'宽度',value:width},{key:'height',name:'高度',value:height}]" :key="item.key">{{item.name}} (%)<input v-if="item.key==='x'" v-model.number="x" type="number" min="0" max="99"/><input v-else-if="item.key==='y'" v-model.number="y" type="number" min="0" max="99"/><input v-else-if="item.key==='width'" v-model.number="width" type="number" min="1" max="100"/><input v-else v-model.number="height" type="number" min="1" max="100"/></label></div><p v-if="!valid" role="alert" class="error-message">裁剪区域须在图片范围内，宽高须大于 0。</p><p v-if="error" role="alert" class="error-message">{{error}}</p><template #footer><el-button @click="emit('close')">取消裁剪</el-button><el-button type="primary" :loading="saving" :disabled="!ready||!valid" @click="save">保存裁剪</el-button></template></el-dialog></template>
