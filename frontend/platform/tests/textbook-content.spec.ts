import { createHash } from 'node:crypto';
import { test, expect, type Page } from '@playwright/test';

const admin={id:'70000000-0000-4000-8000-000000000003',email:'security@example.test',role:'security_admin',
  audience:'platform',permissions:['platform.textbooks.read','platform.textbooks.write','platform.textbooks.review'],session_id:'session',expires_at:'2099-01-01T00:00:00Z'};
const edition={id:'70000000-0000-4000-8000-000000000001',code:'CW_0123456789ABCDEF0123456789ABCDEF',title:'数学',stage:'primary',grade:3,
  subject:'数学',publisher:'示例出版社',edition_label:'2026版',academic_year:'2026-2027',volume:'first',
  rights:{basis:'publisher_contract',holder:'示例出版社',reference:'CONTRACT-001',scopes:['storage','ocr','preview','retrieval'],regions:['CN'],valid_from:'2020-01-01',valid_until:'2099-12-31'},
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
const previewPng=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j1ZkAAAAASUVORK5CYII=','base64');
const contentId='a0000000-0000-4000-8000-000000000001';
const other='a0000000-0000-4000-8000-000000000002';
const initialBody={pages:[{number:1,text:'<script>window.bad=true</script>数学 😀',verified:false},{number:2,text:'',verified:false}],chapters:[] as any[]};
const panel=(page:Page)=>page.getByRole('region',{name:'教材正文校核工作区',exact:true});
async function trackPreviewUrls(page:Page) {
  await page.addInitScript(()=>{
    const created:string[]=[],revoked:string[]=[];
    (window as any).previewUrls={created,revoked};
    const create=URL.createObjectURL.bind(URL),revoke=URL.revokeObjectURL.bind(URL);
    URL.createObjectURL=blob=>{const url=create(blob);created.push(url);return url;};
    URL.revokeObjectURL=url=>{revoked.push(url);revoke(url);};
  });
}
async function setup(page:Page,options:{empty?:boolean;review?:boolean;readonly?:boolean;state?:string}={}) {
  const ready=!!options.review;
  const initial={id:contentId,job_id:draft.id,edition_id:edition.id,edition_revision:3,document_id:receipt.id,
    revision:1,current_revision:1,revision_id:other,state:options.state??(ready?'pending_review':'draft'),created_by:ready?other:admin.id,
    submitted_by:ready?other:null,actor_id:ready?other:admin.id,action:ready?'submit':'create',reason:'逐页人工校核',
    created_at:'2026-10-26T02:00:00Z',source_created_at:'2026-10-26T01:00:00Z',page_count:2,chapter_count:ready?1:0,
    verified_pages:ready?2:0,sample_pages:[],required_sample_pages:[1,2],ready,ocr_performed:false,
    contributors:[ready?other:admin.id],content_available:false};
  const reviewBody={pages:initialBody.pages.map(p=>({...p,text:p.text||'人工确认空白页',verified:true})),
    chapters:[{code:'CH1',title:'第一章',parent_code:null,first_page:1,last_page:2}]};
  const state={head:options.empty?null:initial as any,bodies:{1:structuredClone(ready?reviewBody:initialBody)} as Record<number,any>,
    history:options.empty?[]:[structuredClone(initial)] as any[],requests:[] as any[],lose:false,denied:false,blocked:false,images:[] as number[],imageStatus:200,imageCode:'draft_rights_unavailable',imageWait:null as Promise<void>|null,errors:[] as string[]};
  page.on('pageerror',e=>state.errors.push(e.message));
  await page.route('**/api/v1/admin/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    let data:unknown={revoked:true};
    if(path.endsWith('/auth/captcha')) data={captcha_id:'11111111-1111-1111-1111-111111111111',image:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='};
    if(path.endsWith('/auth/login')) data={access_token:'test-memory-token',audience:'platform'};
    else if(path.endsWith('/auth/me')) data={...admin,permissions:options.readonly?['platform.textbooks.read']:admin.permissions};
    else if(path.includes('/contents') || path.endsWith('/content')) {
      if(state.denied){await route.fulfill({status:403,json:{error:{}}});return;}
      if(state.blocked && (path.endsWith('/body') || path.endsWith('/txt'))){
        await route.fulfill({status:409,json:{error:{code:'content_rights_unavailable'}}});return;
      }
      if(request.method()==='POST') {
        const payload=request.postDataJSON();state.requests.push(payload);
        const operation=path.endsWith('/content')?'create':path.endsWith('/revisions')?'revise':path.split('/').at(-1)!;
        // A retry returns the first committed operation without applying it twice.
        if(state.requests.length===1 || state.requests.at(-2).operation_id!==payload.operation_id){
          const revision=operation==='create'?1:state.head.revision+1;
          const body=payload.body??state.bodies[state.head?.revision??1];
          state.bodies[revision]=structuredClone(body);
          const verified=body.pages.filter((p:any)=>p.verified).length;
          state.head={...initial,...state.head,revision,current_revision:revision,action:operation,reason:payload.reason,
            actor_id:admin.id,state:({create:'draft',revise:'draft',submit:'pending_review',approve:'published',reject:'draft',withdraw:'withdrawn'} as any)[operation],
            submitted_by:operation==='submit'?admin.id:state.head?.submitted_by??null,
            chapter_count:body.chapters.length,verified_pages:verified,ready:verified===2 && body.chapters.length>0,
            sample_pages:payload.sample_pages??[],content_available:operation==='approve'};
          state.history.unshift(structuredClone(state.head));
        }
        data=state.head;
        if(state.lose){state.lose=false;await route.abort('failed');return;}
      } else if(path.endsWith('/body')) {
        const rev=Number(path.split('/').at(-2)); data={content_id:contentId,revision:rev,body:state.bodies[rev]};
      } else if(path.endsWith('/history')) data={items:state.history.map(v=>({...v,current_revision:state.head.revision,content_available:v.revision===state.head.revision&&v.state==='published'})),next_before:null};
      else if(path.endsWith('/txt')) data={content_id:contentId,revision:state.head.revision,text:'[PDF page 1]\n已校核数学 😀',chapters:state.bodies[state.head.revision].chapters};
      else if(path.endsWith('/contents')) data={items:state.head?[state.head]:[],next_cursor:null};
      else data=state.head;
    } else if(/\/pages\/\d+\/image$/.test(path)) {
      state.images.push(Number(path.split('/').at(-2)));
      const status=state.imageStatus,code=state.imageCode,wait=state.imageWait;
      if(wait) await wait;
      if(status!==200) await route.fulfill({status,json:{error:{code}}});
      else await route.fulfill({contentType:'image/png',body:previewPng});
      return;
    } else if(path.includes('/processing-jobs')) data=path.endsWith('/processing-jobs')?{items:[draft],next_cursor:null}:draft;
    else if(path.endsWith('/documents')) data={items:[receipt],next_cursor:null};
    else if(path.endsWith('/textbook-editions')) data={items:[edition],next_cursor:null};
    else if(path.endsWith('/revisions')) data={items:[edition],next_cursor:null};
    else if(path.endsWith(`/textbook-editions/${edition.id}`)) data=edition;
    await route.fulfill({json:{data}});
  });
  await page.goto('/');await page.getByLabel('平台邮箱').fill(admin.email);
  await page.getByLabel('登录密码',{exact:true}).fill('test-only');await page.getByLabel('验证码').fill('K7M2');
  await page.getByRole('button',{name:'登录工作台',exact:true}).click();
  await page.getByRole('button',{name:'教材管理',exact:true}).click();
  await page.getByRole('button',{name:`查看教材 ${edition.code}`,exact:true}).click();
  const processing=page.getByRole('region',{name:'数学.pdf 的处理任务',exact:true});
  await processing.getByRole('button',{name:'查看处理任务',exact:true}).click();
  await processing.getByRole('button',{name:'查看任务详情',exact:true}).first().click();
  await panel(page).getByRole('button',{name:'打开校核工作区',exact:true}).click();
  await expect(panel(page).getByRole('button',{name:'刷新当前版本',exact:true})).toBeEnabled();
  return state;
}
async function read(page:Page){await panel(page).getByRole('button',{name:'读取当前正文',exact:true}).click();await expect(panel(page).getByLabel('本页校核正文',{exact:true})).toBeVisible();}
async function reason(page:Page,value='逐页对照原件核对'){await panel(page).getByLabel('正文操作说明',{exact:true}).fill(value);}

test('create, edit pages and chapter mapping, save and submit with unsaved-change protection',async({page})=>{
  const state=await setup(page,{empty:true});await reason(page,'建立人工校核稿');
  await panel(page).getByRole('button',{name:'建立校核草稿',exact:true}).click();await read(page);
  expect(await page.evaluate(()=>('bad' in window))).toBe(false);
  await panel(page).getByLabel('本页校核正文',{exact:true}).fill('人工校核数学 😀');
  await panel(page).getByLabel('本页已与原件核对',{exact:true}).check();
  await expect(panel(page).getByRole('button',{name:'刷新当前版本',exact:true})).toBeDisabled();
  await panel(page).getByLabel('校核页码',{exact:true}).selectOption('2');
  await panel(page).getByLabel('本页校核正文',{exact:true}).fill('原件本页为空白');
  await panel(page).getByLabel('本页已与原件核对',{exact:true}).check();
  await panel(page).getByRole('button',{name:'新增章节',exact:true}).click();
  await panel(page).getByLabel('章节1名称',{exact:true}).fill('第一章');
  await panel(page).getByLabel('章节1起始页',{exact:true}).fill('1');
  await panel(page).getByLabel('章节1结束页',{exact:true}).fill('2');
  await reason(page);await panel(page).getByRole('button',{name:'保存正文与章节',exact:true}).click();
  await expect(panel(page)).toContainText('修订正文已确认');await read(page);await reason(page,'提交独立复核');
  await panel(page).getByRole('button',{name:'提交独立审核',exact:true}).click();
  await expect(panel(page)).toContainText('您参与过本内容的编辑');
  expect(state.requests).toHaveLength(3);expect(state.head.state).toBe('pending_review');expect(state.errors).toEqual([]);
});

test('independent reviewer checks required pages, publishes, downloads TXT and withdraws with history',async({page})=>{
  const state=await setup(page,{review:true});await read(page);await reason(page,'抽检一致，批准发布');
  await expect(panel(page).getByRole('button',{name:'审核通过并发布',exact:true})).toBeDisabled();
  await panel(page).getByLabel('抽检第1页已核对',{exact:true}).check();
  await panel(page).getByLabel('抽检第2页已核对',{exact:true}).check();
  await panel(page).getByRole('button',{name:'审核通过并发布',exact:true}).click();
  const download=page.waitForEvent('download');
  await panel(page).getByRole('button',{name:'下载已发布 TXT',exact:true}).click();
  expect((await download).suggestedFilename()).toBe(`${edition.code}-v2.txt`);
  await panel(page).getByText('本次下载的 TXT 正文',{exact:true}).click();
  await expect(panel(page).locator('pre')).toContainText('已校核数学 😀');
  await reason(page,'发现内容需修订');await panel(page).getByRole('button',{name:'撤回内容',exact:true}).click();
  await expect(panel(page)).toContainText('撤回已确认');
  await expect(panel(page).getByRole('button',{name:'下载已发布 TXT',exact:true})).toHaveCount(0);
  await panel(page).getByRole('button',{name:'查看内容历史',exact:true}).click();
  await panel(page).getByRole('button',{name:'查看 v1 正文',exact:true}).click();
  await expect(panel(page)).toContainText('历史只读');await expect(panel(page).getByLabel('本页校核正文',{exact:true})).not.toBeEditable();
  expect(state.requests[0].sample_pages).toEqual([1,2]);expect(state.errors).toEqual([]);
});

test('independent reviewer can reject without publishing',async({page})=>{
  const state=await setup(page,{review:true});await reason(page,'章节定位需要重核');
  await panel(page).getByRole('button',{name:'驳回至校核',exact:true}).click();
  await expect(panel(page)).toContainText('驳回已确认');expect(state.head.state).toBe('draft');
});

test('unknown write response retries frozen operation and body',async({page})=>{
  const state=await setup(page,{empty:true});state.lose=true;await reason(page,'建立人工校核稿');
  await panel(page).getByRole('button',{name:'建立校核草稿',exact:true}).click();
  await expect(panel(page).getByRole('button',{name:'重试原操作',exact:true})).toBeVisible();
  await panel(page).getByRole('button',{name:'重试原操作',exact:true}).click();await read(page);
  expect(state.requests).toHaveLength(2);expect(state.requests[0]).toEqual(state.requests[1]);expect(state.head.revision).toBe(1);
});

test('rights failure clears body while keeping withdrawal available',async({page})=>{
  const state=await setup(page,{review:true});await read(page);state.blocked=true;
  await panel(page).getByRole('button',{name:'重新读取当前正文',exact:true}).click();
  await expect(panel(page).getByLabel('本页校核正文',{exact:true})).toHaveCount(0);
  await expect(panel(page).getByRole('button',{name:'读取当前正文',exact:true})).toBeDisabled();
  await reason(page,'授权变更，撤回内容');await panel(page).getByRole('button',{name:'撤回内容',exact:true}).click();
  await expect(panel(page)).toContainText('撤回已确认');expect(state.head.state).toBe('withdrawn');
});

test('permission failure clears loaded text and stops subsequent requests',async({page})=>{
  const state=await setup(page);await read(page);state.denied=true;
  await panel(page).getByRole('button',{name:'重新读取当前正文',exact:true}).click();
  await expect(panel(page).getByLabel('本页校核正文',{exact:true})).toHaveCount(0);
  await expect(panel(page).getByRole('button',{name:'刷新当前版本',exact:true})).toBeDisabled();
});

test('readonly account cannot edit and content fits a 390px viewport',async({page})=>{
  await page.setViewportSize({width:390,height:844});const state=await setup(page,{readonly:true});await read(page);
  await expect(panel(page).getByLabel('本页校核正文',{exact:true})).not.toBeEditable();
  await expect(panel(page).getByRole('button',{name:'新增章节',exact:true})).toHaveCount(0);
  const preview=await panel(page).getByRole('region',{name:'原 PDF 单页预览',exact:true}).boundingBox();
  const text=await panel(page).getByLabel('本页校核正文',{exact:true}).boundingBox();
  expect(preview!.y+preview!.height).toBeLessThanOrEqual(text!.y);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(state.errors).toEqual([]);
});


test('page preview switches safely, zooms and releases image URLs on page change and unmount',async({page})=>{
  await page.setViewportSize({width:1440,height:1000});await trackPreviewUrls(page);
  const state=await setup(page);await read(page);
  const image=panel(page).getByRole('img',{name:'原 PDF 第 1 页',exact:true});
  await expect(image).toBeVisible();await expect(image).toHaveJSProperty('naturalWidth',1);
  const preview=await panel(page).getByRole('region',{name:'原 PDF 单页预览',exact:true}).boundingBox();
  const text=await panel(page).getByLabel('本页校核正文',{exact:true}).boundingBox();
  expect(preview!.x+preview!.width).toBeLessThanOrEqual(text!.x);
  expect(text!.y).toBeLessThan(preview!.y+preview!.height);
  await panel(page).getByRole('button',{name:'放大原件',exact:true}).click();
  await expect(panel(page).getByRole('button',{name:'适合宽度',exact:true})).toBeVisible();
  await panel(page).getByLabel('校核页码',{exact:true}).selectOption('2');
  await expect(panel(page).getByRole('img',{name:'原 PDF 第 2 页',exact:true})).toBeVisible();
  await expect(image).toHaveCount(0);
  expect(state.images).toEqual([1,2]);
  await expect.poll(()=>page.evaluate(()=>(window as any).previewUrls.revoked.length)).toBe(1);
  await page.getByRole('button',{name:'账号与会话',exact:true}).click();
  await expect.poll(()=>page.evaluate(()=>(window as any).previewUrls.revoked.length)).toBe(2);
  expect(state.errors).toEqual([]);
});

test('preview service failure offers retry without deleting editable text',async({page})=>{
  const state=await setup(page);state.imageStatus=503;await read(page);
  await expect(panel(page).getByRole('button',{name:'重新加载原件',exact:true})).toBeVisible();
  await expect(panel(page).getByLabel('本页校核正文',{exact:true})).toHaveValue(initialBody.pages[0].text);
  state.imageStatus=200;await panel(page).getByRole('button',{name:'重新加载原件',exact:true}).click();
  await expect(panel(page).getByRole('img',{name:'原 PDF 第 1 页',exact:true})).toHaveJSProperty('naturalWidth',1);
  expect(state.images).toEqual([1,1]);expect(state.errors).toEqual([]);
});

for(const code of ['draft_rights_unavailable','preview_source_mismatch']) test(`preview ${code} clears text and prevents further reads`,async({page})=>{
  await trackPreviewUrls(page);const state=await setup(page);await read(page);
  await expect(panel(page).getByRole('img',{name:'原 PDF 第 1 页',exact:true})).toHaveJSProperty('naturalWidth',1);
  state.imageStatus=409;state.imageCode=code;
  await panel(page).getByLabel('校核页码',{exact:true}).selectOption('2');
  await expect(panel(page).getByLabel('本页校核正文',{exact:true})).toHaveCount(0);
  await expect(panel(page).getByRole('region',{name:'原 PDF 单页预览',exact:true})).toHaveCount(0);
  await expect(panel(page).getByRole('button',{name:'读取当前正文',exact:true})).toBeDisabled();
  await expect.poll(()=>page.evaluate(()=>(window as any).previewUrls.revoked.length)).toBe(1);
  expect(state.images).toEqual([1,2]);expect(state.errors).toEqual([]);
});

for(const status of [401,403]) test(`preview ${status} clears loaded content and preview resources`,async({page})=>{
  await trackPreviewUrls(page);const state=await setup(page);await read(page);
  await expect(panel(page).getByRole('img',{name:'原 PDF 第 1 页',exact:true})).toHaveJSProperty('naturalWidth',1);
  state.imageStatus=status;await panel(page).getByLabel('校核页码',{exact:true}).selectOption('2');
  await expect(page.getByLabel('本页校核正文',{exact:true})).toHaveCount(0);
  await expect(page.getByRole('region',{name:'原 PDF 单页预览',exact:true})).toHaveCount(0);
  if(status===401) await expect(page.getByRole('heading',{name:'登录平台工作台',exact:true})).toBeVisible();
  else await expect(panel(page).getByRole('button',{name:'刷新当前版本',exact:true})).toBeDisabled();
  await expect.poll(()=>page.evaluate(()=>(window as any).previewUrls.revoked.length)).toBe(1);
  expect(state.images).toEqual([1,2]);expect(state.errors).toEqual([]);
});

test('page switch aborts the pending request and late response never replaces the current preview',async({page})=>{
  await page.addInitScript(()=>{
    const signals:AbortSignal[]=[];(window as any).previewSignals=signals;
    const fetch=window.fetch.bind(window);
    window.fetch=(input,init)=>{
      if(String(input).match(/\/pages\/\d+\/image$/) && init?.signal) signals.push(init.signal);
      return fetch(input,init);
    };
  });
  const state=await setup(page);let release!:()=>void;
  state.imageWait=new Promise<void>(resolve=>{release=resolve;});
  await read(page);await expect.poll(()=>state.images.length).toBe(1);
  state.imageWait=null;await panel(page).getByLabel('校核页码',{exact:true}).selectOption('2');
  await expect(panel(page).getByRole('img',{name:'原 PDF 第 2 页',exact:true})).toHaveJSProperty('naturalWidth',1);
  await expect.poll(()=>page.evaluate(()=>(window as any).previewSignals[0]?.aborted)).toBe(true);
  release();await expect(panel(page).getByRole('img',{name:'原 PDF 第 1 页',exact:true})).toHaveCount(0);
  expect(state.images).toEqual([1,2]);expect(state.errors).toEqual([]);
});
