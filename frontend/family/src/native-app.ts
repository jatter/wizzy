interface NativePort {
  postMessage(message: string): void;
  onmessage: ((event: { data: string }) => void) | null;
}
declare global { interface Window { WizzyApp?: NativePort } }

export function inNativeApp() {
  // #ifdef H5
  return typeof window !== 'undefined' && !!window.WizzyApp && /WizzyAndroid\//.test(navigator.userAgent);
  // #endif
  // #ifndef H5
  return false;
  // #endif
}

let nextId = 0;
const pending = new Map<number, { resolve: (value: Record<string, unknown>) => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> }>();
export function nativeRequest(action: 'rememberDevice' | 'forgetDevice' | 'resumeSession' | 'status' | 'checkUpdate'): Promise<Record<string, unknown>> {
  if (!inNativeApp()) return Promise.reject(new Error('请在小知 APP 中操作。'));
  // #ifdef H5
  const port = window.WizzyApp!;
  port.onmessage = event => {
    let message: { id: number; ok: boolean; data?: Record<string, unknown>; error?: string };
    try { message = JSON.parse(event.data); } catch { return; }
    const task = pending.get(message.id);
    if (!task) return;
    pending.delete(message.id); clearTimeout(task.timer);
    if (message.ok) task.resolve(message.data ?? {});
    else task.reject(new Error(message.error || '设备设置未完成，请重试。'));
  };
  return new Promise((resolve, reject) => {
    const id = ++nextId;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('设备设置超时，请检查网络后重试。')); }, 20000);
    pending.set(id, { resolve, reject, timer });
    try { port.postMessage(JSON.stringify({ id, action })); }
    catch { pending.delete(id); clearTimeout(timer); reject(new Error('设备连接未完成，请重新打开 APP。')); }
  });
  // #endif
  // #ifndef H5
  return Promise.reject(new Error('请在小知 APP 中操作。'));
  // #endif
}
