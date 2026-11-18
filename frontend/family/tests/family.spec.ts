import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ status: 401, json: { error: { code: 'authentication_required' } } }));
});

test('统一入口没有身份选项，设备连接只需配对码', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: /家长入口|孩子入口/ })).toHaveCount(0);
  await expect(page.getByText('平台管理端', { exact: true })).toHaveCount(0);
  await page.screenshot({ path: test.info().outputPath('login-desktop.png'), fullPage: true });
  const child = page.getByRole('button', { name: '连接孩子的学习设备', exact: true });
  await expect(child).toBeVisible();
  await child.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('textbox', { name: '一次性配对码', exact: true })).toBeVisible();
  await expect(page.getByRole('textbox', { name: '设备名称', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '返回登录', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '用户名', exact: true })).toBeVisible();
});

test('注册支持键盘操作，授权默认关闭', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '创建账号', exact: true }).click();
  const consent = page.getByRole('checkbox', { name: '我已阅读并同意上述隐私说明' });
  await expect(consent).not.toBeChecked();
  const username = page.getByRole('textbox', { name: '用户名', exact: true });
  await expect(username).toHaveAttribute('type', 'text');
  await username.fill('liyuhan');
  await page.getByLabel('登录密码', { exact: true }).fill('123456789');
  const register = page.getByRole('button', { name: '创建账号', exact: true });
  await expect(register).toBeDisabled();
  await consent.focus();
  await page.keyboard.press('Space');
  await expect(consent).toBeChecked();
  await expect(register).toBeEnabled();
});

test('旧孩子链接仍能打开设备连接，切回登录后去掉角色参数', async ({ page }) => {
  await page.goto('/?end=child');
  await expect(page.getByRole('textbox', { name: '一次性配对码', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '返回登录', exact: true }).click();
  await expect(page).not.toHaveURL(/end=/);
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible();
});

test('手机登录页无横向溢出', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  await page.goto('/');
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(await page.evaluate(() => {
    const panel = document.querySelector('.auth-panel')!.getBoundingClientRect();
    return [...document.querySelectorAll('.auth-panel input')].every(input => input.getBoundingClientRect().right < panel.right);
  })).toBe(true);
  await page.screenshot({ path: test.info().outputPath('login-mobile.png'), fullPage: true });
});

test('家庭登录失败提示账号类型，并保留用户名供重试', async ({ page }) => {
  await page.route('**/api/v1/auth/login', route => route.fulfill({ status: 401, json: { error: { code: 'authentication_required' } } }));
  await page.goto('/');
  await page.getByLabel('用户名', { exact: true }).fill('parent@example.test');
  await page.getByLabel('登录密码', { exact: true }).fill('wrong-password');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('家庭账号');
  await expect(page.getByRole('alert')).not.toContainText('已失效');
  await expect(page.getByLabel('用户名', { exact: true })).toHaveValue('parent@example.test');
  await expect(page.getByLabel('登录密码', { exact: true })).toHaveValue('');
});
