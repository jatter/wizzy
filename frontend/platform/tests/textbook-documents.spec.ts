import { createHash } from 'node:crypto';
import { test, expect, type Page } from '@playwright/test';

const admin={id:'70000000-0000-4000-8000-000000000003',email:'security@example.test',role:'security_admin',
  audience:'platform',permissions:['platform.textbooks.read','platform.textbooks.write'],session_id:'session',expires_at:'2099-01-01T00:00:00Z'};
const edition={id:'70000000-0000-4000-8000-000000000001',code:'MATH_PDF',title:'数学',stage:'primary',grade:3,
  subject:'数学',publisher:'示例出版社',edition_label:'2026版',academic_year:'2026-2027',volume:'first',
  rights:{basis:'publisher_contract',holder:'示例出版社',reference:'CONTRACT-001',scopes:['storage','ocr'],regions:['CN'],valid_from:'2020-01-01',valid_until:'2099-12-31'},
  revision:3,revision_id:'70000000-0000-4000-8000-000000000002',state:'approved',action:'approve',
  creator_id:admin.id,editor_id:admin.id,submitted_by:admin.id,actor_id:admin.id,reason:'独立审核通过',
  created_at:'2026-10-26T00:00:00Z',registered_at:'2026-10-26T00:00:00Z',availability:'rights_approved',content_available:false};
const bytes=Buffer.from('%PDF-1.7\nprototype upload test\n%%EOF');
const file={name:'数学.pdf',mimeType:'application/pdf',buffer:bytes};
const receipt={id:'80000000-0000-4000-8000-000000000001',edition_id:edition.id,edition_revision:3,filename:file.name,
  size_bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex'),status:'awaiting_upload',scan_status:'not_scanned',content_available:false,
  created_by:admin.id,operation_id:'80000000-0000-4000-8000-000000000002',reason:'接收授权原件',
  created_at:new Date().toISOString(),expires_at:new Date(Date.now()+900000).toISOString(),received_at:null as string|null};
