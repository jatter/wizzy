<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue';
import { ElButton } from 'element-plus';
import { FileImage, ZoomIn, ScanLine, RefreshCw } from 'lucide-vue-next';
import { ApiError, type PlatformApi } from './api';

const props = defineProps<{api: PlatformApi; edition: string; document: string; job: string; page: number}>();
const emit = defineEmits<{failure: [error: unknown]}>();
const image = ref(''), loading = ref(false), loaded = ref(false), enlarged = ref(false), issue = ref('');
let controller: AbortController | null = null, generation = 0;
function clearImage() {
  if (image.value) URL.revokeObjectURL(image.value);
  image.value = ''; loaded.value = false; enlarged.value = false;
}
function cancel() { generation++; controller?.abort(); controller = null; clearImage(); }
async function load() {
  cancel(); const version = generation;
  const request = new AbortController(); controller = request;
  loading.value = true; issue.value = '';
  try {
    const blob = await props.api.textbookProcessingPageImage(props.edition,props.document,props.job,props.page,request.signal);
    if (version !== generation) return;
    image.value = URL.createObjectURL(blob);
  } catch (error) {
    if (version !== generation) return;
    loading.value = false;
    issue.value = error instanceof ApiError && error.status === 502 ? '原件图片无法显示，请重新加载；持续失败时请联系管理员。' : '原件暂时无法加载，请重试。';
    if (error instanceof ApiError && ([401,403].includes(error.status) ||
      ['draft_rights_unavailable','preview_source_mismatch'].includes(error.code ?? ''))) emit('failure',error);
  }
}
function displayed() { loaded.value = true; loading.value = false; }
function imageFailed() { clearImage(); loading.value = false; issue.value = '原件图片无法显示，请重新加载；持续失败时请联系管理员。'; }
watch(() => [props.edition,props.document,props.job,props.page],load,{immediate:true,flush:'sync'});
onUnmounted(cancel);
</script>

<template>
  <section class="pdf-preview" aria-label="原 PDF 单页预览" :aria-busy="loading">
    <div class="pdf-heading"><strong><FileImage :size="16" />原件 · 第 {{ page }} 页</strong>
      <ElButton size="small" :disabled="!loaded" @click="enlarged = !enlarged"><ScanLine v-if="enlarged" :size="14" /><ZoomIn v-else :size="14" />{{ enlarged ? '适合宽度' : '放大原件' }}</ElButton>
    </div>
    <div class="pdf-stage" :class="{'is-enlarged':enlarged}" tabindex="0" aria-label="原件图片滚动区域">
      <p v-if="loading" class="pdf-message" role="status">正在加载当前页原件…</p>
      <div v-if="issue" class="pdf-message" role="alert"><p>{{ issue }}</p><ElButton size="small" @click="load"><RefreshCw :size="14" />重新加载原件</ElButton></div>
      <img v-if="image" :key="image" :src="image" :alt="`原 PDF 第 ${page} 页`" draggable="false" @load="displayed" @error="imageFailed" />
    </div>
    <p class="pdf-caption">PDF 物理页码 · 仅预览当前页，核对完成后请手动勾选</p>
  </section>
</template>

<style scoped>
.pdf-preview{min-width:0;border-right:1px solid var(--el-border-color-light);background:var(--el-fill-color-light)}
.pdf-heading{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;padding:12px 16px;border-bottom:1px solid var(--el-border-color-light);background:var(--el-bg-color)}
.pdf-heading strong{display:flex;align-items:center;gap:7px;font-size:13px}.pdf-heading :deep(.el-button>span),.pdf-message :deep(.el-button>span){display:flex;gap:6px;align-items:center}
.pdf-stage{box-sizing:border-box;min-height:320px;max-height:640px;overflow:auto;padding:16px;overscroll-behavior:contain}.pdf-stage:focus-visible{outline:2px solid var(--el-color-primary);outline-offset:-2px}
.pdf-stage img{display:block;width:100%;height:auto;box-shadow:0 2px 8px #00000014;background:white}.pdf-stage.is-enlarged img{width:200%;max-width:none}
.pdf-message{font-size:13px;text-align:center;color:var(--el-text-color-secondary);padding:40px 8px;line-height:1.8;overflow-wrap:anywhere}.pdf-caption{font-size:12px;color:var(--el-text-color-secondary);margin:0;padding:8px 16px 12px;line-height:1.6}
@media(max-width:1100px){.pdf-preview{border-right:0;border-bottom:1px solid var(--el-border-color-light)}}
</style>
