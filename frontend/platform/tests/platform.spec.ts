import { test, expect, type Page } from '@playwright/test';

const admin = {id:'account-id',email:'security@example.test',role:'security_admin',audience:'platform',
  permissions:['platform.accounts.read','platform.audit.read'],session_id:'session-id',expires_at:'2099-01-01T00:00:00Z'};
const account = {id:'account-1',email:'member@example.test',role:'operator',status:'active',created_at:'2026-10-25T08:00:00Z'};
async function mock(page: Page, identity = admin) {
  const paths: string[] = [];
  await page.route('**/api/v1/admin/**', async route => {
    const path = new URL(route.request().url()).pathname;
    paths.push(path);
    let data: unknown = {revoked:true};
    if (path.endsWith('/auth/captcha')) data = {captcha_id:'11111111-1111-1111-1111-111111111111',image:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='};
    if (path.endsWith('/auth/login')) data = {access_token:'memory-only-platform-token',audience:'platform'};
    if (path.endsWith('/auth/me')) data = identity;
    if (path.endsWith('/roles')) data = {items:[{name:'security_admin',permissions:admin.permissions},{name:'operator',permissions:[]}]};
    if (path.endsWith('/accounts')) data = {items:[account],next_cursor:null};
    if (path.endsWith('/audit-events')) data = {items:[{id:'audit-1',actor_id:admin.id,action:'login',request_id:'req_'+'a'.repeat(32),created_at:'2026-10-25T08:00:00Z'}],next_cursor:null};
    await route.fulfill({json:{data}});
  });
  await page.goto('/');
  return paths;
}
async function login(page: Page) {
  await page.getByLabel('平台邮箱').fill(admin.email);
  await page.getByLabel('登录密码', {exact:true}).fill('test-password');
  await page.getByLabel('验证码').fill('K7M2');
  await page.getByRole('button', {name:'登录工作台',exact:true}).click();
  await expect(page.getByRole('heading',{name:'账号与会话'})).toBeVisible();
}

test('captcha is required; keyboard login; no persistent token; reload requires authentication', async ({page}) => {
  const paths = await mock(page);
  await page.getByLabel('平台邮箱').fill(admin.email);
  await page.getByLabel('登录密码',{exact:true}).fill('test-password');
  await page.getByRole('button',{name:'登录工作台',exact:true}).click();
  expect(paths.filter(path => path.endsWith('/auth/login'))).toHaveLength(0);
  await page.getByLabel('验证码').fill('K7M2');
  await page.getByLabel('验证码').press('Enter');
  await expect(page.getByRole('heading',{name:'账号与会话'})).toBeVisible();
  expect(await page.evaluate(() => ({local:{...localStorage},session:{...sessionStorage},cookie:document.cookie}))).toEqual({local:{},session:{},cookie:''});
  await page.reload();
  await expect(page.getByRole('heading',{name:'登录平台工作台'})).toBeVisible();
});

test('security admin reads real-shaped accounts, roles and audit without write controls', async ({page}) => {
  await mock(page);
  await page.screenshot({path:test.info().outputPath('desktop-login.png'),fullPage:true,animations:'disabled'});
  await login(page);
  await expect(page.getByRole('heading',{name:'授权范围'})).toBeVisible();
  await expect(page.getByText('查看平台账号、安全审计',{exact:true})).toBeVisible();
  await expect(page.getByText('platform.accounts.read')).toHaveCount(0);
  await page.screenshot({path:test.info().outputPath('desktop-session.png'),fullPage:true,animations:'disabled'});
  await page.getByRole('button',{name:'平台账号',exact:true}).click();
  await expect(page.getByText(account.email,{exact:true})).toBeVisible();
  await page.screenshot({path:test.info().outputPath('desktop-accounts.png'),fullPage:true,animations:'disabled'});
  await expect(page.getByRole('button',{name:'下一页'})).toBeDisabled();
  await expect(page.getByRole('button',{name:/新增账号|停用账号/})).toHaveCount(0);
  await page.getByRole('button',{name:'角色权限',exact:true}).click();
  await expect(page.getByRole('cell',{name:'查看平台账号、安全审计',exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:'安全管理员'})).toBeVisible();
  await page.getByRole('button',{name:'显示权限标识',exact:true}).click();
  const accountRow = page.locator('.perm-matrix').getByRole('row',{name:/平台账号/});
  await expect(accountRow.locator('[data-granted="true"]',{hasText:'platform.accounts.read'})).toBeVisible();
  await expect(accountRow.locator('[data-granted="false"]',{hasText:'申请变更'})).toBeVisible();
  await page.screenshot({path:test.info().outputPath('desktop-roles.png'),fullPage:true,animations:'disabled'});
  await page.getByRole('button',{name:'安全审计',exact:true}).click();
  await expect(page.getByText('登录成功',{exact:true})).toBeVisible();
  await expect(page.getByText('req_'+'a'.repeat(32))).toBeVisible();
});

test('operator sees no restricted navigation and sends no restricted requests', async ({page}) => {
  const paths = await mock(page,{...admin,role:'operator',permissions:[]}); await login(page);
  await expect(page.getByRole('button',{name:'平台账号',exact:true})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'安全审计',exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'角色权限',exact:true}).click();
  await expect(page.getByRole('heading',{name:'角色权限'})).toBeVisible();
  await expect(page.getByRole('cell',{name:'只能查看自己的会话和角色目录',exact:true})).toBeVisible();
  expect(paths.some(p => /accounts|audit-events/.test(p))).toBe(false);
});

