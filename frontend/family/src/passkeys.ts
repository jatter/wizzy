// #ifdef H5
import { startAuthentication, startRegistration } from '@simplewebauthn/browser';
// #endif
import type { FamilyApi } from './api';

export function supportsPasskeys() {
  // #ifdef H5
  return typeof window !== 'undefined' && window.isSecureContext && typeof window.PublicKeyCredential !== 'undefined';
  // #endif
  // #ifndef H5
  return false;
  // #endif
}
export async function registerLearningPasskey(api: FamilyApi, name: string) {
  // #ifdef H5
  const optionsJSON = await api.passkeyRegistrationOptions();
  const credential = await startRegistration({ optionsJSON });
  return api.registerPasskey(credential, name);
  // #endif
  // #ifndef H5
  throw new Error('请在支持免密登录的浏览器或 APP 中操作。');
  // #endif
}
export async function authenticateLearningPasskey(api: FamilyApi) {
  // #ifdef H5
  const optionsJSON = await api.passkeyAuthenticationOptions();
  const credential = await startAuthentication({ optionsJSON });
  return api.loginWithPasskey(credential);
  // #endif
  // #ifndef H5
  throw new Error('请在支持免密登录的浏览器或 APP 中操作。');
  // #endif
}
export function passkeyError(error: unknown) {
  if (error instanceof Error && ['NotAllowedError', 'AbortError'].includes(error.name)) return '已取消免密操作，你仍可使用原来的方式进入。';
  if (error instanceof Error && error.name === 'InvalidStateError') return '这台设备已有对应的免密凭证，可直接使用免密进入学习。';
  return '免密操作未完成。请检查设备是否已设置屏幕锁，也可以继续使用配对码。';
}
