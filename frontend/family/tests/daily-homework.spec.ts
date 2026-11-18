import { test, expect, type Page } from '@playwright/test';
const button = (page: Page, name: string) => page.getByRole('button', { name, exact: true });
const day = () => ({ id: null as string | null, student_id: 's1', assigned_date: '2026-11-13', revision: 0, scope_state: 'incomplete', subjects: { chinese:'unknown', math:'unknown', english:'unknown', other:'unknown' }, pending_imports: 0, items: [] as any[], summary: { required:0,completed:0,verified:0,preparation_total:0,preparation_completed:0,all_verified:false,no_homework:false } });
async function setup(page: Page, child = false, dropFirst = true) {
  let value = day(), lost = true;
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.name));
  const writes: unknown[] = [];
  const baseItem = { id:'i1',student_id:'s1',day_id:'d1',assigned_date:'2026-11-13',revision:1,sort_order:1,subject:'chinese',item_type:'reading',title:'阅读课文',raw_text:'',locator:{},due_at:null,work_state:'pending',verification_state:'unverified',completion_actor:null,sources:[],events:[{id:'e1',actor_audience:'guardian',action:'created',reason:'',before_value:null,after_value:{title:'阅读课文'},created_at:'2026-11-13T10:00:00Z'}] };
  if (child) { value.items.push(baseItem); value.summary.required = 1; }
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname.replace('/api/v1','');
    const data = (data: unknown) => route.fulfill({ json:{ data } });
    if (path === '/auth/me') return data({ audience:child?'child':'guardian',tenant_id:'family',student_id:child?'s1':null,role:child?null:'owner' });
    if (path === '/tenants') return data({items:[{id:'family',name:'验收家庭',role:'owner'}]});
    if (path === '/students') return data({items:[{id:'s1',nickname:'小禾',grade:6,revision:1,consent_active:true}],next_cursor:null});
    if (path === '/students/s1') return data({id:'s1',nickname:'小禾',grade:6,revision:1,consent_active:true});
    if (path === '/evaluation-policy') return data({version:'v1',text:'授权',optional:true});
    if (path.endsWith('/evaluation-consent')) return data({revision:0,granted:false,active:false});
    if (path === '/quota') return data({remaining_pages:100});
    if (path.endsWith('/homework-backlog')) return data({items:[]});
    if (path.endsWith('/items') && request.method() === 'POST') {
      const body = request.postDataJSON(); writes.push(body);
      const created = { ...baseItem, ...body, id: 'i' + (value.items.length + 1) };
      value.items = dropFirst ? [created] : [...value.items, created]; value.revision = 1; value.summary.required = value.items.length;
      if (lost && dropFirst) { lost=false; return route.abort('failed'); }
      return data(created);
    }
    if (path.includes('/homework-days/')) return data(value);
    if (path === '/homework-items/i1/progress') { const body=request.postDataJSON(); writes.push(body); value.items[0]={...value.items[0],work_state:body.state,revision:2,completion_actor:'child'}; value.summary.completed=body.state==='completed'?1:0; return data(value.items[0]); }
    if (path === '/homework-items/i1') return data(value.items[0]);
    if (path.endsWith('/mistakes')) return data({items:[],next_cursor:null,pending_updates:0});
    if (path.endsWith('/homework-imports') || path === '/homework-imports') return data({items:[],next_cursor:null});
    if (path === '/child-sessions' || path.endsWith('/mistake-bank') || path.endsWith('/mistake-sheets') || path.endsWith('/review-tasks') || path.endsWith('/terms') || ['/review-plans','/reports','/submissions'].includes(path) || path.endsWith('/submission-days')) return data({items:[],next_cursor:null});
    throw new Error('Unexpected daily route '+path);
  });
  await page.goto('/'); await button(page,'每日作业').click();
  await expect(page.getByTestId('daily-day')).toBeVisible();
  return { writes, errors };
}
test('manual receipt retry preserves date/deadline/body and empty summary is explicit', async ({ page }) => {
  const { writes } = await setup(page);
  await expect(page.getByTestId('daily-day')).toContainText('未录入 · 清单待补充');
  await page.getByTestId('daily-day').getByLabel('作业日期',{exact:true}).fill('2026-11-13');
  await button(page,'新增').click();
  await page.getByLabel('作业内容',{exact:true}).fill('阅读课文');
  await page.getByLabel('截止日期',{exact:true}).fill('2026-11-14');
  await page.getByLabel('截止时刻',{exact:true}).fill('20:30');
  await button(page,'保存作业').click();
  await expect(button(page,'重试添加作业')).toBeVisible();
  await expect(page.getByLabel('作业内容',{exact:true})).toBeDisabled();
  await button(page,'重试添加作业').click();
  await expect(page.getByTestId('daily-item')).toContainText('阅读课文');
  expect(writes).toHaveLength(2); expect(writes[0]).toEqual(writes[1]);
  expect(writes[0]).toMatchObject({due_at:'2026-11-14T20:30:00+08:00'});
  await page.setViewportSize({width:320,height:740});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
test('child progress remains unverified and guardian controls stay absent', async ({ page }) => {
  const { errors } = await setup(page,true);
  await expect(button(page,'新增')).toHaveCount(0);
  await expect(button(page,'导入作业截图')).toHaveCount(0);
  await button(page,'标记做完').click();
  await expect(page.getByTestId('daily-item')).toContainText('已标完成');
  await expect(page.getByTestId('daily-item')).toContainText('尚未经家长核验');
  await expect(page.getByTestId('daily-day')).not.toContainText('今日作业已核对完成');
  await button(page,'查看记录与来源').click();
  await expect(button(page,'家长确认完成')).toHaveCount(0);
  await expect(button(page,'删除作业')).toHaveCount(0);
  await expect(page.locator('.daily-detail')).toContainText('本次记录：阅读课文');
  expect(errors).toEqual([]);
});

test('changing candidate date clears both date and conflict acknowledgments', async ({ page }) => {
  await setup(page);
  const candidate = {id:'c1',image_id:'image1',subject:'chinese',item_type:'written',title:'小册第15课',raw_text:'小册第15课',locator:{},due_at:null,assigned_date:'2026-11-13',date_raw:'11月13日',date_candidates:[],needs_review:[],source_mark:null,bbox:null,decision:'include',merge_item_id:null,date_confirmed:true,conflict_confirmed:false};
  const record = {id:'r1',student_id:'s1',target_date:'2026-11-13',source_label:'群作业截图',status:'review_ready',revision:1,created_at:'2026-11-13T10:00:00Z',images:[{id:'image1',media_id:'m1',position:1,crop_rect:null,status:'succeeded',error_message:null}],draft:{items:[candidate],no_homework:[],notes:[]},applied_ids:{},duplicate_images:[],suggestions:[],runs:[]};
  await page.route('**/api/v1/homework-imports**', route => route.fulfill({json:{data:new URL(route.request().url()).pathname.endsWith('/r1')?record:{items:[record],next_cursor:null}}}));
  await page.route('**/api/v1/media/m1/content', route => route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aP1sAAAAASUVORK5CYII=','base64')}));
  await button(page,'导入作业截图').click();
  await page.getByTestId('import-index').click();
  const conflict = page.getByLabel('确认第1项即使重复或与无作业声明冲突仍需新增',{exact:true});
  await conflict.check();
  await expect(conflict).toBeChecked();
  await page.getByLabel('第1项布置日期',{exact:true}).fill('2026-11-14');
  await expect(conflict).not.toBeChecked();
  await expect(page.getByLabel('确认第1项布置日期',{exact:true})).not.toBeChecked();
});

test('pasted homework text fills the matching fields before save', async ({ page }) => {
  const { writes } = await setup(page, false, false);
  await button(page,'新增').click();
  await page.getByLabel('作业文字',{exact:true}).fill('语文：大册第15课、小册第15课、预习作文\n数学：练习册P13-P15\n英语：抄写U1单词3遍\n准备2本语文本\n截止：2026-11-16 20:30');
  await button(page,'识别填入').click();
  await expect(page.getByLabel('作业内容',{exact:true})).toHaveValue('大册第15课');
  await expect(page.getByLabel('书本简称',{exact:true})).toHaveValue('大册');
  await expect(page.getByLabel('课次',{exact:true})).toHaveValue('15');
  await expect(page.getByLabel('截止日期',{exact:true})).toHaveValue('2026-11-16');
  await expect(page.getByLabel('截止时刻',{exact:true})).toHaveValue('20:30');
  await expect(button(page,'第2项 小册第15课')).toHaveAttribute('aria-pressed','false');
  await button(page,'第2项 小册第15课').click();
  await expect(page.getByLabel('书本简称',{exact:true})).toHaveValue('小册');
  await button(page,'全部保存').click();
  await expect.poll(() => writes.length).toBe(6);
  expect(writes.map(body => (body as { title: string }).title)).toEqual(['大册第15课','小册第15课','预习作文','练习册P13-P15','抄写U1单词3遍','准备2本语文本']);
  await expect(page.getByTestId('daily-item').filter({ hasText: '大册第15课' })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
