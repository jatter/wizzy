import { test, expect, type Page } from '@playwright/test';

const admin={id:'70000000-0000-4000-8000-000000000003',email:'security@example.test',role:'security_admin',
  audience:'platform',permissions:['platform.textbooks.read','platform.textbooks.write','platform.textbooks.review'],
  session_id:'session',expires_at:'2099-01-01T00:00:00Z'};
const entry={id:'70000000-0000-4000-8000-000000000001',code:'MATH_G3_2026_A',title:'数学',stage:'primary',grade:3,
  subject:'数学',publisher:'示例出版社',edition_label:'2026修订版',academic_year:'2026-2027',volume:'first',
  rights:{basis:'publisher_contract',holder:'示例出版社',reference:'CONTRACT-001',scopes:['storage','ocr'],regions:['CN'],
    valid_from:'2020-01-01',valid_until:'2099-12-31'},revision:1,revision_id:'70000000-0000-4000-8000-000000000002',
  state:'draft',action:'create',creator_id:admin.id,editor_id:admin.id,submitted_by:null as string|null,actor_id:admin.id,
  reason:'登记教材权利',created_at:'2026-10-26T00:00:00Z',registered_at:'2026-10-26T00:00:00Z',
  availability:'metadata_only',content_available:false};
async function setup(page:Page,readonly=false) {
  const state={identity:{...admin,permissions:readonly?['platform.textbooks.read']:[...admin.permissions]},
    item:structuredClone(entry),history:[structuredClone(entry)],writes:[] as Record<string,any>[],queries:[] as string[],
    writeStatus:201,code:'textbook_revision_conflict',failOnce:false,commits:0,listStatus:200,
    historyPage:false,pageErrors:[] as string[]};
  const operations=new Map<string,typeof entry>();
  page.on('pageerror',error=>state.pageErrors.push(error.name));
  await page.route('**/api/v1/admin/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    let data:unknown={revoked:true},status=200;
    if(path.endsWith('/auth/captcha')) data={captcha_id:'11111111-1111-1111-1111-111111111111',image:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='};
    if(path.endsWith('/auth/login')) data={access_token:'test-only-memory-token',audience:'platform'};
    else if(path.endsWith('/auth/me')) data=state.identity;
    else if(path.endsWith('/textbook-editions') && request.method()==='GET') {
      state.queries.push(url.search);
      if(state.listStatus!==200) {await route.fulfill({status:state.listStatus,json:{error:{code:'denied'}}});return;}
      const empty=url.searchParams.has('cursor')||url.searchParams.get('q')==='不存在';
      data={items:empty?[]:[state.item],next_cursor:empty?null:'page+/='};
    } else if(path.endsWith('/documents')) data={items:[],next_cursor:null};
    else if(path.includes('/textbook-editions') && request.method()==='POST') {
      const body=request.postDataJSON();state.writes.push(body);
      if(state.writeStatus!==201) {await route.fulfill({status:state.writeStatus,json:{error:{code:state.code}}});return;}
      let saved=operations.get(body.operation_id);const replay=!!saved;
      if(!saved) {
        const action=path.endsWith('/revisions')?'revise':path.endsWith('/textbook-editions')?'create':path.split('/').at(-1)!;
        const {operation_id,expected_revision,...fields}=body;
        const nextState=({create:'draft',revise:'draft',submit:'pending_review',approve:'approved',reject:'draft',withdraw:'withdrawn'} as Record<string,string>)[action];
        state.item={...state.item,...fields,action,state:nextState,revision:expected_revision?expected_revision+1:1,
          revision_id:crypto.randomUUID(),actor_id:state.identity.id,
          editor_id:['create','revise'].includes(action)?state.identity.id:state.item.editor_id,
          submitted_by:action==='submit'?state.identity.id:['create','revise'].includes(action)?null:state.item.submitted_by,
          availability:({draft:'metadata_only',pending_review:'pending_review',approved:'rights_approved',withdrawn:'withdrawn'} as Record<string,string>)[nextState]};
        saved=structuredClone(state.item);operations.set(body.operation_id,saved);state.history.unshift(saved);state.commits++;
      }
      if(state.failOnce){state.failOnce=false;await route.abort('failed');return;}
      data=saved;status=replay?200:201;
    } else if(path.endsWith('/revisions')) data={items:url.searchParams.has('cursor')?state.history.slice(1):state.historyPage?state.history.slice(0,1):state.history,
      next_cursor:state.historyPage&&!url.searchParams.has('cursor')?'history+/=':null};
    else if(path.endsWith(`/textbook-editions/${entry.id}`)) data=state.item;
    await route.fulfill({status,json:{data}});
  });
  await page.goto('/');
  await page.getByLabel('平台邮箱').fill(admin.email);
  await page.getByLabel('登录密码',{exact:true}).fill('test-only-password');
  await page.getByLabel('验证码').fill('K7M2');
  await page.getByRole('button',{name:'登录工作台',exact:true}).click();
  await page.getByRole('button',{name:'教材管理',exact:true}).click();
  await expect(page.getByRole('heading',{name:'教材与权利登记',exact:true})).toBeVisible();
  await expect(page.locator('.school-list')).toHaveAttribute('aria-busy','false');
  return state;
}
async function detail(page:Page) {
  await page.getByRole('button',{name:`查看教材 ${entry.code}`,exact:true}).click();
  const section=page.getByRole('region',{name:'教材详情',exact:true});
  await expect(section.getByRole('heading',{name:'修订与审核历史',exact:true})).toBeVisible();return section;
}
async function edit(page:Page) {
  await detail(page);await page.getByRole('button',{name:'修订教材 / 权利',exact:true}).click();
  return page.getByRole('dialog',{name:'修订教材',exact:true});
}
async function registration(page:Page) {
  await page.getByRole('button',{name:'登记教材',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'登记教材',exact:true});
  for(const [label,value] of Object.entries({'教材代码':'math_g4_2026','教材名称':'四年级数学','年级':'4','科目':'数学','出版社':'示例出版社','版本标识':'2026版','学年':'2026-2027','操作原因':'教材版本登记'}))
    await dialog.getByLabel(label,{exact:true}).fill(value);
  return dialog;
}
async function decision(page:Page,name:string) {
  await page.getByRole('button',{name,exact:true}).click();
  const dialog=page.getByRole('dialog',{name,exact:true});
  await dialog.getByLabel('操作原因',{exact:true}).fill(`测试${name}`);
  await dialog.getByRole('button',{name,exact:true}).click();await expect(dialog).not.toBeVisible();
}

