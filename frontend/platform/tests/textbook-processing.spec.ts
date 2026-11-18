import { createHash } from 'node:crypto';
import { test, expect, type Page } from '@playwright/test';

const admin={id:'70000000-0000-4000-8000-000000000003',email:'security@example.test',role:'security_admin',
  audience:'platform',permissions:['platform.textbooks.read','platform.textbooks.write'],session_id:'session',expires_at:'2099-01-01T00:00:00Z'};
const edition={id:'70000000-0000-4000-8000-000000000001',code:'MATH_PDF',title:'数学',stage:'primary',grade:3,
  subject:'数学',publisher:'示例出版社',edition_label:'2026版',academic_year:'2026-2027',volume:'first',
  rights:{basis:'publisher_contract',holder:'示例出版社',reference:'CONTRACT-001',scopes:['storage','ocr','preview'],regions:['CN'],valid_from:'2020-01-01',valid_until:'2099-12-31'},
  revision:3,revision_id:'70000000-0000-4000-8000-000000000002',state:'approved',action:'approve',
  creator_id:admin.id,editor_id:admin.id,submitted_by:admin.id,actor_id:admin.id,reason:'独立审核通过',
  created_at:'2026-10-26T00:00:00Z',registered_at:'2026-10-26T00:00:00Z',availability:'rights_approved',content_available:false};
const bytes=Buffer.from('%PDF-1.7\nprototype upload test\n%%EOF');
const file={name:'数学.pdf',mimeType:'application/pdf',buffer:bytes};
const receipt={id:'80000000-0000-4000-8000-000000000001',edition_id:edition.id,edition_revision:3,filename:file.name,
  size_bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex'),status:'quarantined',scan_status:'not_scanned',content_available:false,
  created_by:admin.id,operation_id:'80000000-0000-4000-8000-000000000002',reason:'接收授权原件',
  created_at:new Date().toISOString(),expires_at:new Date(Date.now()+900000).toISOString(),received_at:'2026-10-26T01:00:00Z'};

const queued={id:'90000000-0000-4000-8000-000000000001',document_id:receipt.id,edition_id:edition.id,edition_revision:3,
  created_by:admin.id,reason:'扫描授权原件',state:'queued',attempts:0,created_at:'2026-10-26T01:00:00Z',finished_at:null,
  code:null,scan:null,page_count:null,character_count:null,pages_without_text:null,requires_review:true,ocr_performed:false,content_available:false};
const draft={...queued,state:'draft_ready',attempts:1,finished_at:'2026-10-26T01:01:00Z',page_count:2,character_count:16,pages_without_text:[2],
  scan:{engine:'ClamAV',database_revision:123,database_date:'2026-10-26',checked_at:'2026-10-26T01:00:01Z'}};
