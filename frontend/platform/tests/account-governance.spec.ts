import { test, expect, type Page } from '@playwright/test';
import type { AccountDetail, AccountChange, Identity } from '../src/api';

const actor = '10000000-0000-4000-8000-000000000001';
const otherActor = '10000000-0000-4000-8000-000000000002';
const target: AccountDetail = {id:'20000000-0000-4000-8000-000000000001',email:'operator@example.test',
  role:'operator',status:'active',revision:0,created_at:'2026-10-26T00:00:00Z'};
const admin: Identity = {id:actor,email:'security@example.test',role:'security_admin',audience:'platform',
  permissions:['platform.accounts.read','platform.accounts.write','platform.accounts.approve'],
  session_id:'session',expires_at:'2099-01-01T00:00:00Z'};
const application = (overrides: Partial<AccountChange> = {}): AccountChange => ({
  id:crypto.randomUUID(),account_id:target.id,actor_id:otherActor,expected_revision:0,
  previous_role:'operator',previous_status:'active',role:'security_admin',status:'active',reason:'申请安全管理职责',
  requires_approval:true,created_at:'2026-10-26T00:00:00Z',state:'pending',decided_by:null,
  decision_reason:null,decided_at:null,resulting_revision:null,stale:false,...overrides,
});

async function setup(page: Page, readonly = false) {
  const state = {identity:{...admin,permissions:readonly ? ['platform.accounts.read'] : [...admin.permissions]},
    item:{...target},history:[] as AccountChange[],older:[] as AccountChange[],
    writes:[] as {path:string;body:Record<string,unknown>}[],status:200,code:'revision_conflict',
    meStatus:200,loseReceipt:false,commits:0,requests:[] as string[]};
  const operations = new Map<string,AccountChange>();
  await page.route('**/api/v1/admin/**', async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname;
    let data: unknown = {revoked:true}, status = 200;
    if (path.endsWith('/auth/captcha')) data = {captcha_id:'11111111-1111-1111-1111-111111111111',image:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='};
    if (path.endsWith('/auth/login')) data = {access_token:'test-only-token',audience:'platform'};
    else if (path.endsWith('/auth/me')) {
      if (state.meStatus !== 200) { await route.fulfill({status:state.meStatus,json:{error:{code:'unauthorized'}}}); return; }
      data = state.identity;
    } else if (path.endsWith('/session-revocations')) data = {items:[],next_cursor:null};
    else if (path.endsWith('/accounts') && req.method() === 'GET') data = {items:[state.item],next_cursor:null};
    else if (path.endsWith('/changes') && req.method() === 'GET') {
      state.requests.push(url.search);
      data = {items:url.searchParams.has('cursor') ? state.older : state.history,
        next_cursor:!url.searchParams.has('cursor') && state.older.length ? 'history+/=' : null};
    } else if (path === `/api/v1/admin/accounts/${target.id}`) data = state.item;
    else if (req.method() === 'POST' && (path.endsWith('/changes') || /\/(approve|reject)$/.test(path))) {
      const body = req.postDataJSON(); state.writes.push({path,body});
      if (state.status !== 200) { await route.fulfill({status:state.status,json:{error:{code:state.code}}}); return; }
      let result = operations.get(body.operation_id);
      if (!result) {
        if (path.endsWith('/changes')) {
          const promotion = body.role==='security_admin' && body.status==='active';
          result = application({actor_id:state.identity.id,expected_revision:body.expected_revision,
            previous_role:state.item.role,previous_status:state.item.status,role:body.role,status:body.status,reason:body.reason,
            requires_approval:promotion,...(!promotion ? {state:'applied',decided_by:state.identity.id,
              decision_reason:body.reason,decided_at:'2026-10-26T01:00:00Z',resulting_revision:state.item.revision+1} : {})});
          state.history.unshift(result); status=201;
        } else {
          const id = path.split('/').at(-2)!;
          const current = state.history.find(c => c.id === id)!;
          result = {...current,state:path.endsWith('/approve') ? 'approved' : 'rejected',stale:false,
            decided_by:state.identity.id,decision_reason:body.reason,decided_at:'2026-10-26T01:00:00Z',
            resulting_revision:path.endsWith('/approve') ? state.item.revision+1 : null};
          state.history = state.history.map(c => c.id === id ? result! : c);
        }
        if (result.resulting_revision !== null) state.item = {...state.item,role:result.role,status:result.status,revision:result.resulting_revision};
        operations.set(body.operation_id,result); state.commits++;
      }
      data=result;
      if (state.loseReceipt) { state.loseReceipt=false; await route.abort('failed'); return; }
    }
    await route.fulfill({status,json:{data}});
  });
  await page.goto('/');
  await page.getByLabel('平台邮箱').fill(admin.email);
  await page.getByLabel('登录密码',{exact:true}).fill('test-only-password');
  await page.getByLabel('验证码').fill('K7M2');
  await page.getByRole('button',{name:'登录工作台',exact:true}).click();
  await page.getByRole('button',{name:'平台账号',exact:true}).click();
  await expect(page.getByText(target.email,{exact:true})).toBeVisible();
  return state;
}
async function open(page: Page) {
  await page.getByRole('button',{name:'管理与历史',exact:true}).click();
  const dialog = page.getByRole('dialog',{name:'平台账号管理',exact:true});
  await expect(dialog.getByRole('heading',{name:target.email,exact:true})).toBeVisible();
  return dialog;
}

