import type { Transport } from './api';
import { inNativeApp, nativeRequest } from './native-app';

const logoutMarker = 'wizzy-family-logout-pending';
export function logoutPending() {
  // #ifdef H5
  return localStorage.getItem(logoutMarker) === '1';
  // #endif
  // #ifndef H5
  return false;
  // #endif
}

// H5 uses a same-origin reverse proxy. WeChat requires an explicitly configured HTTPS API domain.
export const request: Transport = (path, options) => {
  let origin = '';
  const headers = { ...options.headers };
  // #ifdef H5
  headers['X-Family-Session'] = 'browser';
  if (path === '/api/v1/auth/logout') localStorage.setItem(logoutMarker, '1');
  // #endif
  // #ifdef MP-WEIXIN
  origin = import.meta.env.VITE_FAMILY_API_ORIGIN ?? '';
  if (!/^https:\/\/[^/]+$/.test(origin)) return Promise.reject(new Error('API domain is not configured'));
  // #endif
  const dispatch = () => new Promise<Awaited<ReturnType<Transport>>>((resolve, reject) => {
    uni.request({ url: origin + path, method: options.method, data: options.body as UniApp.RequestOptions['data'],
      header: headers, responseType: options.responseType ?? 'json', timeout: 15000,
      success: result => {
        // #ifdef H5
        if ((path === '/api/v1/auth/logout' && result.statusCode === 401) ||
            (result.statusCode >= 200 && result.statusCode < 300 &&
             ['/api/v1/auth/logout', '/api/v1/auth/login', '/api/v1/auth/register', '/api/v1/child-sessions'].includes(path))) localStorage.removeItem(logoutMarker);
        // #endif
        resolve({ status: result.statusCode, body: result.data });
      }, fail: reject });
  });
  return dispatch().then(async result => {
    // #ifdef H5
    if (result.status === 401 && path === '/api/v1/auth/me' && inNativeApp() && !logoutPending()) {
      try { if ((await nativeRequest('resumeSession')).restored === true) return dispatch(); } catch { /* Preserve the original authentication failure. */ }
    }
    // #endif
    return result;
  });
};
