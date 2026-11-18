import { test, expect, type Page } from '@playwright/test';
import { dataset, entry, identity, label, sample } from './evaluation-fixtures.mjs';

async function setup(page: Page, permissions = identity.permissions) {
  const state = { identity:{...identity,permissions}, item:dataset(), writes:[] as any[], queries:[] as string[],
    status:201, failOnce:false, listNext:null as number | null, detailNext:null as number | null };
  await page.route('**/api/v1/admin/**', async route => {
    const request=route.request(), url=new URL(request.url()), path=url.pathname;
    let data: unknown = {revoked:true};
    if (path.endsWith('/auth/captcha')) data = {captcha_id:'11111111-1111-1111-1111-111111111111',image:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='};
    if (path.endsWith('/auth/login')) data={access_token:'test-only-memory-token',audience:'platform'};
    else if (path.endsWith('/auth/me')) data=state.identity;
    else if (path.includes('/evaluation-datasets')) {
      state.queries.push(`${request.method()} ${url.pathname}${url.search}`);
      if (request.method()==='POST') {
        const body=request.postDataJSON(); state.writes.push({path,body});
        if (state.failOnce) { state.failOnce=false; await route.abort('failed'); return; }
        if (state.status!==201) {
          await route.fulfill({status:state.status,json:{error:{code:state.status===409?'conflict':'forbidden'},request_id:'safe-request'}}); return;
        }
        state.item={...state.item,revision:body.expected_revision+1};
        if (path.endsWith('/evaluation-datasets')) state.item=dataset({...body,revision:1,sample_count:0,samples:[],usable:false});
        else if (path.endsWith('/annotations')) state.item.samples=[sample({label_count:1,own_label:body.label})];
        else if (path.endsWith('/adjudications')) state.item.samples=[sample({state:'adjudicated',label_count:2})];
        else if (path.endsWith('/lock')) state.item={...state.item,state:'locked',locked_at:'2026-10-26T01:00:00Z',locked_digest:'c'.repeat(64),samples:[sample({state:'agreed',label_count:2,gold:label()})]};
        else state.item={...state.item,sample_count:1,samples:[sample()]};
        data=state.item;
      } else if (path.endsWith('/evaluation-datasets')) data={items:[entry(state.item)],offset:Number(url.searchParams.get('offset')),next_offset:Number(url.searchParams.get('offset'))===0?state.listNext:null};
      else data={...state.item,offset:Number(url.searchParams.get('offset')),next_offset:Number(url.searchParams.get('offset'))===0?state.detailNext:null};
    }
    await route.fulfill({status:request.method()==='POST' && path.includes('/evaluation-datasets')?201:200,json:{data}});
  });
  await page.goto('/');
  await page.getByLabel('平台邮箱').fill(identity.email);
  await page.getByLabel('登录密码',{exact:true}).fill('test-only-password');
  await page.getByLabel('验证码').fill('K7M2');
  await page.getByRole('button',{name:'登录工作台',exact:true}).click();
  await page.getByRole('button',{name:'评测数据集',exact:true}).click();
  await expect(page.getByRole('region',{name:'评测数据集列表'}).getByRole('button',{name:/三年级数学基准/})).toBeVisible();
  return state;
}
async function open(page: Page) {
  await page.getByRole('region',{name:'评测数据集列表'}).getByRole('button',{name:/三年级数学基准/}).click();
  await expect(page.getByRole('region',{name:'评测数据集详情'}).getByRole('heading',{name:'三年级数学基准'})).toBeVisible();
}
async function create(page: Page) {
  await page.getByRole('button',{name:'新建数据集',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'新建评测数据集'});
  await dialog.getByLabel('数据集名称',{exact:true}).fill('四年级数学基准');
  return dialog;
}

test('curator creates, intakes and locks with immutable operation identifiers', async ({page}) => {
  const state=await setup(page);
  let dialog=await create(page);
  await dialog.getByLabel('数据分组').selectOption('validation');
  await dialog.getByRole('button',{name:'提交保存'}).click();
  await expect(dialog).not.toBeVisible();
  expect(state.writes[0].body).toMatchObject({name:'四年级数学基准',split:'validation',expected_revision:0});
  await expect(page.getByText('存在失效授权或数据集暂不可用',{exact:false})).toHaveCount(0);
  await page.getByRole('button',{name:'录入授权样本',exact:true}).click();
  dialog=page.getByRole('dialog',{name:'录入授权样本'});
  for (const [name,value] of Object.entries({'源题编号':'33333333-3333-4333-8333-333333333333','授权编号':'44444444-4444-4444-8444-444444444444',
    '近重复题组':'math/addition/01','脱敏题干':'计算 2 + 3','脱敏学生答案':'5','脱敏处理说明':'已移除身份信息'})) await dialog.getByLabel(name,{exact:true}).fill(value);
  await dialog.getByRole('button',{name:'提交保存'}).click();
  await expect(dialog).not.toBeVisible();
  expect(state.writes[1].body).toMatchObject({expected_revision:1,source_revision:1,redacted_stem:'计算 2 + 3'});
  state.item=dataset({revision:4,samples:[sample({state:'agreed',label_count:2})],agreement:{double_labeled:1,agreed:1,disputed:0,adjudicated:0,rate:1}});
  await page.getByRole('button',{name:'刷新数据集列表'}).click();
  await open(page);
  await page.getByRole('button',{name:'检查并锁定'}).click();
  dialog=page.getByRole('dialog',{name:'锁定评测数据集'});
  await dialog.getByLabel('我已核对样本、双标与裁决，确认锁定').check();
  await dialog.getByRole('button',{name:'确认锁定'}).click();
  await expect(dialog).not.toBeVisible();
  await expect(page.getByText('已锁定标准答案',{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'录入授权样本',exact:true})).toHaveCount(0);
  expect(state.writes.at(-1).body.expected_revision).toBe(4);
  for (const {body} of state.writes) expect(body.operation_id).toMatch(/^[a-f0-9-]{36}$/);
});

test('annotator is blind to other labels and cannot curate', async ({page}) => {
  const state=await setup(page,['platform.evaluation.read','platform.evaluation.annotate']);
  state.item=dataset({samples:[sample({label_count:1})]});
  await open(page);
  await expect(page.getByRole('button',{name:'新建数据集'})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'录入授权样本',exact:true})).toHaveCount(0);
  await expect(page.getByRole('heading',{name:'独立标注 A'})).toHaveCount(0);
  await page.getByRole('button',{name:'提交独立标注'}).click();
  const dialog=page.getByRole('dialog',{name:'独立标注',exact:true});
  await dialog.getByLabel('标准答案',{exact:true}).fill('5');
  await dialog.getByLabel('已人工核对：题目和作答均完成脱敏').check();
  await dialog.getByRole('button',{name:'提交保存'}).click();
  await expect(dialog).not.toBeVisible();
  await expect(page.getByRole('heading',{name:'我的独立标注'})).toBeVisible();
  await expect(page.getByRole('button',{name:'提交独立标注'})).toHaveCount(0);
  expect(state.writes[0].path).toContain('/annotations');
  expect(state.writes[0].body.label).toMatchObject({correct_answer:'5',redaction_safe:true});
});