test('read-only identity sees details and paginated history without write controls', async ({page}) => {
  const state = await setup(page,true);
  state.history=[application()]; state.older=[application({reason:'较早申请'})];
  const dialog = await open(page);
  await expect(dialog.getByText('当前权限可查询此账号及历史。账号变更需要账号管理权限。')).toBeVisible();
  await expect(dialog.getByRole('combobox',{name:'账号角色',exact:true})).toHaveCount(0);
  await expect(dialog.getByRole('button',{name:'批准申请',exact:true})).toHaveCount(0);
  await dialog.getByRole('button',{name:'加载更早变更',exact:true}).click();
  await expect(dialog.getByText('较早申请',{exact:true})).toBeVisible();
  expect(state.requests.at(-1)).toContain('cursor=history%2B%2F%3D');
  await expect(dialog.getByRole('button',{name:'加载更早变更',exact:true})).toHaveCount(0);
  expect(state.writes).toHaveLength(0);
});

test('disable and enable update revision and render immutable change history', async ({page}) => {
  const state = await setup(page), dialog = await open(page);
  await dialog.getByRole('combobox',{name:'账号状态',exact:true}).selectOption('disabled');
  await dialog.getByRole('button',{name:'保存账号变更',exact:true}).click();
  await expect(dialog.getByRole('alert')).toContainText('未通过校验');
  expect(state.writes).toHaveLength(0);
  await dialog.getByLabel('变更原因',{exact:true}).fill(' 离岗停用 ');
  await dialog.getByRole('button',{name:'保存账号变更',exact:true}).click();
  await expect(dialog.getByText('基于版本 1 提交',{exact:true})).toBeVisible();
  expect(state.writes[0].body).toMatchObject({expected_revision:0,status:'disabled',reason:'离岗停用'});
  await dialog.getByRole('combobox',{name:'账号状态',exact:true}).selectOption('active');
  await dialog.getByLabel('变更原因',{exact:true}).fill('恢复工作');
  await dialog.getByRole('button',{name:'保存账号变更',exact:true}).click();
  await expect(dialog.getByText('基于版本 2 提交',{exact:true})).toBeVisible();
  await expect(dialog.locator('.governance-record')).toHaveCount(2);
  expect(state.writes[1].body).toMatchObject({expected_revision:1,status:'active'});
});