test('textbook filters and cursor pages retain query and encode cursors',async({page})=>{
  const state=await setup(page),list=page.getByRole('region',{name:'教材目录',exact:true});
  await page.getByLabel('搜索教材').fill('数学 & 出版');await page.getByLabel('审核状态',{exact:true}).selectOption('draft');
  await list.getByRole('button',{name:'查询',exact:true}).click();
  await expect.poll(()=>state.queries.length).toBe(2);
  let params=new URLSearchParams(state.queries.at(-1));expect(params.get('q')).toBe('数学 & 出版');expect(params.get('state')).toBe('draft');
  await list.getByRole('button',{name:'下一页',exact:true}).click();await expect(list).toContainText('没有符合条件的教材');
  params=new URLSearchParams(state.queries.at(-1));expect(params.get('cursor')).toBe('page+/=');expect(params.get('q')).toBe('数学 & 出版');
  await list.getByRole('button',{name:'上一页',exact:true}).click();await expect(list.getByRole('article')).toContainText(entry.code);
  await list.getByRole('button',{name:'重置筛选',exact:true}).click();await expect(page.getByLabel('搜索教材')).toHaveValue('');
});

test('metadata draft can be registered without rights and cannot be submitted',async({page})=>{
  const state=await setup(page),dialog=await registration(page);
  await dialog.getByRole('button',{name:'保存草稿',exact:true}).click();await expect(dialog).not.toBeVisible();
  expect(state.writes[0]).toMatchObject({code:'MATH_G4_2026',grade:4,rights:null});
  expect(state.writes[0].operation_id).toMatch(/^[0-9a-f-]{36}$/);
  const section=page.getByRole('region',{name:'教材详情',exact:true});
  await expect(section).toContainText('尚未登记权利');await expect(section.getByRole('button',{name:'提交审核',exact:true})).toBeDisabled();
});