test('adjudicator can compare conflicting labels and submit a reason', async ({page}) => {
  const state=await setup(page,['platform.evaluation.read','platform.evaluation.adjudicate']);
  state.item=dataset({samples:[sample({state:'disputed',label_count:2,disagreement_labels:[label(),{...label(),correct_answer:'6',verdict:'incorrect'}]})],
    agreement:{double_labeled:1,agreed:0,disputed:1,adjudicated:0,rate:0}});
  await open(page);
  await expect(page.getByRole('heading',{name:'独立标注 A'})).toBeVisible();
  await expect(page.getByRole('heading',{name:'独立标注 B'})).toBeVisible();
  await page.getByRole('button',{name:'裁决此分歧'}).click();
  const dialog=page.getByRole('dialog',{name:'分歧裁决'});
  await dialog.getByLabel('标准答案',{exact:true}).fill('5');
  await dialog.getByLabel('裁决依据').fill('依据加法运算规则重新核算');
  await dialog.getByLabel('已人工核对：题目和作答均完成脱敏').check();
  await dialog.getByRole('button',{name:'提交保存'}).click();
  await expect(dialog).not.toBeVisible();
  expect(state.writes[0].body.reason).toBe('依据加法运算规则重新核算');
  await expect(page.getByRole('button',{name:'裁决此分歧'})).toHaveCount(0);
});

test('inactive authorization hides source and all annotation actions', async ({page}) => {
  const state=await setup(page);
  state.item=dataset({usable:false,samples:[sample({state:'authorization_inactive',redacted_stem:null,redacted_answer:null})]});
  await open(page);
  await expect(page.getByText('源题评测授权已撤回、过期或变更，此样本不可使用。')).toBeVisible();
  await expect(page.getByText('计算 2 + 3',{exact:true})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'提交独立标注'})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'检查并锁定'})).toBeDisabled();
});

