import { test, expect, type Page } from '@playwright/test';
const admin = {id:'admin',email:'security@example.test',role:'security_admin',audience:'platform',
  permissions:['platform.models.read','platform.models.write'],session_id:'session',expires_at:'2099-01-01T00:00:00Z'};
const ANSWER = '批改这几张图片的作业，按以下格式指出错误题，以及分析错误原因：\n第X张的第X题错误，分析错误原因\n\n最后汇总给出正确答案，格式：\n第X张的第X题错误，正确答案为';
const answer = {id:'22222222-2222-4222-8222-222222222222',name:'错题原因与正确答案',content:ANSWER,priority:10,enabled:true,
  created_at:'2026-11-14T00:00:00Z',updated_at:'2026-11-14T00:00:00Z'};
const hint = {id:'33333333-3333-4333-8333-333333333333',name:'订正提示',content:'先指出错误题，再给订正提示，不要直接写最终答案。',priority:40,enabled:true,
  created_at:'2026-11-14T00:00:01Z',updated_at:'2026-11-14T00:00:01Z'};
async function setup(page: Page, readonly = false) {
  const state = {identity:{...admin,permissions:readonly ? ['platform.models.read'] : [...admin.permissions]},
    items:[answer,hint],writes:[] as {path:string;body:Record<string,unknown>}[]};
  await page.route('**/api/v1/admin/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    let data: unknown = {revoked:true}, status = 200;
    if (path.endsWith('/auth/captcha')) data = {captcha_id:'11111111-1111-1111-1111-111111111111',image:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='};
    else if (path.endsWith('/auth/login')) data = {access_token:'test-only-memory-token',audience:'platform'};
    else if (path.endsWith('/auth/me')) data = state.identity;
    else if (path.endsWith('/grading-prompts') && request.method() === 'GET') {
      data = {items:[...state.items].sort((a,b) => a.priority-b.priority)};
    } else if (path.includes('/grading-prompts') && request.method() === 'POST') {
      const body = request.postDataJSON() ?? {};
      state.writes.push({path,body});
      if (path.endsWith('/delete')) {
        state.items = state.items.filter(item => !path.includes(item.id)); data = {id:answer.id};
      } else if (path.endsWith('/grading-prompts')) {
        const item = {...answer,...body,id:'44444444-4444-4444-8444-444444444444',created_at:answer.created_at,updated_at:'2026-11-14T01:00:00Z'};
        state.items.push(item); data = item; status = 201;
      } else {
        state.items = state.items.map(item => path.endsWith(item.id) ? {...item,...body,updated_at:'2026-11-14T01:00:00Z'} : item);
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
  await page.getByRole('button',{name:'提示词管理',exact:true}).click();
  await expect(page.getByRole('heading',{name:'提示词管理',exact:true})).toBeAttached();
  return state;
}

test('lists seeded grading prompts with priority and the preferred call', async ({page}) => {
  await setup(page);
  const row = page.getByRole('row',{name:/错题原因与正确答案/});
  await expect(row).toContainText('批改这几张图片的作业');
  await expect(row).toContainText('10');
  await expect(row).toContainText('优先调用');
  await expect(page.getByRole('row',{name:/订正提示/})).toBeVisible();
  await page.screenshot({path:test.info().outputPath('prompt-config.png'),fullPage:true,animations:'disabled'});
});

test('create, edit and disable keep priority order', async ({page}) => {
  const state = await setup(page);
  await page.getByRole('button',{name:'新增',exact:true}).click();
  const dialog = page.getByRole('dialog',{name:'新增提示词'});
  await dialog.getByRole('button',{name:'保存',exact:true}).click();
  await expect(dialog.getByRole('alert')).toContainText('请填写名称');
  await dialog.getByLabel('名称').fill('步骤核对');
  await dialog.getByLabel('提示词').fill('按页核对计算步骤。');
  await dialog.getByLabel('调用优先级').fill('5');
  await dialog.getByRole('button',{name:'保存',exact:true}).click();
  await expect(dialog).toBeHidden();
  expect(state.writes.at(-1)?.body).toMatchObject({name:'步骤核对',content:'按页核对计算步骤。',priority:5,enabled:true});
  const rows = page.locator('.erp-table .el-table__body tr');
  await expect(rows.first()).toContainText('步骤核对');
  await expect(rows.first()).toContainText('优先调用');

  const answerRow = page.getByRole('row',{name:/错题原因与正确答案/});
  await answerRow.locator('.el-switch').click();
  await expect(answerRow).not.toContainText('优先调用');
  expect(state.writes.at(-1)?.body).toMatchObject({name:'错题原因与正确答案',enabled:false,priority:10});

  await answerRow.getByRole('button',{name:'编辑',exact:true}).click();
  const edit = page.getByRole('dialog',{name:'编辑提示词'});
  await expect(edit.getByLabel('提示词')).toHaveValue(ANSWER);
  await edit.getByLabel('调用优先级').fill('15');
  await edit.getByRole('button',{name:'保存',exact:true}).click();
  await expect(edit).toBeHidden();
  expect(state.writes.at(-1)?.body).toMatchObject({priority:15,enabled:false});
});

test('delete asks for confirmation', async ({page}) => {
  const state = await setup(page);
  await page.getByRole('row',{name:/订正提示/}).getByRole('button',{name:'删除',exact:true}).click();
  await page.getByRole('button',{name:'取消',exact:true}).click();
  expect(state.writes).toHaveLength(0);
  await page.getByRole('row',{name:/订正提示/}).getByRole('button',{name:'删除',exact:true}).click();
  await page.locator('.el-message-box').getByRole('button',{name:'删除',exact:true}).click();
  await expect(page.getByRole('row',{name:/订正提示/})).toHaveCount(0);
  expect(state.writes.at(-1)?.path).toBe(`/api/v1/admin/grading-prompts/${hint.id}/delete`);
});

test('read-only operator cannot change prompts', async ({page}) => {
  await setup(page,true);
  await expect(page.getByRole('row',{name:/错题原因与正确答案/})).toContainText('启用');
  await expect(page.getByRole('button',{name:/^(新增|编辑|删除)$/})).toHaveCount(0);
  await expect(page.getByRole('switch')).toHaveCount(0);
});
