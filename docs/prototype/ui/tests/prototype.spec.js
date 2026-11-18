import { test, expect } from '@playwright/test';
const routes={child:['home','homework','mistakes','preview','pairing','review','handwriting'],parent:['overview','submissions','mistakes','reports','schedule','privacy','tutoring','handwriting'],platform:['ops','tenants','jobs','people','schools','books','models','admins','audit']};
for(const width of [1440,390]) for(const [end,tabs] of Object.entries(routes)) for(const tab of tabs) test(`${end}/${tab} @${width}`,async({page})=>{
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.setViewportSize({width,height:1000});await page.goto(`/${end}.html#${tab}`);
  await expect(page.locator('h1:visible')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();
  if(width===390){
    await page.getByRole('button',{name:'展开导航'}).click();
    await expect(page.getByRole('navigation',{name:'主导航'})).toBeVisible();
    const close=page.getByRole('button',{name:'关闭菜单'});
    await expect(close).toBeVisible();
    const target=await close.boundingBox();
    expect(target.x+target.width/2).toBeGreaterThanOrEqual(246);
    await close.click();
    await expect(close).toBeHidden();
    await expect(page.locator('.app-shell')).not.toHaveClass(/menu-open/);
  }
  expect(errors).toEqual([]);
});
test('school duplicate and roster validation preserve form',async({page})=>{
 await page.goto('/platform.html#schools');await page.getByRole('button',{name:'新建学校',exact:true}).click();
 const d=page.getByRole('dialog');const inputs=d.locator('input');
 for(const [i,v] of ['测试学校','DEMO-SCH-001','演示地区','2026','DEMO-AUTH'].entries()) await inputs.nth(i).fill(v);
 await d.getByRole('button',{name:'保存',exact:true}).click();await expect(page.getByText('组织编码已存在')).toBeVisible();await expect(d).toBeVisible();
 await inputs.nth(1).fill('DEMO-SCH-NEW');await d.getByRole('button',{name:'保存',exact:true}).click();await expect(d).toBeHidden();await expect(page.getByText('测试学校',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'名册校验',exact:true}).click();await d.locator('textarea').fill('DEMO-1,三年级\nDEMO-1,三年级');await d.getByRole('button',{name:'保存',exact:true}).click();await expect(d).toBeVisible();await expect(page.getByText(/校验失败：/)).toBeVisible();
 await d.locator('textarea').fill('DEMO-1,三年级');await d.getByRole('button',{name:'保存',exact:true}).click();await expect(d).toBeHidden();
});
test('correction requires evidence and preserves machine record',async({page})=>{
 await page.goto('/parent.html#submissions');await page.getByRole('button',{name:'核对详情',exact:true}).first().click();
 await page.getByRole('button',{name:'保存核对版本'}).click();await expect(page.getByText('请填写核对依据，保留可追溯记录')).toBeVisible();
 await page.getByPlaceholder('例如：对照原图确认漏写了两条边').fill('原图漏写两条边');await page.locator('label.el-radio').filter({hasText:'需订正'}).click();await page.getByRole('button',{name:'保存核对版本'}).click();
 await expect(page.getByText('人工核对 v1：需订正')).toBeVisible();await expect(page.getByText('机器原始记录 · v1 · 只读')).toBeVisible();
});
test('chapter update changes displayed progress',async({page})=>{
 await page.goto('/parent.html#schedule');await page.getByRole('button',{name:'更新教学进度'}).click();await page.getByRole('dialog').locator('.el-select__wrapper').click();await page.getByRole('option',{name:'单元复习'}).click();await page.getByRole('dialog').getByRole('button',{name:'保存',exact:true}).click();await expect(page.getByText('当前章节：单元复习')).toBeVisible();
});
test('model report cannot imply passing admission',async({page})=>{
 await page.goto('/platform.html#models');await expect(page.getByText('未达准入',{exact:true}).first()).toBeVisible();await page.getByRole('button',{name:'评测报告',exact:true}).first().click();await expect(page.getByText(/95.2% 不达标/)).toBeVisible();await page.getByRole('button',{name:'申请评测',exact:true}).click();await expect(page.getByRole('button',{name:'评测申请已登记'})).toBeDisabled();
});

test('child upload validates attempt and leads to correction',async({page})=>{
 await page.goto('/child.html#homework');
 await page.getByRole('button',{name:'开始检查 · 0 张'}).click();
 await expect(page.getByRole('alert')).toHaveText('请先确认已经独立尝试完成作业');
 await page.getByLabel('我已经自己试着完成了这些题').check();await page.getByRole('button',{name:'开始检查 · 0 张'}).click();
 await expect(page.getByRole('alert')).toHaveText('请先选择至少一张图片');
 await page.locator('input[type=file]').setInputFiles({name:'homework.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=','base64')});
 await page.getByRole('button',{name:'旋转第1张'}).click();await expect(page.locator('.photo-card img')).toHaveAttribute('style',/rotate\(90deg\)/);
 await page.getByRole('button',{name:'开始检查 · 1 张'}).click();await expect(page.getByText('检查示例已准备好')).toBeVisible();
 await page.getByRole('button',{name:'从提示开始订正'}).click();await page.getByPlaceholder('写下你的答案和想法').fill('26米');await page.getByRole('button',{name:'检查我的订正'}).click();await expect(page.getByText('这次你把四条边都算上了！')).toBeVisible();
});
test('book workflow requires rights and submits only for review',async({page})=>{
 await page.goto('/platform.html#books');await page.getByRole('button',{name:'开始模拟解析'}).click();
 await expect(page.getByText('请先选择 PDF、填写教材名称并登记权利依据')).toBeVisible();
 await page.getByLabel('选择教材PDF').setInputFiles({name:'demo.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.4\n% local test fixture, no OCR\n%%EOF')});
 await page.getByPlaceholder('如：数学 · 三年级上册').fill('测试教材');await page.locator('.book-workflow .el-select__wrapper').click();await page.getByRole('option',{name:'自有原创内容'}).click();
 await page.getByRole('button',{name:'开始模拟解析'}).click();await expect(page.getByText('演示解析稿已就绪')).toBeVisible();
 const downloaded=page.waitForEvent('download');await page.getByRole('button',{name:/下载.*TXT/}).click();expect((await downloaded).suggestedFilename()).toMatch(/\.txt$/);
 await page.getByRole('button',{name:/提交.*抽检/}).click();await expect(page.locator('tr').filter({hasText:'测试教材'})).toContainText('待复核');
});
test('privacy verification preserves rejected input before recording request',async({page})=>{
 await page.goto('/parent.html#privacy');await page.getByRole('button',{name:'申请导出',exact:true}).click();const d=page.getByRole('dialog');
 await d.locator('input').nth(0).fill('000000');await d.locator('input').nth(1).fill('确认导出');await d.getByRole('button',{name:'保存',exact:true}).click();
 await expect(page.getByText('验证码或确认文字不匹配')).toBeVisible();await expect(d.locator('input').nth(1)).toHaveValue('确认导出');
 await d.locator('input').nth(0).fill('246810');await d.getByRole('button',{name:'保存',exact:true}).click();await expect(d).toBeHidden();await expect(page.getByText('待处理 · 演示')).toBeVisible();
});
