import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ status: 401, json: {} }));
});

test('APP 下载展示真实发布信息、二维码和安装说明，并支持键盘关闭', async ({ page }) => {
  await page.route('**/downloads/android/latest.json', route => route.fulfill({ json: {
    version: '0.1.0', version_code: 1, size: 4000000, sha256: 'a'.repeat(64), url: '/downloads/android/wizzy-0.1.0.apk',
  } }));
  await page.goto('/');
  const download = page.getByRole('button', { name: '下载 APP', exact: true });
  await download.click();
  const dialog = page.getByRole('dialog', { name: '把小知装到学习设备上' });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole('link', { name: '下载安卓版 APK' })).toHaveAttribute('href', '/downloads/android/wizzy-0.1.0.apk');
  await expect(dialog.getByRole('img', { name: '安卓版 APP 安装包下载二维码' })).toBeVisible();
  await expect(dialog).toContainText('Android 8.0');
  await expect(dialog).toContainText('由家长确认');
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(download).toBeFocused();
});

test('320px 屏幕可下载 APP，缺失或不安全的安装包信息不展示下载链接', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  await page.route('**/downloads/android/latest.json', route => route.fulfill({ json: {
    version: '1', version_code: 1, size: 4000000, sha256: 'a'.repeat(64), url: 'https://attacker.test/download.apk',
  } }));
  await page.goto('/');
  await page.getByRole('button', { name: '下载 APP', exact: true }).click();
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('暂时无法获取安装包');
  await expect(page.getByRole('link', { name: '下载安卓版 APK' })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('原生 APP 内隐藏下载入口，不支持设备免密时保留配对码', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'userAgent', { value: navigator.userAgent + ' WizzyAndroid/0.1.0' });
    const port = { onmessage: null as null | ((event: { data: string }) => void), postMessage(raw: string) {
      const { id } = JSON.parse(raw);
      queueMicrotask(() => port.onmessage?.({ data: JSON.stringify({ id, ok: true, data: { passkeys: false, remembered: false } }) }));
    } };
    (window as any).WizzyApp = port;
  });
  await page.goto('/?end=child');
  await expect(page.getByRole('button', { name: '连接并开始学习', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '下载 APP', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '免密进入学习', exact: true })).toHaveCount(0);
});
