import { test, expect, type Page } from '@playwright/test';
const admin = {id:'admin',email:'security@example.test',role:'security_admin',audience:'platform',
  permissions:['platform.models.read','platform.models.write'],session_id:'session',expires_at:'2099-01-01T00:00:00Z'};
const ID = '22222222-2222-4222-8222-222222222222';
const entry = {id:ID,name:'qwen-vl-max',base_url:'https://dashscope.aliyuncs.com/compatible-mode/v1',priority:10,
  api_key_configured:true,api_key_hint:'****e5f6',last_test_ok:null,last_test_at:null,last_test_message:null,
  created_at:'2026-11-13T00:00:00Z',updated_at:'2026-11-13T00:00:00Z'};
async function setup(page: Page, readonly = false) {
  const state = {identity:{...admin,permissions:readonly ? ['platform.models.read'] : [...admin.permissions]},
    items:[{...entry}],writes:[] as {path:string;body:Record<string,unknown>}[]};
  await page.route('**/api/v1/admin/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    let data: unknown = {revoked:true}, status = 200;
    if (path.endsWith('/auth/captcha')) data = {captcha_id:'11111111-1111-1111-1111-111111111111',image:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='};
    else if (path.endsWith('/auth/login')) data = {access_token:'test-only-memory-token',audience:'platform'};
    else if (path.endsWith('/auth/me')) data = state.identity;
    else if (path.endsWith('/model-configs') && request.method() === 'GET') data = {items:[...state.items].sort((a,b) => a.priority-b.priority)};
    else if (path.includes('/model-configs') && request.method() === 'POST') {
      const body = request.postDataJSON() ?? {};
      state.writes.push({path,body});
      if (path.endsWith('/test')) {
        const item = {...state.items[0],last_test_ok:true,last_test_at:'2026-11-13T01:00:00Z',last_test_message:'连接成功：HTTP 200，耗时 88 ms'};
        state.items[0] = item; data = {ok:true,message:item.last_test_message,elapsed_ms:88,config:item};
      } else if (path.endsWith('/delete')) {
        state.items = state.items.filter(item => !path.includes(item.id)); data = {id:ID};
      } else if (path.endsWith('/model-configs')) {
        const {api_key,...fields} = body;
        const item = {...entry,...fields,id:'33333333-3333-4333-8333-333333333333',api_key_configured:!!api_key,
          api_key_hint:api_key ? '****'+String(api_key).slice(-4) : null};
        state.items.push(item as typeof entry); data = item; status = 201;
      } else {
        const {api_key,...fields} = body;
        state.items = state.items.map(item => path.endsWith(item.id) ? {...item,...fields,
          ...(api_key ? {api_key_hint:'****'+String(api_key).slice(-4)} : {})} as typeof entry : item);
        data = state.items.find(item => path.endsWith(item.id));
      }
    }
    await route.fulfill({status,json:{data}});
  });
  await page.goto('/');
  await page.getByLabel('平台邮箱').fill(admin.email);
  await page.getByLabel('登录密码',{exact:true}).fill('test-only-password');
  await page.getByLabel('验证码').fill('K7M2');
  await page.getByRole('button',{name:'登录工作台',exact:true}).click();
  await page.getByRole('button',{name:'模型配置',exact:true}).click();
  await expect(page.getByRole('heading',{name:'模型配置',exact:true})).toBeAttached();
  return state;
}

test('model config lists name, endpoint, masked key and priority; tests connection', async ({page}) => {
  const state = await setup(page);
  const row = page.getByRole('row',{name:/qwen-vl-max/});
  await expect(row).toContainText('https://dashscope.aliyuncs.com/compatible-mode/v1');
  await expect(row).toContainText('****e5f6');
  await expect(row).toContainText('未测试');
  await page.screenshot({path:test.info().outputPath('model-config.png'),fullPage:true,animations:'disabled'});
  await row.getByRole('button',{name:'测试',exact:true}).click();
  await expect(page.getByText(/连接成功：HTTP 200/).first()).toBeVisible();
  await expect(row).toContainText('连接正常');
  expect(state.writes.at(-1)?.path).toBe(`/api/v1/admin/model-configs/${ID}/test`);
});

test('create and edit keep the key write-only; priority orders rows', async ({page}) => {
  const state = await setup(page);
  await page.getByRole('button',{name:'新增',exact:true}).click();
  const dialog = page.getByRole('dialog',{name:'新增模型'});
  await dialog.getByLabel('模型名').fill('deepseek-chat');
  await dialog.getByLabel('调用地址').fill('ftp://bad');
  await dialog.getByRole('button',{name:'保存',exact:true}).click();
  await expect(dialog.getByRole('alert')).toContainText('http://');
  await dialog.getByLabel('调用地址').fill('https://api.deepseek.com/v1');
  await dialog.getByLabel('密钥',{exact:true}).fill('sk-secret-123456');
  await dialog.getByLabel('调用优先级').fill('5');
  await dialog.getByRole('button',{name:'保存',exact:true}).click();
  await expect(dialog).toBeHidden();
  expect(state.writes.at(-1)?.body).toEqual({name:'deepseek-chat',base_url:'https://api.deepseek.com/v1',api_key:'sk-secret-123456',priority:5});
  const rows = page.locator('.erp-table .el-table__body tr');
  await expect(rows.first()).toContainText('deepseek-chat');
  await expect(page.getByText('sk-secret-123456')).toHaveCount(0);

  await page.getByRole('row',{name:/qwen-vl-max/}).getByRole('button',{name:'编辑',exact:true}).click();
  const edit = page.getByRole('dialog',{name:'编辑模型'});
  await expect(edit.getByLabel('密钥',{exact:true})).toHaveValue('');
  await expect(edit.getByLabel('密钥',{exact:true})).toHaveAttribute('placeholder',/留空则不修改/);
  await edit.getByLabel('调用优先级').fill('1');
  await edit.getByRole('button',{name:'保存',exact:true}).click();
  await expect(edit).toBeHidden();
  expect(state.writes.at(-1)?.body).toEqual({name:'qwen-vl-max',base_url:entry.base_url,api_key:null,priority:1});
  await expect(rows.first()).toContainText('qwen-vl-max');
});

test('delete asks for confirmation', async ({page}) => {
  const state = await setup(page);
  await page.getByRole('row',{name:/qwen-vl-max/}).getByRole('button',{name:'删除',exact:true}).click();
  await page.getByRole('button',{name:'取消',exact:true}).click();
  expect(state.writes).toHaveLength(0);
  await page.getByRole('row',{name:/qwen-vl-max/}).getByRole('button',{name:'删除',exact:true}).click();
  await page.locator('.el-message-box').getByRole('button',{name:'删除',exact:true}).click();
  await expect(page.getByRole('row',{name:/qwen-vl-max/})).toHaveCount(0);
  expect(state.writes.at(-1)?.path).toBe(`/api/v1/admin/model-configs/${ID}/delete`);
});

test('read-only operator sees no write or test controls; removed modules are gone', async ({page}) => {
  await setup(page,true);
  await expect(page.getByRole('row',{name:/qwen-vl-max/})).toBeVisible();
  await expect(page.getByRole('button',{name:/^(新增|测试|编辑|删除)$/})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'学校管理',exact:true})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'模型评测',exact:true})).toHaveCount(0);
});