test('uncertain save freezes fields and retries the exact original command', async ({page}) => {
  const state=await setup(page); state.failOnce=true;
  const dialog=await create(page);
  await dialog.getByRole('button',{name:'提交保存'}).click();
  await expect(dialog.getByRole('alert')).toContainText('保存结果尚未确认');
  await expect(dialog.getByLabel('数据集名称',{exact:true})).toBeDisabled();
  await dialog.getByRole('button',{name:'重试原请求'}).click();
  await expect(dialog).not.toBeVisible();
  expect(state.writes).toHaveLength(2);
  expect(state.writes[1]).toEqual(state.writes[0]);
  expect(await page.evaluate(()=>({local:{...localStorage},session:{...sessionStorage}}))).toEqual({local:{},session:{}});
});

test('revision conflict blocks resubmission and allows detail recheck', async ({page}) => {
  const state=await setup(page); state.status=409;
  const dialog=await create(page);
  await dialog.getByRole('button',{name:'提交保存'}).click();
  await expect(dialog.getByRole('alert')).toContainText('请关闭窗口并刷新详情');
  await expect(dialog.getByRole('button',{name:'提交保存'})).toBeDisabled();
  await expect(dialog.getByLabel('数据集名称',{exact:true})).toBeDisabled();
  await dialog.getByRole('button',{name:'关闭并核对详情'}).click();
  await expect(dialog).not.toBeVisible();
  await open(page);
  expect(state.writes).toHaveLength(1);
});

test('sample and dataset pagers use fifty-row offsets in both directions', async ({page}) => {
  const state=await setup(page); state.detailNext=50; state.listNext=50;
  await open(page);
  const region=page.getByRole('region',{name:'评测数据集详情'});
  await region.getByRole('button',{name:'下一页样本'}).click();
  await expect(region.getByText('第 2 页',{exact:true})).toBeVisible();
  expect(state.queries.at(-1)).toContain('offset=50');
  await region.getByRole('button',{name:'上一页样本'}).click();
  await expect(region.getByText('第 1 页',{exact:true})).toBeVisible();
  expect(state.queries.at(-1)).toContain('offset=0');
  await page.getByRole('button',{name:'刷新数据集列表'}).click();
  await page.getByRole('button',{name:'下一页数据集'}).click();
  await expect(page.getByRole('region',{name:'评测数据集列表'}).getByText('第 2 页',{exact:true})).toBeVisible();
  expect(state.queries.at(-1)).toContain('offset=50');
  await page.getByRole('button',{name:'上一页数据集'}).click();
  await expect(page.getByRole('region',{name:'评测数据集列表'}).getByText('第 1 页',{exact:true})).toBeVisible();
  expect(state.queries.at(-1)).toContain('offset=0');
});

test('narrow screen and keyboard preserve usable labels without page overflow', async ({page}) => {
  await page.setViewportSize({width:390,height:844});
  await setup(page);
  await open(page);
  const dialog=await create(page);
  const input=dialog.getByLabel('数据集名称',{exact:true});
  await input.focus(); await page.keyboard.press('Tab');
  await expect(dialog.getByLabel('数据分组')).toBeFocused();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
});

for (const status of [401,403]) {
  test(`status ${status} clears sensitive detail and invalidates the session or permissions`, async ({page}) => {
    const state=await setup(page);
    await open(page);
    const dialog=await create(page);
    state.status=status;
    if (status===403) state.identity={...state.identity,permissions:[]};
    await dialog.getByRole('button',{name:'提交保存'}).click();
    await expect(dialog).not.toBeVisible();
    await expect(page.getByText('计算 2 + 3',{exact:true})).toHaveCount(0);
    if (status===401) await expect(page.getByRole('button',{name:'登录工作台',exact:true})).toBeVisible();
    else await expect(page.getByRole('button',{name:'评测数据集',exact:true})).toHaveCount(0);
  });
}

test('late detail response cannot restore source after leaving evaluation', async ({page}) => {
  await setup(page);
  let release!: () => void;
  const gate=new Promise<void>(resolve=>{release=resolve;});
  await page.route('**/api/v1/admin/evaluation-datasets/*', async route=>{
    await gate;
    await route.fulfill({json:{data:dataset()}});
  });
  await page.getByRole('region',{name:'评测数据集列表'}).getByRole('button',{name:/三年级数学基准/}).click();
  await expect(page.getByText('正在核对样本授权与标注状态…')).toBeVisible();
  await page.getByRole('button',{name:'账号与会话',exact:true}).click();
  release();
  await expect(page.getByText('当前平台账号',{exact:true})).toBeVisible();
  await expect(page.getByText('计算 2 + 3',{exact:true})).toHaveCount(0);
});