test('promotion remains pending and proposer cannot approve their own application', async ({page}) => {
  const state = await setup(page), dialog = await open(page);
  await dialog.getByRole('combobox',{name:'账号角色',exact:true}).selectOption('security_admin');
  await expect(dialog.getByText('授予启用的安全管理员权限需要独立审批，申请人不能自行批准。')).toBeVisible();
  await dialog.getByLabel('变更原因',{exact:true}).fill('承担安全管理职责');
  await dialog.getByRole('button',{name:'提交权限申请',exact:true}).click();
  await expect(dialog.getByRole('status').filter({hasText:'账号权限尚未改变'})).toBeVisible();
  await expect(dialog.getByText('由另一位有审批权限的管理员处理')).toBeVisible();
  await expect(dialog.getByRole('button',{name:'批准申请',exact:true})).toHaveCount(0);
  expect(state.item).toMatchObject({role:'operator',revision:0});
});

for (const kind of ['approve','reject'] as const) {
  test(`independent approver can ${kind} with a mandatory reason`, async ({page}) => {
    const state = await setup(page); state.history=[application()];
    const dialog = await open(page);
    await dialog.getByRole('button',{name:kind==='approve' ? '批准申请' : '拒绝申请',exact:true}).click();
    const decision = dialog.getByRole('form',{name:'独立审批'});
    await decision.getByRole('button',{name:kind==='approve' ? '确认批准' : '确认拒绝',exact:true}).click();
    expect(state.writes).toHaveLength(0);
    await decision.getByLabel('审批原因',{exact:true}).fill('已独立核对职责');
    await decision.getByRole('button',{name:kind==='approve' ? '确认批准' : '确认拒绝',exact:true}).click();
    await expect(decision).toHaveCount(0);
    await expect(dialog.getByText(kind==='approve' ? '已批准' : '已拒绝',{exact:true})).toBeVisible();
    expect(state.writes[0].path).toContain(`/${kind}`);
    expect(state.history[0].decided_by).toBe(actor);
    expect(state.item.revision).toBe(kind==='approve' ? 1 : 0);
  });
}

test('stale application can only be rejected and last administrator conflicts preserve the draft', async ({page}) => {
  const state = await setup(page); state.history=[application({stale:true})];
  const dialog = await open(page);
  await expect(dialog.getByRole('button',{name:'批准申请',exact:true})).toBeDisabled();
  await expect(dialog.getByRole('button',{name:'拒绝申请',exact:true})).toBeEnabled();
  state.status=409; state.code='last_security_admin';
  await dialog.getByRole('combobox',{name:'账号状态',exact:true}).selectOption('disabled');
  await dialog.getByLabel('变更原因',{exact:true}).fill('需保留的草稿');
  await dialog.getByRole('button',{name:'保存账号变更',exact:true}).click();
  await expect(dialog.getByRole('alert')).toContainText('至少一个启用的安全管理员');
  await expect(dialog.getByLabel('变更原因',{exact:true})).toHaveValue('需保留的草稿');
  await expect(dialog.getByRole('button',{name:'保存账号变更',exact:true})).toBeDisabled();
  state.item.revision=3;
  await dialog.getByRole('button',{name:'刷新账号版本并清除草稿',exact:true}).click();
  await expect(dialog.getByLabel('变更原因',{exact:true})).toHaveValue('');
  await expect(dialog.getByText('基于版本 3 提交',{exact:true})).toBeVisible();
});

for (const kind of ['change','approve'] as const) {
  test(`lost ${kind} receipt retries the identical operation without a second commit`, async ({page}) => {
    const state = await setup(page); state.loseReceipt=true;
    if (kind==='approve') state.history=[application()];
    const dialog = await open(page);
    if (kind==='change') {
      await dialog.getByRole('combobox',{name:'账号状态',exact:true}).selectOption('disabled');
      await dialog.getByLabel('变更原因',{exact:true}).fill('网络重试');
      await dialog.getByRole('button',{name:'保存账号变更',exact:true}).click();
    } else {
      await dialog.getByRole('button',{name:'批准申请',exact:true}).click();
      await dialog.getByLabel('审批原因',{exact:true}).fill('独立复核');
      await dialog.getByRole('button',{name:'确认批准',exact:true}).click();
    }
    await expect(dialog.getByRole('alert')).toContainText('无法连接');
    await expect(dialog.getByLabel(kind==='change' ? '变更原因' : '审批原因',{exact:true})).toBeDisabled();
    await dialog.getByRole('button',{name:kind==='change' ? '原样重试变更' : '原样重试审批',exact:true}).click();
    await expect(dialog.getByRole('alert')).toHaveCount(0);
    await expect(dialog.getByText('基于版本 1 提交',{exact:true})).toBeVisible();
    expect(state.writes).toHaveLength(2);
    expect(state.writes[1]).toEqual(state.writes[0]);
    expect(state.commits).toBe(1);
  });
}

