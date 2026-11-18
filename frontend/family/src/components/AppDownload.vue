<script setup lang="ts">
import { nextTick, onUnmounted, ref } from 'vue';
import FamilyButton from './FamilyButton.vue';
import { inNativeApp } from '../native-app';
// #ifdef H5
import qrcode from 'qrcode-generator';
// #endif

interface Release { version: string; version_code: number; size: number; sha256: string; url: string }
const visible = ref(false), loading = ref(false), error = ref(''), release = ref<Release | null>(null), qr = ref('');
const native = inNativeApp(), nativeAnchor = 'a';
let trigger: HTMLElement | null = null, controller: AbortController | undefined;
function close() {
  visible.value = false; controller?.abort();
  // #ifdef H5
  document.removeEventListener('keydown', keydown); trigger?.focus();
  // #endif
}
function keydown(event: KeyboardEvent) {
  // #ifdef H5
  if (event.key === 'Escape') { event.preventDefault(); close(); }
  if (event.key === 'Tab') {
    const items = Array.from(document.querySelectorAll<HTMLElement>('.download-dialog button:not(:disabled), .download-dialog a[href]'));
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
  // #endif
}
async function open(event: unknown) {
  // #ifdef H5
  trigger = (event as Event).currentTarget as HTMLElement;
  visible.value = true; loading.value = true; error.value = ''; release.value = null;
  document.addEventListener('keydown', keydown);
  await nextTick(); document.querySelector<HTMLElement>('.download-dialog button')?.focus();
  controller = new AbortController();
  const timer = setTimeout(() => controller?.abort(), 10000);
  try {
    const response = await fetch('/downloads/android/latest.json', { cache: 'no-store', signal: controller.signal });
    if (!response.ok) throw new Error('missing');
    const value = await response.json();
    if (typeof value.version !== 'string' || !Number.isInteger(value.version_code) ||
        !Number.isFinite(value.size) || value.size <= 0 || !/^[a-f0-9]{64}$/.test(value.sha256) ||
        !/^\/downloads\/android\/[a-zA-Z0-9._-]+\.apk$/.test(value.url)) throw new Error('invalid');
    release.value = value;
    const code = qrcode(0, 'M'); code.addData(new URL(value.url, location.origin).href); code.make(); qr.value = code.createDataURL(4, 16);
  } catch { if (visible.value) error.value = '暂时无法获取安装包，请检查网络后重新打开下载。'; }
  finally { clearTimeout(timer); loading.value = false; }
  // #endif
}
onUnmounted(() => {
  controller?.abort();
  // #ifdef H5
  document.removeEventListener('keydown', keydown);
  // #endif
});
</script>

<template>
  <!-- #ifdef H5 -->
  <view v-if="!native" class="app-download">
    <FamilyButton class="text-button wide" @click="open">下载 APP</FamilyButton>
    <view v-if="visible" class="download-backdrop" @click.self="close">
      <view class="download-dialog panel" role="dialog" aria-modal="true" aria-labelledby="app-download-title">
        <view class="section-head"><text id="app-download-title" class="section-title">把小知装到学习设备上</text><FamilyButton class="text-button compact" aria-label="关闭 APP 下载" @click="close">关闭</FamilyButton></view>
        <text class="muted">由家长完成安装和首次配对，孩子以后点击桌面图标即可打开。</text>
        <text v-if="loading" class="notice" role="status">正在获取安装包…</text>
        <text v-if="error" class="notice" role="alert">{{ error }}</text>
        <template v-if="release">
          <image :src="qr" class="download-qr" mode="aspectFit" role="img" aria-label="安卓版 APP 安装包下载二维码" />
          <text class="small muted">电脑上打开时，可用安卓手机或平板扫描二维码。</text>
          <component :is="nativeAnchor" :href="release.url" download class="primary download-link">下载安卓版 APK</component>
          <text class="small muted">版本 {{ release.version }} · {{ (release.size / 1024 / 1024).toFixed(1) }} MB · Android 8.0 及以上</text>
          <view class="download-steps"><text>1. 下载完成后打开安装包。</text><text>2. 按系统提示允许本次来源安装，由家长确认。</text><text>3. 打开小知，在“连接孩子的学习设备”输入家长提供的配对码。</text></view>
          <text class="small muted">iPhone / iPad 暂无安装包，可先使用网页版。APP 需要联网使用。</text>
          <text class="download-checksum small muted">安装包 SHA-256：{{ release.sha256 }}</text>
        </template>
      </view>
    </view>
  </view>
  <!-- #endif -->
</template>

<style scoped>
.app-download{margin-top:8px}.download-backdrop{position:fixed;inset:0;z-index:1000;display:flex;align-items:center;justify-content:center;padding:20px;background:#182b4070}.download-dialog{display:flex;flex-direction:column;gap:14px;width:100%;max-width:440px;max-height:calc(100dvh - 40px);overflow:auto;box-sizing:border-box}.download-dialog .section-head{gap:12px}.download-qr{align-self:center;width:180px;height:180px}.download-link{display:block;padding:13px 18px;border-radius:10px;text-decoration:none;text-align:center;font-weight:600}.download-steps{display:flex;flex-direction:column;gap:8px;font-size:13px;line-height:1.8}.download-checksum{overflow-wrap:anywhere;word-break:break-all}@media(max-width:400px){.download-backdrop{padding:12px}.download-dialog{padding:20px;max-height:calc(100dvh - 24px)}}
</style>