async function setup(page:Page,options:{readonly?:boolean;draft?:boolean;existing?:boolean}={}) {
  const state={intents:[] as Record<string,any>[],uploads:[] as Buffer[],docs:options.existing?[structuredClone(receipt)]:[] as typeof receipt[],
    loseIntent:false,loseUpload:false,forbid:false,badContract:false,listFailure:false,errors:[] as string[]};
  page.on('pageerror',error=>state.errors.push(error.message));
  await page.route('**/api/v1/admin/**',async route=>{
    const req=route.request(),path=new URL(req.url()).pathname;let data:unknown={revoked:true};
    if(path.endsWith('/auth/captcha')) data={captcha_id:'11111111-1111-1111-1111-111111111111',image:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='};
    if(path.endsWith('/auth/login')) data={access_token:'test-memory-token',audience:'platform'};
    else if(path.endsWith('/auth/me')) data={...admin,permissions:options.readonly?['platform.textbooks.read']:admin.permissions};
    else if(path.endsWith('/documents') && req.method()==='GET') {
      if(state.listFailure){await route.fulfill({status:503,json:{error:{}}});return;}
      data={items:state.docs.map(item=>state.badContract?{...item,content_available:true}:item),next_cursor:null};
    } else if(path.endsWith('/documents') && req.method()==='POST') {
      const body=req.postDataJSON();state.intents.push(body);
      if(state.forbid){await route.fulfill({status:403,json:{error:{}}});return;}
      if(!state.docs.length) state.docs=[{...receipt,...body,edition_revision:body.expected_revision}];
      if(state.loseIntent){state.loseIntent=false;await route.abort('failed');return;}
      data=state.docs[0];
    } else if(path.endsWith('/content')) {
      state.uploads.push(req.postDataBuffer()!);
      state.docs[0]={...state.docs[0],status:'quarantined',received_at:new Date().toISOString()};
      if(state.loseUpload){state.loseUpload=false;await route.abort('failed');return;}
      data=state.docs[0];
    } else if(path.endsWith('/textbook-editions')) data={items:[{...edition,...(options.draft?{state:'draft',action:'create',availability:'metadata_only'}:{})}],next_cursor:null};
    else if(path.endsWith('/revisions')) data={items:[edition],next_cursor:null};
    else if(path.endsWith(`/textbook-editions/${edition.id}`)) data={...edition,...(options.draft?{state:'draft',action:'create',availability:'metadata_only'}:{})};
    await route.fulfill({json:{data}});
  });
  await page.goto('/');await page.getByLabel('平台邮箱').fill(admin.email);
  await page.getByLabel('登录密码',{exact:true}).fill('test-only');await page.getByLabel('验证码').fill('K7M2');
  await page.getByRole('button',{name:'登录工作台',exact:true}).click();
  await page.getByRole('button',{name:'教材管理',exact:true}).click();
  await page.getByRole('button',{name:`查看教材 ${edition.code}`,exact:true}).click();
  await expect(panel(page).locator('.document-list')).toHaveAttribute('aria-busy','false');return state;
}
const panel=(page:Page)=>page.getByRole('region',{name:'教材 PDF 文件',exact:true});
async function select(page:Page,upload=file) {
  await panel(page).getByLabel('教材 PDF（最大 50 MiB）',{exact:true}).setInputFiles(upload);
  await panel(page).getByLabel('PDF 接收原因',{exact:true}).fill('接收授权原件');
}

test('authorized PDF computes SHA, sends exact bytes and shows quarantine receipt without content access',async({page})=>{
  const state=await setup(page);await select(page);await panel(page).getByRole('button',{name:'接收 PDF',exact:true}).click();
  await expect(panel(page)).toContainText('PDF 已接收并隔离');await expect(panel(page)).toContainText('已隔离接收');
  expect(state.intents).toHaveLength(1);expect(state.intents[0]).toMatchObject({filename:file.name,size_bytes:bytes.length,sha256:receipt.sha256,expected_revision:3});
  expect(state.uploads).toEqual([bytes]);await panel(page).getByText('查看接收凭证与摘要',{exact:true}).click();
  await expect(panel(page)).toContainText(receipt.sha256);await expect(panel(page).getByRole('link')).toHaveCount(0);
  expect(state.errors).toEqual([]);
});

for(const loss of ['loseIntent','loseUpload'] as const) test(`lost ${loss} retries original operation and locks local selection`,async({page})=>{
  const state=await setup(page);state[loss]=true;await select(page);
  await panel(page).getByRole('button',{name:'接收 PDF',exact:true}).click();
  await expect(panel(page)).toContainText('接收结果未确认');await expect(panel(page).getByLabel('PDF 接收原因',{exact:true})).toBeDisabled();
  await expect(panel(page).getByLabel('教材 PDF（最大 50 MiB）',{exact:true})).toBeDisabled();
  await panel(page).getByRole('button',{name:'重试原上传',exact:true}).click();await expect(panel(page)).toContainText('PDF 已接收并隔离');
  expect(state.intents).toHaveLength(loss==='loseIntent'?2:1);
  if(loss==='loseIntent') expect(state.intents[1]).toEqual(state.intents[0]);
  expect(state.uploads).toEqual(loss==='loseUpload'?[bytes,bytes]:[bytes]);expect(state.errors).toEqual([]);
});

test('pending receipt resumes only with matching original file and sends no second intent',async({page})=>{
  const state=await setup(page,{existing:true});await select(page,{...file,buffer:Buffer.from('%PDF-other')});
  await panel(page).getByRole('button',{name:'使用所选原文件继续上传',exact:true}).click();
  await expect(panel(page).getByRole('alert')).toContainText('与原申请不一致');expect(state.uploads).toHaveLength(0);
  await select(page);await panel(page).getByRole('button',{name:'使用所选原文件继续上传',exact:true}).click();
  await expect(panel(page)).toContainText('已隔离接收');expect(state.intents).toHaveLength(0);expect(state.uploads).toEqual([bytes]);
});

test('rejects invalid extension, empty and fake PDF before requesting upload',async({page})=>{
  const state=await setup(page);
  for(const upload of [{...file,name:'math.txt'},{...file,buffer:Buffer.alloc(0)},{...file,buffer:Buffer.from('fake file')}]) {
    await select(page,upload);await panel(page).getByRole('button',{name:'接收 PDF',exact:true}).click();await expect(panel(page).getByRole('alert')).toBeVisible();
  }
  expect(state.intents).toHaveLength(0);expect(state.uploads).toHaveLength(0);
});

for(const options of [{readonly:true},{draft:true}]) test(`file reception unavailable for ${Object.keys(options)[0]}`,async({page})=>{
  await setup(page,options);await expect(panel(page).getByLabel('教材 PDF（最大 50 MiB）',{exact:true})).toHaveCount(0);
  await expect(panel(page)).toContainText(options.readonly?'没有接收文件权限':'仅已批准');
});

test('file list failure can recover and forged available-content receipt is rejected',async({page})=>{
  const state=await setup(page,{existing:true});state.listFailure=true;
  await panel(page).getByRole('button',{name:'刷新文件记录',exact:true}).click();await expect(panel(page).getByRole('alert')).toBeVisible();
  state.listFailure=false;state.badContract=true;await panel(page).getByRole('button',{name:'重试文件列表',exact:true}).click();
  await expect(panel(page).getByRole('alert')).toBeVisible();state.badContract=false;
  await panel(page).getByRole('button',{name:'重试文件列表',exact:true}).click();await expect(panel(page).getByRole('alert')).toHaveCount(0);
  await expect(panel(page)).toContainText('待上传');
});

test('denied upload clears selected bytes and shows permission error',async({page})=>{
  const state=await setup(page);state.forbid=true;await select(page);await panel(page).getByRole('button',{name:'接收 PDF',exact:true}).click();
  await expect(panel(page).getByRole('alert')).toContainText('当前账号没有此项权限');
  await expect(panel(page).getByLabel('PDF 接收原因')).toHaveValue('');
  expect(await panel(page).getByLabel('教材 PDF（最大 50 MiB）',{exact:true}).evaluate((input: HTMLInputElement)=>input.files?.length)).toBe(0);
  await expect(panel(page)).not.toContainText('接收结果未确认');expect(state.uploads).toHaveLength(0);
});

test('mobile PDF form fits viewport',async({page})=>{
  await page.setViewportSize({width:390,height:844});await setup(page,{existing:true});
  await panel(page).getByText('查看接收凭证与摘要',{exact:true}).click();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
