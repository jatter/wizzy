// Disposable credentials arrive on stdin; never print tokens or API bodies.
import { chromium, expect } from '@playwright/test';
let input = '';
for await (const chunk of process.stdin) input += chunk;
const { origin, code, owner } = JSON.parse(input);
let browser, step = 'launch', pageErrors = 0;
const button = (page, name) => page.getByRole('button', { name, exact: true });
try {
  browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome' });
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  page.on('pageerror', () => { pageErrors++; });
  const cdp = await context.newCDPSession(page);
  await cdp.send('WebAuthn.enable');
  await cdp.send('WebAuthn.addVirtualAuthenticator', { options: {
    protocol: 'ctap2', transport: 'internal', hasResidentKey: true,
    hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true,
  } });
  step = 'pair child';
  await page.goto(origin + '/?end=child');
  await page.getByLabel('一次性配对码', { exact: true }).fill(code);
  await button(page, '连接并开始学习').click();
  await expect(button(page, '快捷登录')).toBeVisible();
  const devices = await context.request.get(origin + '/api/v1/child-sessions', { headers: owner });
  const roots = (await devices.json()).data.items;
  expect(roots).toHaveLength(1);
  step = 'register resident passkey';
  await button(page, '快捷登录').click();
  await button(page, '设置免密进入').click();
  await expect(page.getByRole('status')).toContainText('免密进入已开启');
  await expect(page.getByRole('button', { name: /^移除免密凭证：/ })).toBeVisible();
  step = 'logout and authenticate';
  await button(page, '退出登录').click();
  await button(page, '免密进入学习').click();
  await expect(button(page, '快捷登录')).toBeVisible();
  step = 'check private child session';
  const identity = await context.request.get(origin + '/api/v1/auth/me', { headers: { 'X-Family-Session': 'browser' } });
  expect((await identity.json()).data.audience).toBe('child');
  const forbidden = await context.request.get(origin + '/api/v1/tenants', { headers: { 'X-Family-Session': 'browser' } });
  expect(forbidden.status()).toBe(403);
  expect(await page.evaluate(() => document.cookie)).toBe('');
  const cookies = await context.cookies(origin + '/api/v1/auth/me');
  expect(cookies.find(c => c.name === 'wizzy_family_session')?.httpOnly).toBe(true);
  step = 'reload child session';
  await page.reload();
  await expect(button(page, '快捷登录')).toBeVisible();
  step = 'guardian password gate';
  await button(page, '家长中心').click();
  await expect(page.getByLabel('登录密码', { exact: true })).toBeVisible();
  await expect(button(page, '免密进入学习')).toHaveCount(0);
  await button(page, '返回学习').click();
  step = 'remote revoke';
  const revoke = await context.request.delete(origin + '/api/v1/child-sessions/' + roots[0].id, { headers: owner });
  expect(revoke.status()).toBe(200);
  await page.reload();
  await expect(button(page, '免密进入学习')).toBeVisible();
  const failed = page.waitForResponse(r => r.url().endsWith('/auth/passkeys/authentication/verify'));
  await button(page, '免密进入学习').click();
  expect((await failed).status()).toBe(401);
  await expect(button(page, '快捷登录')).toHaveCount(0);
  expect(pageErrors).toBe(0);
  process.stdout.write(JSON.stringify({ registration: 'passed', login: 'passed', reload: 'passed',
    guardian_gate: 'passed', remote_revocation: 'passed', page_errors: pageErrors }));
} catch {
  process.stderr.write('Passkey browser check failed at ' + step + '\n');
  process.exitCode = 1;
} finally { await browser?.close(); }