test('focus preserves a draft, revoking write permission clears it and blocks submission', async ({page}) => {
  const state = await setup(page), dialog = await open(page);
  await dialog.getByLabel('变更原因',{exact:true}).fill('窗口切换保留');
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(page.getByRole('button',{name:'管理与历史',exact:true})).toBeVisible();
  await expect(dialog.getByLabel('变更原因',{exact:true})).toHaveValue('窗口切换保留');
  state.identity.permissions=['platform.accounts.read'];
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(dialog.getByLabel('变更原因',{exact:true})).toHaveCount(0);
  await expect(dialog.getByText('当前权限可查询此账号及历史。账号变更需要账号管理权限。')).toBeVisible();
  expect(state.writes).toHaveLength(0);
  state.identity.permissions=[];
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(dialog).not.toBeVisible();
  await expect(page.getByRole('button',{name:'平台账号',exact:true})).toHaveCount(0);
});

test('submit rechecks permission and session expiry clears account details', async ({page}) => {
  const state = await setup(page), dialog = await open(page);
  await dialog.getByRole('combobox',{name:'账号状态',exact:true}).selectOption('disabled');
  await dialog.getByLabel('变更原因',{exact:true}).fill('写入前撤权');
  state.identity.permissions=['platform.accounts.read'];
  await dialog.getByRole('button',{name:'保存账号变更',exact:true}).click();
  await expect(dialog.getByLabel('变更原因',{exact:true})).toHaveCount(0);
  expect(state.writes).toHaveLength(0);
  state.meStatus=401;
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(page.getByRole('heading',{name:'登录平台工作台'})).toBeVisible();
  await expect(dialog).not.toBeVisible();
  await expect(page.getByText(target.email,{exact:true})).toHaveCount(0);
});

test('a delayed detail response cannot reopen a closed account dialog', async ({page}) => {
  await setup(page);
  let release!: () => void;
  const wait = new Promise<void>(resolve => {release=resolve;});
  let received!: () => void;
  const started = new Promise<void>(resolve => {received=resolve;});
  await page.route(`**/api/v1/admin/accounts/${target.id}`, async route => {
    received(); await wait;
    await route.fulfill({json:{data:{...target,email:'late@example.test'}}});
  });
  await page.getByRole('button',{name:'管理与历史',exact:true}).click();
  await started;
  const dialog = page.getByRole('dialog',{name:'平台账号管理',exact:true});
  await dialog.getByRole('button',{name:'关闭账号管理',exact:true}).click();
  release();
  await expect(dialog).not.toBeVisible();
  await expect(page.getByText('late@example.test',{exact:true})).toHaveCount(0);
});

test('390px account dialog fits the viewport and keeps actions reachable', async ({page}) => {
  await page.setViewportSize({width:390,height:844});
  const state = await setup(page); state.history=[application()];
  const dialog = await open(page);
  await page.screenshot({path:test.info().outputPath('account-governance-mobile.png'),fullPage:true});
  expect((await dialog.boundingBox())!.width).toBeLessThanOrEqual(390);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await dialog.getByRole('button',{name:'批准申请',exact:true}).click();
  await expect(dialog.getByLabel('审批原因',{exact:true})).toBeVisible();
  await dialog.getByRole('button',{name:'关闭账号管理',exact:true}).click();
  await expect(dialog).not.toBeVisible();
});