const panel=(page:Page)=>page.getByRole('region',{name:'数学.pdf 的处理任务',exact:true});
async function setup(page:Page,options:{readonly?:boolean;preview?:boolean;empty?:boolean}={}) {
  const state={jobs:options.empty?[]:[structuredClone(draft)] as Record<string,any>[],requests:[] as Record<string,any>[],
    lose:false,denied:false,invalid:false,cursor:null as string|null,cursors:[] as (string|null)[],errors:[] as string[],
    hold:false,release:null as null|(()=>void)};
  page.on('pageerror',error=>state.errors.push(error.message));
  await page.route('**/api/v1/admin/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    let data:unknown={revoked:true};
    if(path.endsWith('/auth/captcha')) data={captcha_id:'11111111-1111-1111-1111-111111111111',image:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='};
    if(path.endsWith('/auth/login')) data={access_token:'test-memory-token',audience:'platform'};
    else if(path.endsWith('/auth/me')) data={...admin,permissions:options.readonly?['platform.textbooks.read']:admin.permissions};
    else if(path.includes('/processing-jobs')) {
      if(state.denied){await route.fulfill({status:403,json:{error:{}}});return;}
      if(path.endsWith('/processing-jobs') && request.method()==='POST') {
        const payload=request.postDataJSON();state.requests.push(payload);
        state.jobs=[{...queued,reason:payload.reason}];
        if(state.lose){state.lose=false;await route.abort('failed');return;}
        data=state.jobs[0];
      } else if(path.includes('/pages/')) {
        if(state.hold) await new Promise<void>(resolve=>{state.release=resolve;});
        const number=Number(path.split('/').at(-1));
        data={job_id:draft.id,page_count:2,page:{number,text:number===1?'<script>window.bad=true</script>数学 😀':''},
          requires_review:true,ocr_performed:false,content_available:state.invalid};
      } else if(path.endsWith('/processing-jobs')) {
        state.cursors.push(url.searchParams.get('cursor'));
        data={items:state.jobs,next_cursor:url.searchParams.has('cursor')?null:state.cursor};
      } else data=state.jobs.find(job=>path.endsWith(job.id));
    } else if(path.endsWith('/documents')) data={items:[receipt],next_cursor:null};
    else if(path.endsWith('/textbook-editions')) data={items:[edition],next_cursor:null};
    else if(path.endsWith('/revisions')) data={items:[edition],next_cursor:null};
    else if(path.endsWith(`/textbook-editions/${edition.id}`)) data={...edition,rights:{...edition.rights,
      scopes:options.preview===false?['storage','ocr']:edition.rights.scopes}};
    await route.fulfill({json:{data}});
  });
  await page.goto('/');await page.getByLabel('平台邮箱').fill(admin.email);
  await page.getByLabel('登录密码',{exact:true}).fill('test-only');await page.getByLabel('验证码').fill('K7M2');
  await page.getByRole('button',{name:'登录工作台',exact:true}).click();
  await page.getByRole('button',{name:'教材管理',exact:true}).click();
  await page.getByRole('button',{name:`查看教材 ${edition.code}`,exact:true}).click();
  await panel(page).getByRole('button',{name:'查看处理任务',exact:true}).click();
  await expect.poll(()=>state.cursors.length).toBe(1);
  await expect(panel(page).getByRole('button',{name:'刷新任务',exact:true})).toBeEnabled();
  return state;
}
async function detail(page:Page) {
  await panel(page).getByRole('button',{name:'查看任务详情',exact:true}).first().click();
  await expect(panel(page).getByLabel('处理任务详情',{exact:true})).toBeVisible();
}
async function read(page:Page) {
  await detail(page);await panel(page).getByRole('button',{name:'读取第一页',exact:true}).click();
  await expect(panel(page).getByLabel('逐页文本草稿',{exact:true})).toBeVisible();
}

test('shows scan evidence and escaped page drafts with empty-page navigation',async({page})=>{
  const state=await setup(page);await read(page);
  await expect(panel(page)).toContainText('待校核草稿，尚未发布，不可供 AI 使用');
  await panel(page).getByText('查看本次扫描证据',{exact:true}).click();
  await expect(panel(page)).toContainText('ClamAV');await expect(panel(page)).toContainText('123');
  await expect(panel(page).locator('pre')).toHaveText('<script>window.bad=true</script>数学 😀');
  expect(await page.evaluate(()=>('bad' in window))).toBe(false);
  await panel(page).getByLabel('草稿页码',{exact:true}).selectOption('2');
  await expect(panel(page).getByLabel('逐页文本草稿')).toContainText('此页未提取到文本');
  expect(state.errors).toEqual([]);
});

test('lost application response reuses original payload and disables duplicate active requests',async({page})=>{
  const state=await setup(page,{empty:true});state.lose=true;
  await panel(page).getByLabel('处理原因',{exact:true}).fill('扫描授权原件');
  await panel(page).getByRole('button',{name:'申请扫描与文本提取',exact:true}).click();
  await expect(panel(page)).toContainText('申请结果未确认');
  await expect(panel(page).getByLabel('处理原因',{exact:true})).toBeDisabled();
  await panel(page).getByRole('button',{name:'重试原处理申请',exact:true}).click();
  await expect(panel(page)).toContainText('处理申请已登记');
  expect(state.requests).toHaveLength(2);expect(state.requests[1]).toEqual(state.requests[0]);
  expect(state.requests[0]).toMatchObject({expected_revision:3,reason:'扫描授权原件'});
  await expect(panel(page).getByRole('button',{name:'申请扫描与文本提取',exact:true})).toBeDisabled();
});

