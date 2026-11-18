// Runs only against the disposable emulator selected by the Python test.
import { _android as android } from 'playwright-core';
import { expect } from '@playwright/test';
let input = '';
for await (const chunk of process.stdin) input += chunk;
const { serial, origin, phase, code } = JSON.parse(input);
let device, step = 'connect WebView';
const button = (page, name) => page.getByRole('button', { name, exact: true });
try {
  device = (await android.devices({ omitDriverInstall: true })).find(d => d.serial() === serial);
  expect(device).toBeTruthy();
  device.setDefaultTimeout(20000);
  const webview = await device.webView({ pkg: 'com.refineview.wizzy' });
  const page = await webview.page();
  await page.waitForURL(origin + '/?end=child');
  async function privateChild() {
    const identity = await page.evaluate(async () => {
      const response = await fetch('/api/v1/auth/me', { headers: { 'X-Family-Session': 'browser' } });
      const body = await response.json();
      return { status: response.status, audience: body.data?.audience, cookie: document.cookie,
        local: Object.fromEntries(Object.entries(localStorage).filter(([key]) => key !== '__DC_STAT_UUID')) };
    });
    expect(identity).toEqual({ status: 200, audience: 'child', cookie: '', local: {} });
    await expect(button(page, '下载 APP')).toHaveCount(0);
  }
  if (phase === 'pair') {
    step = 'fill pairing code';
    await page.getByLabel('一次性配对码', { exact: true }).fill(code);
    step = 'submit pairing code';
    await button(page, '连接并开始学习').click();
    step = 'open paired child space';
    await expect(button(page, '快捷登录')).toBeVisible();
    step = 'check child session privacy';
    await privateChild();
    step = 'open native login settings';
    await button(page, '快捷登录').click();
    await expect(button(page, '关闭自动登录')).toBeVisible();
  } else if (phase === 'open-image-picker') {
    step = 'open native image chooser';
    await button(page, '收起设置').click();
    await page.getByLabel('选择作业图片', { exact: true }).click();
  } else if (phase === 'image-selected') {
    step = 'receive selected native image in WebView';
    await expect(page.getByText('wizzy-native-picker.jpg', { exact: true })).toBeVisible();
    await expect(page.locator('.page-grid img')).toHaveCount(1);
    await button(page, '移除第1页').click();
    await expect(page.locator('.page-grid img')).toHaveCount(0);
  } else if (phase === 'restore-and-logout') {
    step = 'restore expired session on app restart';
    await expect(button(page, '快捷登录')).toBeVisible();
    await privateChild();
    step = 'parent password gate';
    await button(page, '家长中心').click();
    await expect(page.getByLabel('登录密码', { exact: true })).toBeVisible();
    await expect(button(page, '免密进入学习')).toHaveCount(0);
    await button(page, '返回学习').click();
    step = 'logout and forget native device';
    await button(page, '退出登录').click();
    await expect(button(page, '连接孩子的学习设备')).toBeVisible();
  } else {
    step = 'signed out after restart or remote revocation';
    await expect(button(page, '连接并开始学习')).toBeVisible();
    await expect(button(page, '快捷登录')).toHaveCount(0);
    const status = await page.evaluate(async () => (await fetch('/api/v1/auth/me', {
      headers: { 'X-Family-Session': 'browser' } })).status);
    expect(status).toBe(401);
  }
  process.stdout.write(JSON.stringify({ phase, result: 'passed' }));
} catch {
  process.stderr.write('Android check failed at ' + step + '\n');
  process.exitCode = 1;
} finally { await device?.close(); }