test('rights form validates year, grade, distinct regions, storage and dates before writing',async({page})=>{
  const state=await setup(page),dialog=await registration(page),save=dialog.getByRole('button',{name:'保存草稿',exact:true});
  await dialog.getByLabel('学年',{exact:true}).fill('2026-2028');await save.click();await expect(dialog.getByRole('alert')).toContainText('相邻年份');
  await dialog.getByLabel('学年',{exact:true}).fill('2026-2027');await dialog.getByLabel('学段',{exact:true}).selectOption('junior');
  await save.click();await expect(dialog.getByRole('alert')).toContainText('年级与学段');await dialog.getByLabel('年级',{exact:true}).fill('7');
  await dialog.getByLabel('已具备权利依据',{exact:true}).check();await save.click();await expect(dialog.getByRole('alert')).toContainText('权利人');
  await dialog.getByLabel('权利人',{exact:true}).fill('示例出版社');await dialog.getByLabel('权利凭据引用',{exact:true}).fill('CONTRACT-002');
  await dialog.getByLabel('授权地区代码',{exact:true}).fill('CN, CN');await save.click();await expect(dialog.getByRole('alert')).toContainText('不重复');
  await dialog.getByLabel('授权地区代码',{exact:true}).fill('CN, CN-33');await dialog.getByLabel('文件存储',{exact:true}).uncheck();
  await save.click();await expect(dialog.getByRole('alert')).toContainText('必须包含文件存储');await dialog.getByLabel('文件存储',{exact:true}).check();
  await dialog.getByLabel('授权起始日期',{exact:true}).fill('2099-01-01');await dialog.getByLabel('授权截止日期',{exact:true}).fill('2020-01-01');
  await save.click();await expect(dialog.getByRole('alert')).toContainText('起始日期不能晚于');expect(state.writes).toHaveLength(0);
  await dialog.getByLabel('授权起始日期',{exact:true}).fill('2020-01-01');await dialog.getByLabel('授权截止日期',{exact:true}).fill('2099-12-31');
  await dialog.getByLabel('OCR 解析',{exact:true}).check();await save.click();await expect(dialog).not.toBeVisible();
  expect(state.writes[0].rights).toMatchObject({regions:['CN','CN-33'],scopes:['storage','ocr'],reference:'CONTRACT-002'});
});

test('submit prohibits self review, withdrawal allows revision and history snapshots paginate',async({page})=>{
  const state=await setup(page);await detail(page);await decision(page,'提交审核');
  const section=page.getByRole('region',{name:'教材详情',exact:true});
  await expect(section.getByRole('button',{name:'批准权利',exact:true})).toBeDisabled();
  await expect(section.getByRole('button',{name:'驳回申请',exact:true})).toBeDisabled();
  await expect(section).toContainText('请由另一位有审核权限的人员处理');await decision(page,'撤回版本');
  await expect(section.getByRole('button',{name:'修订教材 / 权利',exact:true})).toBeVisible();
  state.historyPage=true;await section.getByRole('button',{name:'刷新详情',exact:true}).click();
  await expect(section.locator('.school-history li')).toHaveCount(1);await section.getByRole('button',{name:'加载更早记录',exact:true}).click();
  await expect(section.locator('.school-history li')).toHaveCount(3);
  await section.locator('summary').first().click();await expect(section.locator('.textbook-snapshot').first()).toContainText('CONTRACT-001');
  expect(state.writes.map(v=>v.expected_revision)).toEqual([1,2]);
});

for(const action of ['批准权利','驳回申请']) test(`independent reviewer can ${action} and content stays unavailable`,async({page})=>{
  const state=await setup(page);state.item={...state.item,state:'pending_review',action:'submit',availability:'pending_review',submitted_by:admin.id,
    creator_id:'70000000-0000-4000-8000-000000000004',editor_id:'70000000-0000-4000-8000-000000000004'};
  state.item.submitted_by=state.item.creator_id;
  const section=await detail(page);await decision(page,action);
  await expect(section).toContainText(action==='批准权利'?'修改已批准档案需先撤回':'草稿');
  await expect(page.getByRole('region',{name:'教材目录',exact:true})).toContainText('正文不可用');
  if(action==='批准权利') await expect(section.getByRole('button',{name:'修订教材 / 权利',exact:true})).toHaveCount(0);
  expect(state.commits).toBe(1);
});

test('lost committed response retries identical UUID and body without another revision',async({page})=>{
  const state=await setup(page),dialog=await edit(page);state.failOnce=true;
  await dialog.getByLabel('操作原因',{exact:true}).fill('修订回执丢失');await dialog.getByLabel('教材名称',{exact:true}).fill('数学修订');
  await dialog.getByRole('button',{name:'保存草稿',exact:true}).click();await expect(dialog.getByRole('alert')).toContainText('无法连接');
  await expect(dialog.getByLabel('教材名称',{exact:true})).toBeDisabled();await dialog.getByRole('button',{name:'重试原请求',exact:true}).click();
  await expect(dialog).not.toBeVisible();expect(state.writes).toHaveLength(2);expect(state.writes[1]).toEqual(state.writes[0]);expect(state.commits).toBe(1);
  await expect(page.getByRole('region',{name:'教材详情',exact:true}).locator('.school-history li')).toHaveCount(2);
});

