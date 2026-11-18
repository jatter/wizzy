/** Real built UI, actual API/database/PDF worker. Antivirus is injected by the Python harness. */
import { chromium, expect } from '@playwright/test';
import { preview } from 'vite';
import { readFile } from 'node:fs/promises';

let input = '';
for await (const chunk of process.stdin) input += chunk;
const { apiOrigin, accounts, code, pdf } = JSON.parse(input);
let server, browser, step = 'start preview', pageErrors = 0;
try {
  server = await preview({configFile:false,logLevel:'silent',preview:{
    host:'127.0.0.1',port:0,strictPort:true,proxy:{'/api/v1/admin':{target:apiOrigin}},
  }});
  browser = await chromium.launch({channel:process.env.PLAYWRIGHT_CHANNEL || 'chrome'});
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  async function login(account) {
    const context = await browser.newContext({viewport:{width:1440,height:1000}});
    const page = await context.newPage();
    page.on('pageerror', () => { pageErrors++; });
    await page.goto(origin);
    await expect(page.locator('.captcha-refresh img')).toBeVisible();
    await page.getByLabel('平台邮箱').fill(account.email);
    await page.getByLabel('登录密码',{exact:true}).fill(account.password);
    await page.getByLabel('验证码').fill('K7M2');
    await page.getByRole('button',{name:'登录工作台',exact:true}).click();
    await page.getByRole('button',{name:'教材管理',exact:true}).click();
    return {context,page};
  }
  async function openEdition(page) {
    await page.getByRole('button',{name:`查看教材 ${code}`,exact:true}).click();
    return page.getByRole('region',{name:'教材详情',exact:true});
  }
  async function openContent(detail) {
    const processing = detail.getByRole('region',{name:'content-web.pdf 的处理任务',exact:true});
    await processing.getByRole('button',{name:'查看处理任务',exact:true}).click();
    await processing.getByRole('button',{name:'查看任务详情',exact:true}).click();
    const panel = processing.getByRole('region',{name:'教材正文校核工作区',exact:true});
    await panel.getByRole('button',{name:'打开校核工作区',exact:true}).click();
    return panel;
  }
  async function expectPreview(panel, number) {
    const image = panel.getByRole('img',{name:`原 PDF 第 ${number} 页`,exact:true});
    await expect(image).toBeVisible();
    await expect.poll(() => image.evaluate(node => node.naturalWidth)).toBeGreaterThan(1);
    await expect(image).toHaveAttribute('src',/^blob:/);
  }
  async function logout(session) {
    await session.page.getByRole('button',{name:'退出登录',exact:true}).click();
    await expect(session.page.getByRole('heading',{name:'登录平台工作台'})).toBeVisible();
    await session.context.close();
  }
  step = 'author login and rights registration';
  const author = await login(accounts[0]), page = author.page;
  await page.getByRole('button',{name:'登记教材',exact:true}).click();
  let dialog = page.getByRole('dialog',{name:'登记教材',exact:true});
  for (const [name,value] of Object.entries({'教材代码':code,'教材名称':'正文真实联调数学',
    '年级':'3','科目':'数学','出版社':'联调出版社','版本标识':'2026版','学年':'2026-2027','操作原因':'登记隔离联调教材'}))
    await dialog.getByLabel(name,{exact:true}).fill(value);
  await expect(dialog.getByLabel('教材代码',{exact:true})).toHaveValue(code);
  await dialog.getByLabel('已具备权利依据',{exact:true}).check();
  for (const [name,value] of Object.entries({'权利人':'联调出版社','权利凭据引用':'CONTENT-WEB-CONTRACT',
    '授权地区代码':'CN, CN-33','授权起始日期':'2020-01-01','授权截止日期':'2099-12-31'}))
    await dialog.getByLabel(name,{exact:true}).fill(value);
  for (const label of ['文件存储','正文预览','OCR 解析','AI 检索'])
    await dialog.getByLabel(label,{exact:true}).check();
  await dialog.getByRole('button',{name:'保存草稿',exact:true}).click();
  await expect(dialog).not.toBeVisible();
  const detail = page.getByRole('region',{name:'教材详情',exact:true});
  await detail.getByRole('button',{name:'提交审核',exact:true}).click();
  dialog = page.getByRole('dialog',{name:'提交审核',exact:true});
  await dialog.getByLabel('操作原因',{exact:true}).fill('提交教材独立权利审核');
  await dialog.getByRole('button',{name:'提交审核',exact:true}).click();
  await expect(dialog).not.toBeVisible();

  step = 'independent reviewer approves rights';
  const reviewer = await login(accounts[1]);
  step = 'reviewer opens submitted edition';
  let reviewDetail = await openEdition(reviewer.page);
  step = 'reviewer opens rights approval';
  await reviewDetail.getByRole('button',{name:'批准权利',exact:true}).click();
  dialog = reviewer.page.getByRole('dialog',{name:'批准权利',exact:true});
  step = 'reviewer enters rights approval reason';
  await dialog.getByLabel('操作原因',{exact:true}).fill('独立核实四项用途及授权期限');
  step = 'reviewer submits rights approval';
  await dialog.getByRole('button',{name:'批准权利',exact:true}).click();
  await expect(dialog).not.toBeVisible();
  step = 'reviewer observes approved edition';
  await expect(reviewDetail).toContainText('修改已批准档案需先撤回');
  step = 'author refreshes approved edition';
  await detail.getByRole('button',{name:'刷新详情',exact:true}).click();
  await expect(detail.locator('.school-history li')).toHaveCount(3);

  step = 'author uploads real three-page PDF';
  const documents = detail.getByRole('region',{name:'教材 PDF 文件',exact:true});
  await documents.getByLabel('教材 PDF（最大 50 MiB）',{exact:true}).setInputFiles({
    name:'content-web.pdf',mimeType:'application/pdf',buffer:Buffer.from(pdf,'base64')});
  await documents.getByLabel('PDF 接收原因',{exact:true}).fill('接收三页真实 PDF 用于联调');
  await documents.getByRole('button',{name:'接收 PDF',exact:true}).click();
  await expect(documents).toContainText('PDF 已接收并隔离');
  const processing = documents.getByRole('region',{name:'content-web.pdf 的处理任务',exact:true});
  await processing.getByRole('button',{name:'查看处理任务',exact:true}).click();
  await processing.getByLabel('处理原因',{exact:true}).fill('提交真实文本提取任务');
  await processing.getByRole('button',{name:'申请扫描与文本提取',exact:true}).click();
  await expect(processing).toContainText('处理申请已登记');
  step = 'worker extracts source PDF';
  await expect(async () => {
    await processing.getByRole('button',{name:'刷新任务',exact:true}).click();
    await expect(processing).toContainText('草稿待校核',{timeout:1000});
  }).toPass({timeout:30000,intervals:[100,250,500]});
  await processing.getByRole('button',{name:'查看任务详情',exact:true}).click();
  const panel = processing.getByRole('region',{name:'教材正文校核工作区',exact:true});
  await panel.getByRole('button',{name:'打开校核工作区',exact:true}).click();
  await panel.getByLabel('正文操作说明',{exact:true}).fill('依据 PDF 建立校核稿');
  await panel.getByRole('button',{name:'建立校核草稿',exact:true}).click();
  await expect(panel).toContainText('内容 v1');
  await panel.getByRole('button',{name:'读取当前正文',exact:true}).click();
  await expect(panel.getByLabel('本页校核正文',{exact:true})).toHaveValue('First source page');

  step = 'real current-page PNG and responsive proofreading layout';
  await expectPreview(panel,1);
  const previewRegion = panel.getByRole('region',{name:'原 PDF 单页预览',exact:true});
  const originalBox = await previewRegion.boundingBox();
  const textBox = await panel.getByLabel('本页校核正文',{exact:true}).boundingBox();
  expect(originalBox.x+originalBox.width).toBeLessThanOrEqual(textBox.x);
  await panel.getByRole('button',{name:'放大原件',exact:true}).click();
  await expect(panel.getByRole('button',{name:'适合宽度',exact:true})).toBeVisible();
  await panel.getByRole('button',{name:'适合宽度',exact:true}).click();
  await page.setViewportSize({width:390,height:844});
  const narrowOriginal = await previewRegion.boundingBox();
  const narrowText = await panel.getByLabel('本页校核正文',{exact:true}).boundingBox();
  expect(narrowOriginal.y+narrowOriginal.height).toBeLessThanOrEqual(narrowText.y);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.setViewportSize({width:1440,height:1000});

  step = 'author corrects pages and maps nested chapters';
  await panel.getByLabel('本页已与原件核对',{exact:true}).check();
  await panel.getByLabel('校核页码',{exact:true}).selectOption('2');
  await expect(panel.getByLabel('本页校核正文',{exact:true})).toHaveValue('');
  await expectPreview(panel,2);
  await panel.getByLabel('本页校核正文',{exact:true}).fill('人工核对：原 PDF 本页为空白');
  await panel.getByLabel('本页已与原件核对',{exact:true}).check();
  await panel.getByLabel('校核页码',{exact:true}).selectOption('3');
  await expect(panel.getByLabel('本页校核正文',{exact:true})).toHaveValue('Third source page');
  await expectPreview(panel,3);
  await panel.getByLabel('本页已与原件核对',{exact:true}).check();
  await panel.getByRole('button',{name:'新增章节',exact:true}).click();
  for (const [label,value] of Object.entries({'章节1编号':'CH1','章节1名称':'第一章','章节1起始页':'1','章节1结束页':'3'}))
    await panel.getByLabel(label,{exact:true}).fill(value);
  await panel.getByRole('button',{name:'新增章节',exact:true}).click();
  for (const [label,value] of Object.entries({'章节2编号':'CH1_1','章节2名称':'第一节','章节2起始页':'1','章节2结束页':'2'}))
    await panel.getByLabel(label,{exact:true}).fill(value);
  await panel.getByLabel('章节2父章节',{exact:true}).selectOption('CH1');
  await panel.getByLabel('正文操作说明',{exact:true}).fill('逐页核对正文并标注章节物理页');
  await panel.getByRole('button',{name:'保存正文与章节',exact:true}).click();
  await expect(panel).toContainText('内容 v2');
  await panel.getByRole('button',{name:'读取当前正文',exact:true}).click();
  await panel.getByLabel('正文操作说明',{exact:true}).fill('三页与章节校核完成，提交独立审核');
  await panel.getByRole('button',{name:'提交独立审核',exact:true}).click();
  await expect(panel).toContainText('内容 v3');
  await expect(panel).toContainText('您参与过本内容的编辑');
  await expect(panel.getByRole('button',{name:'审核通过并发布',exact:true})).toHaveCount(0);

  step = 'independent reviewer samples mandatory pages and publishes';
  await reviewDetail.getByRole('button',{name:'刷新详情',exact:true}).click();
  const reviewPanel = await openContent(reviewDetail);
  await reviewPanel.getByRole('button',{name:'读取当前正文',exact:true}).click();
  await expect(reviewPanel.getByRole('button',{name:'审核通过并发布',exact:true})).toBeDisabled();
  for (const n of [1,2,3]) {
    await reviewPanel.getByLabel('校核页码',{exact:true}).selectOption(String(n));
    await expectPreview(reviewPanel,n);
    await expect(reviewPanel.getByLabel('本页校核正文',{exact:true})).toHaveValue([
      'First source page','人工核对：原 PDF 本页为空白','Third source page'][n-1]);
    await expect(reviewPanel.getByLabel('本页校核正文',{exact:true})).toHaveAttribute('readonly','');
    await reviewPanel.getByLabel(`抽检第${n}页已核对`,{exact:true}).check();
    if (n < 3) await expect(reviewPanel.getByRole('button',{name:'审核通过并发布',exact:true})).toBeDisabled();
  }
  await reviewPanel.getByLabel('正文操作说明',{exact:true}).fill('独立抽检首尾、原空白及修改页通过');
  await reviewPanel.getByRole('button',{name:'审核通过并发布',exact:true}).click();
  await expect(reviewPanel).toContainText('内容 v4');
  await expect(reviewPanel.getByRole('button',{name:'下载已发布 TXT',exact:true})).toBeVisible();

  step = 'operator reads and downloads published TXT without write controls';
  const operator = await login(accounts[2]);
  const readPanel = await openContent(await openEdition(operator.page));
  await readPanel.getByRole('button',{name:'读取当前正文',exact:true}).click();
  await expect(readPanel.getByLabel('本页校核正文',{exact:true})).toHaveValue('First source page');
  await expectPreview(readPanel,1);
  await expect(readPanel.getByLabel('本页校核正文',{exact:true})).toHaveAttribute('readonly','');
  for (const name of ['保存正文与章节','提交独立审核','审核通过并发布','撤回内容'])
    await expect(readPanel.getByRole('button',{name,exact:true})).toHaveCount(0);
  const downloaded = operator.page.waitForEvent('download');
  await readPanel.getByRole('button',{name:'下载已发布 TXT',exact:true}).click();
  const download = await downloaded;
  expect(download.suggestedFilename()).toBe(`${code}-v4.txt`);
  expect(await readFile(await download.path(),'utf8')).toBe(
    '[PDF page 1]\nFirst source page\n\n[PDF page 2]\n人工核对：原 PDF 本页为空白\n\n[PDF page 3]\nThird source page');

  step = 'author withdraws and reads immutable original revision';
  await panel.getByRole('button',{name:'刷新当前版本',exact:true}).click();
  await expect(panel).toContainText('内容 v4');
  await panel.getByLabel('正文操作说明',{exact:true}).fill('教学内容待修订，撤回发布版本');
  await panel.getByRole('button',{name:'撤回内容',exact:true}).click();
  await expect(panel).toContainText('内容 v5');
  await expect(panel.getByRole('button',{name:'下载已发布 TXT',exact:true})).toHaveCount(0);
  await panel.getByRole('button',{name:'查看内容历史',exact:true}).click();
  await expect(panel.getByRole('button',{name:/^查看 v\d 正文$/})).toHaveCount(5);
  await panel.getByRole('button',{name:'查看 v1 正文',exact:true}).click();
  await expect(panel).toContainText('历史只读');
  await panel.getByLabel('校核页码',{exact:true}).selectOption('2');
  await expect(panel.getByLabel('本页校核正文',{exact:true})).toHaveValue('');
  await expect(panel.getByLabel('本页校核正文',{exact:true})).toHaveAttribute('readonly','');
  await readPanel.getByRole('button',{name:'刷新当前版本',exact:true}).click();
  await expect(readPanel).toContainText('内容 v5');
  await expect(readPanel.getByRole('button',{name:'下载已发布 TXT',exact:true})).toHaveCount(0);
  step = 'logout and browser errors';
  await logout(operator); await logout(reviewer); await logout(author);
  expect(pageErrors).toBe(0);
  console.log(JSON.stringify({author:'passed',reviewer:'passed',operator:'passed',txt_download:'passed',page_preview:'passed',page_errors:0}));
} catch (error) {
  const reason = String(error?.message).includes('strict mode violation') ? 'ambiguous locator' : 'interaction or assertion';
  const location = String(error?.stack).match(/real-textbook-content\.mjs:\d+:\d+/)?.[0] || 'no source location';
  console.error(`Real textbook browser check failed at ${step} (${reason}; ${location}). No credentials logged.`);
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  if (server) await new Promise(resolve => {server.httpServer.close(resolve);server.httpServer.closeAllConnections();});
}