test('validates processing reason before sending a request',async({page})=>{
  const state=await setup(page,{empty:true});
  await panel(page).getByRole('button',{name:'申请扫描与文本提取',exact:true}).click();
  await expect(panel(page).getByRole('alert')).toContainText('请填写');expect(state.requests).toHaveLength(0);
});

test('history pagination carries cursor and removes duplicate jobs',async({page})=>{
  const state=await setup(page);state.cursor='older-page';
  await panel(page).getByRole('button',{name:'刷新任务',exact:true}).click();
  await panel(page).getByRole('button',{name:'加载更早任务',exact:true}).click();
  await expect(panel(page).getByRole('button',{name:'加载更早任务',exact:true})).toHaveCount(0);
  expect(state.cursors).toEqual([null,null,'older-page']);
  await expect(panel(page).getByRole('button',{name:'查看任务详情',exact:true})).toHaveCount(1);
});

test('forbidden refresh clears rendered draft and prevents further operations',async({page})=>{
  const state=await setup(page);await read(page);state.denied=true;
  await panel(page).getByRole('button',{name:'刷新任务',exact:true}).click();
  await expect(panel(page).getByRole('alert')).toContainText('已清除草稿');
  await expect(panel(page).getByLabel('逐页文本草稿')).toHaveCount(0);
  await expect(panel(page).getByRole('button',{name:'刷新任务',exact:true})).toBeDisabled();
  await expect(panel(page).getByLabel('处理原因',{exact:true})).toHaveCount(0);
});

test('requires preview authorization and write permission independently',async({page})=>{
  await setup(page,{readonly:true,preview:false});await detail(page);
  await expect(panel(page).getByLabel('处理原因',{exact:true})).toHaveCount(0);
  await expect(panel(page)).toContainText('预览需要当前教材版本匹配');
  await expect(panel(page).getByRole('button',{name:'读取第一页',exact:true})).toHaveCount(0);
});

test('rejects forged published-content responses and recovers by rereading',async({page})=>{
  const state=await setup(page);state.invalid=true;await detail(page);
  await panel(page).getByRole('button',{name:'读取第一页',exact:true}).click();
  await expect(panel(page).getByRole('alert')).toContainText('不符合约定');
  await expect(panel(page).getByLabel('逐页文本草稿')).toHaveCount(0);state.invalid=false;
  await panel(page).getByRole('button',{name:'读取第一页',exact:true}).click();
  await expect(panel(page).getByLabel('逐页文本草稿')).toBeVisible();
});

test('late page response cannot repopulate draft after task refresh',async({page})=>{
  const state=await setup(page);await detail(page);state.hold=true;
  await panel(page).getByRole('button',{name:'读取第一页',exact:true}).click();
  await expect.poll(()=>state.release!==null).toBe(true);
  await panel(page).getByRole('button',{name:'刷新任务',exact:true}).click();
  await expect(panel(page).getByRole('button',{name:'查看任务详情',exact:true})).toBeEnabled();
  state.release!();state.hold=false;await detail(page);
  await expect(panel(page).getByLabel('逐页文本草稿')).toHaveCount(0);
  await panel(page).getByRole('button',{name:'读取第一页',exact:true}).click();
  await expect(panel(page).getByLabel('逐页文本草稿')).toBeVisible();
});

test('mobile processing history and draft fit a 390px viewport',async({page})=>{
  await page.setViewportSize({width:390,height:844});await setup(page);await read(page);
  await panel(page).getByText('查看本次扫描证据',{exact:true}).click();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