test('CAS conflict reloads current metadata and uses fresh revision and operation UUID',async({page})=>{
  const state=await setup(page),dialog=await edit(page);state.writeStatus=409;
  await dialog.getByLabel('操作原因',{exact:true}).fill('原始草稿');await dialog.getByRole('button',{name:'保存草稿',exact:true}).click();
  await expect(dialog.getByRole('alert')).toContainText('教材已被修改');state.item={...state.item,title:'最新数学',revision:2};
  await dialog.getByRole('button',{name:'载入最新版本（替换草稿或返回详情）',exact:true}).click();
  await expect(dialog.getByLabel('教材名称',{exact:true})).toHaveValue('最新数学');await expect(dialog.getByLabel('操作原因',{exact:true})).toHaveValue('');
  state.writeStatus=201;await dialog.getByLabel('操作原因',{exact:true}).fill('基于最新版本修订');await dialog.getByRole('button',{name:'保存草稿',exact:true}).click();
  await expect(dialog).not.toBeVisible();expect(state.writes[1].expected_revision).toBe(2);expect(state.writes[1].operation_id).not.toBe(state.writes[0].operation_id);
});

test('code conflict blocks another registration until directory is checked',async({page})=>{
  const state=await setup(page),dialog=await registration(page);state.writeStatus=409;state.code='textbook_code_conflict';
  await dialog.getByRole('button',{name:'保存草稿',exact:true}).click();await expect(dialog.getByRole('alert')).toContainText('教材代码已存在');
  await expect(dialog.getByRole('button',{name:'保存草稿',exact:true})).toBeDisabled();expect(state.writes).toHaveLength(1);
});

test('permission revocation drops editable draft while retaining read access',async({page})=>{
  const state=await setup(page),dialog=await edit(page);state.identity.permissions=['platform.textbooks.read'];state.writeStatus=403;
  await dialog.getByLabel('操作原因',{exact:true}).fill('权限已撤销');await dialog.getByRole('button',{name:'保存草稿',exact:true}).click();
  await expect(dialog).not.toBeVisible();await expect(page.getByRole('button',{name:'登记教材',exact:true})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'修订教材 / 权利',exact:true})).toHaveCount(0);
});

test('expired write session returns to login without retaining form',async({page})=>{
  const state=await setup(page),dialog=await edit(page);state.writeStatus=401;
  await dialog.getByLabel('操作原因',{exact:true}).fill('会话失效');await dialog.getByRole('button',{name:'保存草稿',exact:true}).click();
  await expect(page.getByRole('heading',{name:'登录平台工作台',exact:true})).toBeVisible();await expect(dialog).toHaveCount(0);expect(state.pageErrors).toEqual([]);
});

test('readonly operator can retry listing and inspect rights without write controls',async({page})=>{
  const state=await setup(page,true),section=await detail(page);
  await expect(page.getByRole('button',{name:'登记教材',exact:true})).toHaveCount(0);await expect(section.getByRole('button',{name:'修订教材 / 权利',exact:true})).toHaveCount(0);
  state.listStatus=503;await page.getByRole('button',{name:'查询',exact:true}).click();await expect(page.getByRole('alert')).toContainText('服务暂时不可用');
  state.listStatus=200;await page.getByRole('button',{name:'重试查询',exact:true}).click();await expect(page.getByRole('article')).toContainText(entry.code);expect(state.writes).toHaveLength(0);
});

test('read revocation on focus removes textbook navigation and details',async({page})=>{
  const state=await setup(page);await detail(page);state.identity.permissions=[];await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await expect(page.getByRole('button',{name:'教材管理',exact:true})).toHaveCount(0);await expect(page.getByRole('region',{name:'教材详情',exact:true})).toHaveCount(0);
});

test('textbook metadata and rights dialog fit mobile width',async({page})=>{
  await page.setViewportSize({width:390,height:844});const state=await setup(page);await detail(page);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  const dialog=await registration(page);await dialog.getByLabel('已具备权利依据',{exact:true}).check();
  await expect(dialog.getByLabel('权利来源',{exact:true})).toBeVisible();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:'/tmp/wizzy-textbooks-mobile.png',fullPage:true});expect(state.pageErrors).toEqual([]);
});