test('cursor forward and back, empty results, and refresh start from first page', async ({page}) => {
  await mock(page);
  const queries: string[] = [];
  await page.route('**/api/v1/admin/accounts?**', route => {
    const query = new URL(route.request().url()).searchParams;
    queries.push(query.get('cursor') ?? '');
    return route.fulfill({json:{data:query.has('cursor') ? {items:[],next_cursor:null} : {items:[account],next_cursor:'opaque+/='}}});
  });
  await login(page); await page.getByRole('button',{name:'平台账号',exact:true}).click();
  await expect(page.getByText(account.email,{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'下一页'}).click();
  await expect(page.getByText('暂无平台账号')).toBeVisible();
  await page.getByRole('button',{name:'上一页'}).click();
  await expect(page.getByText(account.email,{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'刷新',exact:true}).click();
  await expect.poll(() => queries).toEqual(['','opaque+/=','','']);
});

test('403 clears old rows but keeps session, with safe request id and retry', async ({page}) => {
  await mock(page); await login(page);
  await page.getByRole('button',{name:'平台账号',exact:true}).click();
  await expect(page.getByText(account.email,{exact:true})).toBeVisible();
  await page.route('**/api/v1/admin/accounts?**',route => route.fulfill({status:403,json:{error:{message:'SECRET'},request_id:'req_'+'b'.repeat(32)}}));
  await page.getByRole('button',{name:'刷新',exact:true}).click();
  await expect(page.getByText('当前账号没有此项权限，请联系安全管理员。')).toBeVisible();
  await expect(page.getByText(account.email,{exact:true})).toHaveCount(0);
  await expect(page.getByText('SECRET')).toHaveCount(0);
  await expect(page.getByText('req_'+'b'.repeat(32))).toBeVisible();
  await expect(page.getByRole('button',{name:'退出登录',exact:true})).toBeVisible();
});

test('401 clears account view and returns to login', async ({page}) => {
  await mock(page); await login(page);
  await page.route('**/api/v1/admin/accounts?**',route => route.fulfill({status:401,json:{error:{}}}));
  await page.getByRole('button',{name:'平台账号',exact:true}).click();
  await expect(page.getByRole('heading',{name:'登录平台工作台'})).toBeVisible();
  await expect(page.getByText(admin.email,{exact:true})).toHaveCount(0);
});

test('proxy unavailable is recoverable without leaking upstream text', async ({page}) => {
  await mock(page);
  await page.route('**/api/v1/admin/auth/login', route => route.fulfill({status:502,body:'SECRET upstream'}));
  await page.getByLabel('平台邮箱').fill(admin.email);
  await page.getByLabel('登录密码',{exact:true}).fill('test-password');
  await page.getByLabel('验证码').fill('K7M2');
  await page.getByRole('button',{name:'登录工作台',exact:true}).click();
  await expect(page.getByText('服务暂时不可用，请稍后重试。')).toBeVisible();
  await expect(page.getByText('SECRET upstream')).toHaveCount(0);
  await expect(page.getByLabel('登录密码',{exact:true})).toHaveValue('');
  await expect(page.getByLabel('验证码')).toHaveValue('');
});

for (const all of [false,true]) test(`logout ${all ? 'all' : 'current'} failure clears locally and states revocation unconfirmed`, async ({page}) => {
  await mock(page); await login(page);
  await page.route('**/api/v1/admin/auth/logout*', route => route.abort());
  if (all) {
    await page.getByRole('button',{name:'退出全部会话'}).click();
    await page.getByRole('button',{name:'确认退出全部'}).click();
  } else await page.getByRole('button',{name:'退出登录',exact:true}).click();
  await expect(page.getByRole('heading',{name:'登录平台工作台'})).toBeVisible();
  await expect(page.getByText(/服务端撤销未确认/)).toBeVisible();
});

test('focus revalidation revokes navigation and removes rows', async ({page}) => {
  await mock(page); await login(page);
  await page.getByRole('button',{name:'平台账号',exact:true}).click();
  await expect(page.getByText(account.email,{exact:true})).toBeVisible();
  await page.route('**/api/v1/admin/auth/me', route => route.fulfill({json:{data:{...admin,role:'operator',permissions:[]}}}));
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(page.getByRole('heading',{name:'账号与会话'})).toBeVisible();
  await expect(page.getByRole('button',{name:'平台账号',exact:true})).toHaveCount(0);
  await expect(page.getByText(account.email,{exact:true})).toHaveCount(0);
});

test('late account response after logout does not resurrect sensitive rows', async ({page}) => {
  await mock(page); await login(page);
  let release!: () => void;
  const gate = new Promise<void>(r => {release=r;});
  await page.route('**/api/v1/admin/accounts?**', async route => {await gate; await route.fulfill({json:{data:{items:[account],next_cursor:null}}});});
  const request = page.waitForRequest('**/api/v1/admin/accounts?**');
  await page.getByRole('button',{name:'平台账号',exact:true}).click(); await request;
  await page.getByRole('button',{name:'退出登录',exact:true}).click();
  release();
  await expect(page.getByRole('heading',{name:'登录平台工作台'})).toBeVisible();
  await expect(page.getByText(account.email,{exact:true})).toHaveCount(0);
});

test('session expiry logs out automatically', async ({page}) => {
  await page.clock.install();
  await mock(page,{...admin,expires_at:new Date(Date.now()+3000).toISOString()}); await login(page);
  await page.clock.fastForward(4000);
  await expect(page.getByRole('heading',{name:'登录平台工作台'})).toBeVisible();
});

test('390px viewport has no document overflow and supports navigation', async ({page}) => {
  await page.setViewportSize({width:390,height:844});
  await mock(page); await login(page);
  await expect(page.getByRole('heading',{name:'授权范围'})).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path:test.info().outputPath('mobile-session.png'),fullPage:true,animations:'disabled'});
  await page.getByRole('button',{name:'平台账号',exact:true}).click();
  await expect(page.getByText(account.email,{exact:true})).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path:test.info().outputPath('mobile-accounts.png'),fullPage:true,animations:'disabled'});
  await page.getByRole('button',{name:'角色权限',exact:true}).click();
  await expect(page.getByRole('heading',{name:'安全管理员'})).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path:test.info().outputPath('mobile-roles.png'),fullPage:true,animations:'disabled'});
});

test('malformed identity clears old identity and malformed lists display a recoverable error', async ({page}) => {
  await mock(page); await login(page);
  await page.route('**/api/v1/admin/accounts?**', route => route.fulfill({json:{data:{items:null,next_cursor:42}}}));
  await page.getByRole('button',{name:'平台账号',exact:true}).click();
  await expect(page.getByText('服务暂时不可用，请稍后重试。')).toBeVisible();
  await expect(page.getByRole('button',{name:'退出登录',exact:true})).toBeVisible();
  await page.route('**/api/v1/admin/auth/me', route => route.fulfill({json:{data:{audience:'guardian'}}}));
  await page.getByRole('button',{name:'刷新',exact:true}).click();
  await expect(page.getByRole('heading',{name:'登录平台工作台'})).toBeVisible();
  await expect(page.getByText(admin.email,{exact:true})).toHaveCount(0);
});
