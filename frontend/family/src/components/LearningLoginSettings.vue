<script setup lang="ts">
import { onMounted, ref } from 'vue';
import type { FamilyApi, LearningPasskey } from '../api';
import { inNativeApp, nativeRequest } from '../native-app';
import { registerLearningPasskey, supportsPasskeys, passkeyError } from '../passkeys';
import FamilyButton from './FamilyButton.vue';

const props = defineProps<{ api: FamilyApi }>();
const emit = defineEmits<{ close: [] }>();
const items = ref<LearningPasskey[]>([]), busy = ref(false), message = ref('');
const native = inNativeApp(), available = ref(supportsPasskeys()), remembered = ref(false);
async function refresh() { items.value = (await props.api.passkeys()).items; }
async function register() {
  busy.value = true; message.value = '';
  try { await registerLearningPasskey(props.api, native ? '小知 APP 学习设备' : '浏览器学习设备'); await refresh(); message.value = '免密进入已开启。下次可在登录页选择“免密进入学习”。'; }
  catch (error) { message.value = passkeyError(error); }
  finally { busy.value = false; }
}
async function remove(item: LearningPasskey) {
  busy.value = true; message.value = '';
  try { await props.api.revokePasskey(item.id); await refresh(); message.value = '已移除这条免密凭证。'; }
  catch { message.value = '移除未完成，请检查网络后重试。'; }
  finally { busy.value = false; }
}
async function remember() {
  busy.value = true; message.value = '';
  try { const status = await nativeRequest(remembered.value ? 'forgetDevice' : 'rememberDevice'); remembered.value = status.remembered === true; message.value = remembered.value ? '已开启自动进入学习，家长可以随时撤销这台设备。' : '已关闭这台设备的自动登录。'; }
  catch (error) { message.value = error instanceof Error ? error.message : '设置未完成，请重试。'; }
  finally { busy.value = false; }
}
async function update() {
  try { await nativeRequest('checkUpdate'); } catch (error) { message.value = error instanceof Error ? error.message : '检查更新失败，请重试。'; }
}
onMounted(async () => {
  busy.value = true;
  try {
    await refresh();
    if (native) { const status = await nativeRequest('status'); remembered.value = status.remembered === true; available.value = available.value && status.passkeys === true; }
  } catch { message.value = '暂时无法读取快捷登录设置，请稍后重试。'; }
  finally { busy.value = false; }
});
</script>

<template>
  <view class="panel login-settings" role="region" aria-label="学习设备快捷登录">
    <view class="section-head"><text class="section-title">下次更方便地进入学习</text><FamilyButton class="text-button compact" :disabled="busy" @click="emit('close')">收起设置</FamilyButton></view>
    <text class="muted">快捷登录仅用于孩子的学习空间。进入家长中心仍需家长密码，不会采集或上传人脸照片。</text>
    <view v-if="native" class="setting-row"><view><text class="section-title">这台设备自动进入学习</text><text class="muted small">首次配对后可开启，有效期内打开 APP 即可学习。</text></view><FamilyButton class="secondary compact" :disabled="busy" @click="remember">{{ remembered ? '关闭自动登录' : '开启自动登录' }}</FamilyButton></view>
    <view class="setting-row"><view><text class="section-title">指纹 / 人脸等设备免密</text><text class="muted small">由设备选择认证方式，也可能使用屏幕锁密码。建议在孩子自己的学习设备上设置。</text></view><FamilyButton v-if="available" class="secondary compact" :disabled="busy" @click="register">设置免密进入</FamilyButton><text v-else class="small muted">当前连接或设备暂不支持，可继续使用配对码。</text></view>
    <view v-for="item in items" :key="item.id" class="setting-row"><text>{{ item.name }}</text><FamilyButton class="danger-text compact" :disabled="busy" :aria-label="'移除免密凭证：' + item.name" @click="remove(item)">移除</FamilyButton></view>
    <text v-if="message" class="notice" role="status">{{ message }}</text>
    <FamilyButton v-if="native" class="text-button" @click="update">检查 APP 更新</FamilyButton>
  </view>
</template>

<style scoped>
.login-settings{margin:20px 0}.login-settings>.muted{display:block;margin:12px 0}.setting-row{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:18px 0;border-top:1px solid var(--line)}.setting-row>view{flex:1;min-width:0}.setting-row .muted{display:block;margin-top:6px}.setting-row .family-button{flex-shrink:0}@media(max-width:600px){.setting-row{flex-wrap:wrap}.setting-row>view{flex-basis:100%}}
</style>
