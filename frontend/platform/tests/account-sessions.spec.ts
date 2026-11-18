import { test, expect, type Page } from '@playwright/test';
import type { Identity, SessionRevocation } from '../src/api';

const actor='10000000-0000-4000-8000-000000000001';
const target={id:'20000000-0000-4000-8000-000000000001',email:'operator@example.test',role:'operator',status:'active',revision:0,created_at:'2026-10-26T00:00:00Z'};
const permission='platform.accounts.sessions.revoke';
async function setup(page: Page, readonly=false, self=false) {
  const identity: Identity={id:self ? target.id : actor,email:'security@example.test',role:'security_admin',audience:'platform',
    permissions:['platform.accounts.read',...(!readonly ? [permission] : [])],session_id:'test-session',expires_at:'2099-01-01T00:00:00Z'};
  const state={identity,records:[] as SessionRevocation[],older:[] as SessionRevocation[],writes:[] as Record<string,unknown>[],
    commits:0,loseReceipt:false,status:200,code:'operation_conflict',meStatus:200,queries:[] as string[]};
  const operations=new Map<string,SessionRevocation>();
  await page.route('**/api/v1/admin/**',async route => {
    const req=route.request(),url=new URL(req.url()),path=url.pathname;
    let data: unknown={revoked:true},status=200;
    if (path.endsWith('/auth/captcha')) data={captcha_id:crypto.randomUUID(),image:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='};
    else if (path.endsWith('/auth/login')) data={access_token:'test-token',audience:'platform'};
    else if (path.endsWith('/auth/me')) {
      if (state.meStatus!==200) { await route.fulfill({status:state.meStatus,json:{error:{code:'unauthorized'}}}); return; }
      data=state.identity;
    } else if (path.endsWith('/accounts')) data={items:[target],next_cursor:null};
    else if (path.endsWith('/changes')) data={items:[],next_cursor:null};
    else if (path.endsWith('/session-revocations') && req.method()==='GET') {
      state.queries.push(url.search);
      data={items:url.searchParams.has('cursor') ? state.older : state.records,next_cursor:!url.searchParams.has('cursor') && state.older.length ? 'older+/=' : null};
    } else if (path.endsWith('/session-revocations')) {
      const body=req.postDataJSON(); state.writes.push(body);
      if (state.status!==200) { await route.fulfill({status:state.status,json:{error:{code:state.code}}}); return; }
      let result=operations.get(body.operation_id);
      if (!result) {
        result={...body,id:crypto.randomUUID(),account_id:target.id,actor_id:identity.id,revoked_count:2,created_at:'2026-10-26T01:00:00Z'};
        operations.set(body.operation_id,result!); state.records.unshift(result!); state.commits++; status=201;
      }
      data=result;
      if (state.loseReceipt) { state.loseReceipt=false; await route.abort('failed'); return; }
    } else if (path.endsWith(`/${target.id}`)) data=target;
    await route.fulfill({status,json:{data}});
  });
  await page.goto('/');
  await page.getByLabel('平台邮箱').fill(identity.email);
  await page.getByLabel('登录密码',{exact:true}).fill('test-password');
  await page.getByLabel('验证码').fill('K7M2');
  await page.getByRole('button',{name:'登录工作台',exact:true}).click();
  await page.getByRole('button',{name:'平台账号',exact:true}).click();
  await page.getByRole('button',{name:'管理与历史',exact:true}).click();
  const section=page.getByRole('region',{name:'账号会话安全',exact:true});
  await expect(section.getByText('暂无独立撤销记录')).toBeVisible();
  return {state,section};
}

test('revokes with required reason and displays immutable receipt without altering account',async ({page}) => {
  const {state,section}=await setup(page);
  await section.getByRole('button',{name:'撤销全部会话',exact:true}).click();
  await expect(section.getByRole('alert')).toBeVisible(); expect(state.writes).toHaveLength(0);
  await section.getByLabel('撤销原因').fill('  异常登录核查  ');
  await section.getByRole('button',{name:'撤销全部会话',exact:true}).click();
  await expect(section.getByRole('status')).toContainText('已撤销有效会话 2 个');
  expect(state.writes[0]).toMatchObject({reason:'异常登录核查'}); expect(state.commits).toBe(1);
  await expect(section.getByText(state.writes[0]!.operation_id as string,{exact:true})).toBeVisible();
  await expect(section.getByLabel('撤销原因')).toHaveValue('');
  await expect(page.getByRole('region',{name:'账号详情'})).toContainText('当前版本 0');
});

test('lost response locks draft and replays original operation exactly once',async ({page}) => {
  const {state,section}=await setup(page); state.loseReceipt=true;
  await section.getByLabel('撤销原因').fill('安全核查');
  await section.getByRole('button',{name:'撤销全部会话',exact:true}).click();
  await expect(section.getByLabel('撤销原因')).toBeDisabled();
  await expect(section.getByText('操作结果尚未确认',{exact:false})).toBeVisible();
  await section.getByRole('button',{name:'原样重试撤销',exact:true}).click();
  await expect(section.getByRole('status')).toContainText('撤销操作已完成');
  expect(state.writes).toHaveLength(2); expect(state.writes[0]).toEqual(state.writes[1]); expect(state.commits).toBe(1);
});

test('conflict requires history reconciliation and read only users have no action',async ({page}) => {
  const {state,section}=await setup(page); state.status=409;
  await section.getByLabel('撤销原因').fill('核查原因');
  await section.getByRole('button',{name:'撤销全部会话',exact:true}).click();
  await expect(section.getByRole('button',{name:'撤销全部会话',exact:true})).toBeDisabled();
  await expect(section.getByRole('button',{name:'核对历史并清除草稿'})).toBeVisible();
  state.status=200;
  await section.getByRole('button',{name:'核对历史并清除草稿'}).click();
  await expect(section.getByLabel('撤销原因')).toHaveValue('');
  await expect(section.getByRole('button',{name:'撤销全部会话',exact:true})).toBeEnabled();
});

for (const mode of ['readonly','self'] as const) test(`${mode} hides cross account revoke`,async ({page}) => {
  const {section}=await setup(page,mode==='readonly',mode==='self');
  await expect(section.getByRole('button',{name:'撤销全部会话',exact:true})).toHaveCount(0);
  await expect(section.getByText(mode==='self' ? '本人请使用平台右上角' : '当前账号仅可查看',{exact:false})).toBeVisible();
});

test('preflight permission loss prevents POST and clears reason',async ({page}) => {
  const {state,section}=await setup(page);
  await section.getByLabel('撤销原因').fill('敏感原因'); state.identity.permissions=['platform.accounts.read'];
  await section.getByRole('button',{name:'撤销全部会话',exact:true}).click();
  await expect(section.getByRole('button',{name:'撤销全部会话',exact:true})).toHaveCount(0);
  expect(state.writes).toHaveLength(0); await expect(section).not.toContainText('敏感原因');
});

test('390px layout and paginated history keep operation identifiers readable',async ({page}) => {
  await page.setViewportSize({width:390,height:844});
  const {state,section}=await setup(page);
  state.older=[{id:crypto.randomUUID(),account_id:target.id,actor_id:actor,operation_id:crypto.randomUUID(),reason:'较早撤销',revoked_count:0,created_at:'2026-10-25T00:00:00.12345Z'}];
  await section.getByRole('button',{name:'刷新撤销记录'}).click();
  await section.getByRole('button',{name:'加载更早撤销记录'}).click();
  await expect(section.getByText('较早撤销',{exact:true})).toBeVisible();
  expect(state.queries.at(-1)).toContain('cursor=older%2B%2F%3D');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
