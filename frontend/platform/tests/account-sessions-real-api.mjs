// Temporary credentials arrive via stdin and are never written to logs or browser storage.
import { chromium, expect } from '@playwright/test';
import { preview } from 'vite';
let input='';
for await (const chunk of process.stdin) input+=chunk;
const {apiOrigin,accounts,screenshotDirectory}=JSON.parse(input);
let server,browser,step='start preview',pageErrors=0;
try {
  server=await preview({configFile:false,logLevel:'silent',preview:{host:'127.0.0.1',port:0,strictPort:true,
    proxy:{'/api/v1/admin':{target:apiOrigin}}}});
  const origin=`http://127.0.0.1:${server.httpServer.address().port}`;
  browser=await chromium.launch({channel:process.env.PLAYWRIGHT_CHANNEL||'chrome'});
  async function login(account) {
    const context=await browser.newContext({viewport:{width:1440,height:1000}});
    const page=await context.newPage();page.on('pageerror',()=>{pageErrors++;});
    await page.goto(origin);
    await expect(page.locator('.captcha-refresh img')).toBeVisible();
    await page.getByLabel('平台邮箱').fill(account.email);
    await page.getByLabel('登录密码',{exact:true}).fill(account.password);
    await page.getByLabel('验证码').fill('K7M2');
    const response=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/auth/login'));
    await page.getByRole('button',{name:'登录工作台',exact:true}).click();
    const data=await (await response).json();
    expect(typeof data.data?.access_token).toBe('string');
    await expect(page.getByRole('button',{name:'退出登录',exact:true})).toBeVisible();
    return {context,page,token:data.data.access_token};
  }
  async function status(session) {
    return (await session.context.request.get(`${origin}/api/v1/admin/auth/me`,
      {headers:{Authorization:`Bearer ${session.token}`}})).status();
  }
  step='login existing sessions';
  const old=await login(accounts[1]);
  const admin=await login(accounts[0]),page=admin.page;
  await page.getByRole('button',{name:'平台账号',exact:true}).click();
  await page.getByRole('row').filter({hasText:accounts[1].email}).getByRole('button',{name:'管理与历史'}).click();
  const section=page.getByRole('region',{name:'账号会话安全',exact:true});
  await expect(section.getByText('暂无独立撤销记录')).toBeVisible();
  step='commit and lose receipt';
  const writes=[];let receipt;
  await page.route(`**/api/v1/admin/accounts/${accounts[1].id}/session-revocations`,async route=>{
    if(route.request().method()!=='POST'){await route.continue();return;}
    writes.push(route.request().postDataJSON());
    if(writes.length===1){
      const response=await route.fetch();expect(response.status()).toBe(201);
      receipt=(await response.json()).data;await route.abort('failed');
    }else await route.continue();
  });
  await section.getByLabel('撤销原因').fill('真实联调安全核查');
  await section.getByRole('button',{name:'撤销全部会话',exact:true}).click();
  await expect(section.getByText('操作结果尚未确认',{exact:false})).toBeVisible();
  expect(await status(old)).toBe(401);
  step='new login and identical replay';
  const fresh=await login(accounts[1]);
  step='verify fresh session';expect(await status(fresh)).toBe(200);
  step='retry original operation';
  await section.getByRole('button',{name:'原样重试撤销',exact:true}).click();
  step='verify replay receipt';
  await expect(section.getByRole('status')).toContainText('已撤销有效会话 1 个');
  expect(writes).toHaveLength(2);expect(writes[1]).toEqual(writes[0]);
  step='verify receipt identifier';
  await expect(section.getByText(receipt.operation_id,{exact:true})).toBeVisible();
  step='verify fresh session survives';
  expect(await status(fresh)).toBe(200);
  step='responsive receipt and private browser storage';
  await section.scrollIntoViewIfNeeded();
  await page.screenshot({path:`${screenshotDirectory}/sessions-wide.png`});
  await page.setViewportSize({width:390,height:844});
  await section.scrollIntoViewIfNeeded();
  await page.screenshot({path:`${screenshotDirectory}/sessions-mobile.png`});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  expect(await page.evaluate(()=>({local:{...localStorage},session:{...sessionStorage},cookie:document.cookie})))
    .toEqual({local:{},session:{},cookie:''});
  expect(pageErrors).toBe(0);
  console.log(JSON.stringify({page_errors:pageErrors,old_session:401,new_session:200,attempts:writes.length}));
}catch(error){
  console.error(`Session browser failed at ${step}. No credentials logged.`);process.exitCode=1;
}finally{
  if(browser)await browser.close();
  if(server)await new Promise(resolve=>{server.httpServer.close(resolve);server.httpServer.closeAllConnections();});
}
