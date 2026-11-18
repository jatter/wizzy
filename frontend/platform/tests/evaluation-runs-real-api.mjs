// Disposable credentials arrive on stdin. Never log tokens, request bodies or secrets.
import { chromium, expect } from '@playwright/test';
import { preview } from 'vite';

let input='';
for await (const chunk of process.stdin) input+=chunk;
const {apiOrigin,account,datasetId,modelId,sampleId,screenshotDirectory}=JSON.parse(input);
let server,browser,step='start preview';
try {
  server=await preview({configFile:false,logLevel:'silent',preview:{host:'127.0.0.1',port:0,strictPort:true,
    proxy:{'/api/v1/admin':{target:apiOrigin}}}});
  browser=await chromium.launch({channel:process.env.PLAYWRIGHT_CHANNEL||'chrome'});
  const context=await browser.newContext({viewport:{width:1440,height:1000}});
  const page=await context.newPage();let pageErrors=0;
  page.on('pageerror',()=>{pageErrors++;});
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}`);
  step='login';
  await expect(page.locator('.captcha-refresh img')).toBeVisible();
  await page.getByLabel('平台邮箱').fill(account.email);
  await page.getByLabel('登录密码',{exact:true}).fill(account.password);
  await page.getByLabel('验证码').fill('K7M2');
  await page.getByRole('button',{name:'登录工作台',exact:true}).click();
  await page.getByRole('button',{name:'模型评测',exact:true}).click();
  step='create fixed manifest';
  await page.getByRole('button',{name:'新建评测运行',exact:true}).click();
  let dialog=page.getByRole('dialog',{name:'新建固定版本评测运行'});
  await dialog.getByRole('combobox',{name:'已锁定数据集',exact:true}).selectOption(datasetId);
  await dialog.getByRole('combobox',{name:'模型',exact:true}).selectOption(modelId);
  await dialog.getByRole('combobox',{name:'固定历史修订',exact:true}).selectOption('1');
  await dialog.getByLabel('提示词 SHA-256',{exact:true}).fill('a'.repeat(64));
  await dialog.getByLabel('必要验收分层（JSON）',{exact:true}).fill('[{"grade":3,"question_type":"arithmetic"}]');
  const createdResponse=page.waitForResponse(r=>r.request().method()==='POST' && new URL(r.url()).pathname.endsWith('/evaluation-runs'));
  await dialog.getByRole('button',{name:'建立评测运行',exact:true}).click();
  const created=await (await createdResponse).json();
  expect(created.data?.id).toMatch(/^[a-f0-9-]{36}$/);
  const runId=created.data.id;
  await expect(dialog).not.toBeVisible();
  await expect(page.getByText(runId,{exact:true})).toBeVisible();
  step='import complete predictions';
  await page.getByRole('button',{name:'导入预测',exact:true}).click();
  dialog=page.getByRole('dialog',{name:'导入完整预测'});
  await dialog.getByLabel('离线预测来源标识',{exact:true}).fill('browser-offline-001');
  await dialog.getByLabel('完整预测 JSON 数组',{exact:true}).fill(JSON.stringify([
    {question_id:sampleId,verdict:'incorrect',automatic_release:true},
  ]));
  await dialog.getByRole('button',{name:'确认完整导入',exact:true}).click();
  await expect(dialog).not.toBeVisible();
  await expect(page.getByText('browser-offline-001',{exact:false})).toBeVisible();
  step='compute statistical report';
  const response=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith(`/evaluation-runs/${runId}/report`));
  await page.getByRole('button',{name:'计算统计报告',exact:true}).click();
  const report=await (await response).json();
  expect(report.data.run_id).toBe(runId);
  expect(report.data.report.metrics.selective_accuracy.estimate).toBe(1);
  expect(report.data.report.production_eligible).toBe(false);
  await expect(page.getByRole('heading',{name:'评测统计报告',exact:true})).toBeVisible();
  await expect(page.getByText('尚未取得生产准入',{exact:true})).toBeVisible();
  for (const dimension of ['by_question_type','by_difficulty','by_grade_question_type']) {
    await page.getByLabel('分层维度').selectOption(dimension);
    await expect(page.locator('.run-detail')).toContainText('1 题');
  }
  step='responsive report';
  await page.getByRole('heading',{name:'评测统计报告',exact:true}).scrollIntoViewIfNeeded();
  await page.screenshot({path:`${screenshotDirectory}/evaluation-report-wide.png`,fullPage:false});
  await page.setViewportSize({width:390,height:844});
  await expect(page.getByRole('heading',{name:'评测统计报告',exact:true})).toBeVisible();
  await page.getByRole('heading',{name:'评测统计报告',exact:true}).scrollIntoViewIfNeeded();
  await page.screenshot({path:`${screenshotDirectory}/evaluation-report-mobile.png`,fullPage:false});
  const overflow=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,elements:[...document.querySelectorAll('.run-workspace *')].filter(e=>e.getBoundingClientRect().right>innerWidth).map(e=>({tag:e.tagName,class:e.className,width:Math.round(e.getBoundingClientRect().width)})).slice(0,20)}));
  if(overflow.scroll>overflow.width) console.error('Layout overflow '+JSON.stringify(overflow));
  expect(overflow.scroll<=overflow.width).toBe(true);
  expect(await page.evaluate(()=>({local:{...localStorage},session:{...sessionStorage},cookie:document.cookie})))
    .toEqual({local:{},session:{},cookie:''});
  expect(pageErrors).toBe(0);
  console.log(JSON.stringify({run_id:runId,page_errors:pageErrors,production_eligible:false}));
} catch(error) {
  const reason=String(error?.message).includes('strict mode violation')?'ambiguous locator':'interaction or assertion';
  console.error(`Evaluation browser failed at ${step} (${reason}). No credentials logged.`);
  process.exitCode=1;
} finally {
  if(browser)await browser.close();
  if(server)await new Promise(resolve=>{server.httpServer.close(resolve);server.httpServer.closeAllConnections();});
}
