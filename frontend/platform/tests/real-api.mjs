// Only receives disposable test credentials over stdin; never logs bodies/tokens or persists them.
import { chromium, expect } from '@playwright/test';
import { preview } from 'vite';

let input = '';
for await (const chunk of process.stdin) input += chunk;
const { apiOrigin, accounts, reviewer, modelKey } = JSON.parse(input);
const schoolCode = modelKey.toUpperCase();
let server, browser;
let step = 'start preview';
try {
  server = await preview({ configFile:false, logLevel:'silent', preview:{
    host:'127.0.0.1', port:0, strictPort:true,
    proxy:{'/api/v1/admin':{target:apiOrigin}},
  }});
  browser = await chromium.launch({channel:process.env.PLAYWRIGHT_CHANNEL || 'chrome'});
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  let pageErrors = 0;
  for (const [index,account] of accounts.entries()) {
    const role = index === 0 ? 'security_admin' : 'operator';
    step = `${role}: login`;
    const context = await browser.newContext();
    const page = await context.newPage();
    page.on('pageerror', () => { pageErrors++; });
    const restricted = [];
    page.on('request', request => {
      if (/\/admin\/(accounts|audit-events)\?/.test(request.url())) restricted.push(true);
    });
    await page.goto(origin);
    await expect(page.locator('.captcha-refresh img')).toBeVisible();
    await page.getByLabel('平台邮箱').fill(account.email);
    await page.getByLabel('登录密码',{exact:true}).fill(account.password);
    await page.getByLabel('验证码').fill('K7M2');
    await page.getByRole('button',{name:'登录工作台',exact:true}).click();
    await expect(page.getByRole('heading',{name:'账号与会话'})).toBeVisible();
    await expect(page.getByText(account.email,{exact:true})).toBeVisible();
    expect(await page.evaluate(() => ({local:{...localStorage},session:{...sessionStorage},cookie:document.cookie})))
      .toEqual({local:{},session:{},cookie:''});
    step = `${role}: roles`;
    await page.getByRole('button',{name:'角色权限',exact:true}).click();
    await page.getByRole('button',{name:'安全管理员',exact:true}).click();
    await page.getByRole('button',{name:'显示权限标识',exact:true}).click();
    await expect(page.locator('.perm-matrix').getByRole('row',{name:/平台账号/}).locator('[data-granted="true"]',{hasText:'platform.accounts.read'})).toBeVisible();
    step = `${role}: model directory`;
    await page.getByRole('button',{name:'模型管理',exact:true}).click();
    await expect(page.getByRole('heading',{name:'模型目录',exact:true})).toBeVisible();
    if (index === 0) {
      step = 'security_admin: register model';
      await page.getByRole('button',{name:'登记模型',exact:true}).click();
      let dialog = page.getByRole('dialog',{name:'登记模型'});
      for (const [name,value] of Object.entries({'显示名称':'真实联调视觉模型','供应商标识':'browser-test',
        '模型标识':modelKey,'固定版本':'2026-10-01','部署区域':'cn-east','变更原因':'隔离数据库浏览器登记'}))
        await dialog.getByLabel(name,{exact:true}).fill(value);
      await dialog.getByLabel('结构化识别',{exact:true}).check();
      await dialog.getByRole('button',{name:'保存登记'}).click();
      step = 'security_admin: model registration response';
      await expect(dialog).not.toBeVisible();
      step = 'security_admin: model search';
      await page.getByLabel('搜索模型').fill(modelKey);
      await page.getByRole('button',{name:'查询',exact:true}).click();
      step = 'security_admin: filtered model detail';
      await expect(page.locator('.model-list')).toHaveAttribute('aria-busy','false');
      const row = page.getByRole('article').filter({hasText:modelKey});
      await expect(row).toHaveCount(1);
      await row.getByRole('button',{name:'查看详情',exact:true}).click();
      let detail = page.getByRole('region',{name:'模型详情'});
      await expect(detail.locator('.model-details').getByText('目录停用',{exact:true})).toBeVisible();
      await expect(detail.locator('.model-details').getByText('配置停用',{exact:true})).toBeVisible();
      await expect(detail.getByText('—（未完成真实评测）',{exact:true})).toBeVisible();
      await detail.getByRole('button',{name:'创建修订',exact:true}).click();
      dialog = page.getByRole('dialog',{name:'创建模型修订'});
      await dialog.getByRole('combobox',{name:'合同状态',exact:true}).selectOption('approved');
      await dialog.getByLabel('合同引用',{exact:true}).fill('BROWSER-CONTRACT');
      await dialog.getByLabel('合同有效至',{exact:true}).fill('2099-01-01');
      await dialog.getByRole('combobox',{name:'密钥操作',exact:true}).selectOption('replace');
      await dialog.getByLabel('密钥服务引用',{exact:true}).fill('secret://providers/browser-test/primary');
      await dialog.getByLabel('目录启用',{exact:true}).check();
      await dialog.getByLabel('变更原因',{exact:true}).fill('合同核验与密钥引用配置；不代表生产准入');
      step = 'security_admin: model revision';
      await dialog.getByRole('button',{name:'保存修订'}).click();
      await expect(dialog).not.toBeVisible();
      detail = page.getByRole('region',{name:'模型详情'});
      await expect(detail.getByText('v2',{exact:true})).toHaveCount(2);
      await expect(detail.locator('.revision-list li')).toHaveCount(2);
      await expect(detail.getByText('已配置（不回显）',{exact:true})).toBeVisible();
      await expect(detail.locator('.model-details').getByText('目录可用',{exact:true})).toBeVisible();
      await expect(detail.locator('.model-details').getByText('配置启用',{exact:true})).toBeVisible();
      await expect(detail.getByText('不可生产（未完成真实评测）',{exact:true})).toBeVisible();
      await expect(detail.locator('.revision-list').getByText('当前判断：目录可用',{exact:false})).toBeVisible();
      await expect(detail.locator('.revision-list').getByText('当前判断：目录停用',{exact:false})).toBeVisible();
      await expect(detail.getByText('—（未完成真实评测）',{exact:true})).toBeVisible();
      expect(await page.evaluate(() => ({local:{...localStorage},session:{...sessionStorage}})))
        .toEqual({local:{},session:{}});
    } else {
      await page.getByLabel('搜索模型').fill(modelKey);
      await page.getByRole('button',{name:'查询',exact:true}).click();
      await expect(page.getByText('真实联调视觉模型',{exact:true})).toBeVisible();
      await expect(page.getByRole('button',{name:'登记模型',exact:true})).toHaveCount(0);
      await expect(page.locator('.model-list')).toHaveAttribute('aria-busy','false');
      const row = page.getByRole('article').filter({hasText:modelKey});
      await expect(row).toHaveCount(1);
      await row.getByRole('button',{name:'查看详情',exact:true}).click();
      const detail = page.getByRole('region',{name:'模型详情'});
      await expect(detail.locator('.revision-list li')).toHaveCount(2);
      await expect(detail.getByRole('button',{name:'创建修订',exact:true})).toHaveCount(0);
      await expect(detail.locator('.model-details').getByText('目录可用',{exact:true})).toBeVisible();
      await expect(detail.getByText('不可生产（未完成真实评测）',{exact:true})).toBeVisible();
    }
    step = `${role}: textbook directory`;
    await page.getByRole('button',{name:'教材管理',exact:true}).click();
    await expect(page.getByRole('heading',{name:'教材与权利登记',exact:true})).toBeVisible();
    if (index === 0) {
      step = 'security_admin: register textbook';
      await page.getByRole('button',{name:'登记教材',exact:true}).click();
      let dialog = page.getByRole('dialog',{name:'登记教材',exact:true});
      for (const [label,value] of Object.entries({'教材代码':schoolCode,'教材名称':'真实联调数学',
        '年级':'3','科目':'数学','出版社':'联调出版社','版本标识':'2026版','学年':'2026-2027','操作原因':'真实联调登记教材'}))
        await dialog.getByLabel(label,{exact:true}).fill(value);
      await dialog.getByLabel('已具备权利依据',{exact:true}).check();
      for (const [label,value] of Object.entries({'权利人':'联调出版社','权利凭据引用':'CONTRACT-BROWSER-001',
        '授权地区代码':'CN, CN-33','授权起始日期':'2020-01-01','授权截止日期':'2099-12-31'}))
        await dialog.getByLabel(label,{exact:true}).fill(value);
      await dialog.getByLabel('OCR 解析',{exact:true}).check();
      await dialog.getByRole('button',{name:'保存草稿',exact:true}).click();await expect(dialog).not.toBeVisible();
      const detail = page.getByRole('region',{name:'教材详情',exact:true});
      await expect(detail.locator('.school-history li')).toHaveCount(1);
      await detail.getByRole('button',{name:'提交审核',exact:true}).click();
      dialog=page.getByRole('dialog',{name:'提交审核',exact:true});
      await dialog.getByLabel('操作原因',{exact:true}).fill('真实联调提交授权审核');
      await dialog.getByRole('button',{name:'提交审核',exact:true}).click();await expect(dialog).not.toBeVisible();
      await expect(detail.getByRole('button',{name:'批准权利',exact:true})).toBeDisabled();
      await expect(detail.getByRole('button',{name:'驳回申请',exact:true})).toBeDisabled();
      step = 'reviewer: independent textbook approval';
      const reviewContext=await browser.newContext(),reviewPage=await reviewContext.newPage();
      reviewPage.on('pageerror',()=>{pageErrors++;});
      await reviewPage.goto(origin);
      await expect(reviewPage.locator('.captcha-refresh img')).toBeVisible();
      await reviewPage.getByLabel('平台邮箱').fill(reviewer.email);
      await reviewPage.getByLabel('登录密码',{exact:true}).fill(reviewer.password);
      await reviewPage.getByLabel('验证码').fill('K7M2');
      await reviewPage.getByRole('button',{name:'登录工作台',exact:true}).click();
      await reviewPage.getByRole('button',{name:'教材管理',exact:true}).click();
      await reviewPage.getByRole('button',{name:`查看教材 ${schoolCode}`,exact:true}).click();
      await reviewPage.getByRole('button',{name:'批准权利',exact:true}).click();
      const reviewDialog=reviewPage.getByRole('dialog',{name:'批准权利',exact:true});
      await reviewDialog.getByLabel('操作原因',{exact:true}).fill('独立核对授权期限、用途与地区');
      await reviewDialog.getByRole('button',{name:'批准权利',exact:true}).click();await expect(reviewDialog).not.toBeVisible();
      await expect(reviewPage.getByRole('region',{name:'教材详情',exact:true})).toContainText('修改已批准档案需先撤回');
      await reviewPage.getByRole('button',{name:'退出登录',exact:true}).click();
      await expect(reviewPage.getByRole('heading',{name:'登录平台工作台'})).toBeVisible();await reviewContext.close();
      step = 'security_admin: refresh approved textbook';
      await detail.getByRole('button',{name:'刷新详情',exact:true}).click();
      await expect(detail.locator('.school-history li')).toHaveCount(3);
      await expect(detail.getByRole('button',{name:'修订教材 / 权利',exact:true})).toHaveCount(0);
      step = 'security_admin: receive authorized PDF into quarantine';
      const documents=detail.getByRole('region',{name:'教材 PDF 文件',exact:true});
      await documents.getByLabel('教材 PDF（最大 50 MiB）',{exact:true}).setInputFiles({
        name:'real-textbook.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.7\nreal browser isolation check\n%%EOF')});
      await documents.getByLabel('PDF 接收原因',{exact:true}).fill('真实联调接收授权教材原件');
      await documents.getByRole('button',{name:'接收 PDF',exact:true}).click();
      await expect(documents).toContainText('PDF 已接收并隔离');
      await expect(documents.locator('.document-record')).toContainText('已隔离接收');
      await expect(documents.getByRole('link')).toHaveCount(0);
      step = 'security_admin: request durable textbook processing';
      const processing=documents.getByRole('region',{name:'real-textbook.pdf 的处理任务',exact:true});
      await processing.getByRole('button',{name:'查看处理任务',exact:true}).click();
      await expect(processing).toContainText('暂无处理任务');
      await processing.getByLabel('处理原因',{exact:true}).fill('真实联调申请扫描与文本提取');
      await processing.getByRole('button',{name:'申请扫描与文本提取',exact:true}).click();
      await expect(processing).toContainText('处理申请已登记');
      await processing.getByRole('button',{name:'刷新任务',exact:true}).click();
      await expect(processing).toContainText('等待处理');
      await processing.getByRole('button',{name:'查看任务详情',exact:true}).click();
      await expect(processing.getByLabel('处理任务详情',{exact:true})).toContainText('尚未完成');
      await expect(processing.getByRole('button',{name:'申请扫描与文本提取',exact:true})).toBeDisabled();
      step = 'security_admin: withdraw and revise approved textbook';
      await detail.getByRole('button',{name:'撤回版本',exact:true}).click();
      dialog=page.getByRole('dialog',{name:'撤回版本',exact:true});
      await dialog.getByLabel('操作原因',{exact:true}).fill('教材名称需要修订');
      await dialog.getByRole('button',{name:'撤回版本',exact:true}).click();await expect(dialog).not.toBeVisible();
      await detail.getByRole('button',{name:'修订教材 / 权利',exact:true}).click();
      dialog=page.getByRole('dialog',{name:'修订教材',exact:true});
      await dialog.getByLabel('教材名称',{exact:true}).fill('真实联调数学修订版');
      await dialog.getByLabel('操作原因',{exact:true}).fill('修订教材元数据');
      await dialog.getByRole('button',{name:'保存草稿',exact:true}).click();await expect(dialog).not.toBeVisible();
      await detail.getByRole('button',{name:'提交审核',exact:true}).click();
      dialog=page.getByRole('dialog',{name:'提交审核',exact:true});
      await dialog.getByLabel('操作原因',{exact:true}).fill('重新提交修订版审核');
      await dialog.getByRole('button',{name:'提交审核',exact:true}).click();await expect(dialog).not.toBeVisible();
      await expect(detail.locator('.school-history li')).toHaveCount(6);
    } else {
      await page.getByLabel('搜索教材').fill(schoolCode);await page.getByRole('button',{name:'查询',exact:true}).click();
      await page.getByRole('button',{name:`查看教材 ${schoolCode}`,exact:true}).click();
      const detail=page.getByRole('region',{name:'教材详情',exact:true});
      await expect(detail.locator('.school-history li')).toHaveCount(6);
      await expect(detail).toContainText('CONTRACT-BROWSER-001');
      await expect(page.getByRole('button',{name:'登记教材',exact:true})).toHaveCount(0);
      for (const name of ['修订教材 / 权利','提交审核','批准权利','驳回申请','撤回版本'])
        await expect(detail.getByRole('button',{name,exact:true})).toHaveCount(0);
    }
    step = `${role}: school directory`;
    await page.getByRole('button',{name:'学校管理',exact:true}).click();
    await expect(page.getByRole('heading',{name:'学校档案',exact:true})).toBeVisible();
    if (index === 0) {
      step = 'security_admin: register school';
      await page.getByRole('button',{name:'登记学校',exact:true}).click();
      let dialog = page.getByRole('dialog',{name:'登记学校',exact:true});
      for (const [label,value] of Object.entries({'学校代码':schoolCode,'学校名称':'真实联调实验学校',
        '所在地区':'上海市 / 浦东新区','学年':'2026-2027','变更原因':'真实联调登记'}))
        await dialog.getByLabel(label,{exact:true}).fill(value);
      await dialog.getByRole('button',{name:'保存登记',exact:true}).click();
      await expect(dialog).not.toBeVisible();
      let detail = page.getByRole('region',{name:'学校详情'});
      await expect(detail.locator('.school-history li')).toHaveCount(1);
      step = 'security_admin: register grades and class';
      const structure = page.getByRole('region',{name:'学校组织结构',exact:true});
      await expect(structure).toHaveAttribute('aria-busy','false');
      async function registerNode(code,name,kind,parentLabel) {
        await structure.getByRole('button',{name:'登记组织',exact:true}).click();
        const form = page.getByRole('dialog',{name:'登记组织',exact:true});
        await form.getByLabel('组织代码',{exact:true}).fill(code);
        await form.getByLabel('组织名称',{exact:true}).fill(name);
        await form.getByRole('combobox',{name:'组织类型',exact:true}).selectOption(kind);
        if (parentLabel) await form.getByRole('combobox',{name:'上级组织',exact:true}).selectOption({label:parentLabel});
        await form.getByLabel('组织变更原因',{exact:true}).fill('真实联调组织登记');
        await form.getByRole('button',{name:'保存组织登记',exact:true}).click();
        await expect(form).not.toBeVisible();
        await expect(structure.getByRole('region',{name:'组织详情',exact:true})).toContainText(name);
      }
      await registerNode('GRADE_A','联调一年级','grade');
      await registerNode('GRADE_B','联调二年级','grade');
      await registerNode('CLASS_A','联调一班','class','年级 · 联调一年级（GRADE_A）');
      step = 'security_admin: move class to another grade';
      await structure.getByRole('button',{name:'编辑组织 / 移动 / 启停',exact:true}).click();
      let nodeForm = page.getByRole('dialog',{name:'编辑组织',exact:true});
      await nodeForm.getByRole('combobox',{name:'上级组织',exact:true}).selectOption({label:'年级 · 联调二年级（GRADE_B）'});
      await nodeForm.getByLabel('组织变更原因',{exact:true}).fill('真实联调班级归属调整');
      await nodeForm.getByRole('button',{name:'保存组织变更',exact:true}).click();
      await expect(nodeForm).not.toBeVisible();
      let nodeDetail = structure.getByRole('region',{name:'组织详情',exact:true});
      await expect(nodeDetail.locator('.structure-history li')).toHaveCount(2);
      await expect(nodeDetail.locator('.details')).toContainText('联调二年级');
      step = 'security_admin: register student, transfer class and pause enrollment';
      await registerNode('CLASS_B','联调二班','class','年级 · 联调二年级（GRADE_B）');
      const roster = page.getByRole('region',{name:'学校学生名册',exact:true});
      await roster.getByRole('button',{name:'查询名册',exact:true}).click();
      await expect(roster).toHaveAttribute('aria-busy','false');
      await roster.getByRole('button',{name:'登记学生',exact:true}).click();
      let studentForm = page.getByRole('dialog',{name:'登记学生',exact:true});
      await studentForm.getByLabel('学生代码',{exact:true}).fill('STU_WEB_01');
      await studentForm.getByLabel('学生显示名',{exact:true}).fill('联调学生');
      await studentForm.getByRole('combobox',{name:'所属班级',exact:true}).selectOption({label:'联调二年级 · 联调一班（CLASS_A）'});
      await studentForm.getByLabel('在读变更原因',{exact:true}).fill('真实联调学生登记');
      await studentForm.getByRole('button',{name:'保存学生登记',exact:true}).click();
      await expect(studentForm).not.toBeVisible();
      const studentDetail = roster.getByRole('region',{name:'学生在读详情',exact:true});
      await expect(studentDetail.locator('.roster-history li')).toHaveCount(1);
      await studentDetail.getByRole('button',{name:'编辑学生 / 调班 / 状态',exact:true}).click();
      studentForm = page.getByRole('dialog',{name:'编辑学生在读信息',exact:true});
      await studentForm.getByRole('combobox',{name:'所属班级',exact:true}).selectOption({label:'联调二年级 · 联调二班（CLASS_B）'});
      await studentForm.getByLabel('在读变更原因',{exact:true}).fill('真实联调学生调班');
      await studentForm.getByRole('button',{name:'保存在读变更',exact:true}).click();
      await expect(studentForm).not.toBeVisible();
      await expect(studentDetail.locator('.roster-history li')).toHaveCount(2);
      await studentDetail.getByRole('button',{name:'编辑学生 / 调班 / 状态',exact:true}).click();
      studentForm = page.getByRole('dialog',{name:'编辑学生在读信息',exact:true});
      await studentForm.getByRole('combobox',{name:'学生状态',exact:true}).selectOption('paused');
      await studentForm.getByRole('combobox',{name:'所属班级',exact:true}).selectOption('');
      await studentForm.getByLabel('在读变更原因',{exact:true}).fill('真实联调学生休学');
      await studentForm.getByRole('button',{name:'保存在读变更',exact:true}).click();
      await expect(studentForm).not.toBeVisible();
      await expect(studentDetail.locator('.roster-history li')).toHaveCount(3);
      await expect(studentDetail.locator('.details')).toContainText('未分班');
      step = 'security_admin: disable grade and verify descendant effective state';
      await structure.getByRole('button',{name:/年级 联调二年级 GRADE_B/}).click();
      await structure.getByRole('button',{name:'编辑组织 / 移动 / 启停',exact:true}).click();
      nodeForm = page.getByRole('dialog',{name:'编辑组织',exact:true});
      await nodeForm.getByRole('combobox',{name:'组织自身状态',exact:true}).selectOption('disabled');
      await nodeForm.getByLabel('组织变更原因',{exact:true}).fill('真实联调停用年级');
      await nodeForm.getByRole('button',{name:'保存组织变更',exact:true}).click();
      await expect(nodeForm).not.toBeVisible();
      await structure.getByRole('button',{name:/班级 联调一班 CLASS_A 停用/}).click();
      nodeDetail = structure.getByRole('region',{name:'组织详情',exact:true});
      await expect(nodeDetail.locator('.details')).toContainText('已停用（自身、学校或上级组织停用）');
      await expect(nodeDetail.locator('.structure-history li')).toHaveCount(2);
      await detail.getByRole('button',{name:'编辑学校 / 启停',exact:true}).click();
      dialog = page.getByRole('dialog',{name:'编辑学校',exact:true});
      await expect(dialog.getByLabel('学校代码',{exact:true})).toBeDisabled();
      await dialog.getByLabel('学校名称',{exact:true}).fill('真实联调实验学校（修订）');
      await dialog.getByLabel('学年',{exact:true}).fill('2027-2028');
      await dialog.getByRole('combobox',{name:'档案状态',exact:true}).selectOption('disabled');
      await dialog.getByLabel('变更原因',{exact:true}).fill('真实联调修订与停用');
      step = 'security_admin: revise school';
      await dialog.getByRole('button',{name:'保存变更',exact:true}).click();
      await expect(dialog).not.toBeVisible();
      detail = page.getByRole('region',{name:'学校详情'});
      await expect(detail.getByRole('heading',{name:'真实联调实验学校（修订）',exact:true})).toBeVisible();
      await expect(detail.locator('.school-history li')).toHaveCount(2);
      await expect(detail.locator('.school-history li').first()).toContainText('v2 · 已停用');
      await expect(detail.locator('.school-history li').last()).toContainText('真实联调登记');
    } else {
      step = 'operator: read school history';
      await page.getByLabel('搜索学校',{exact:true}).fill(schoolCode);
      await page.getByRole('button',{name:'查询',exact:true}).click();
      await expect(page.locator('.school-list')).toHaveAttribute('aria-busy','false');
      const row = page.getByRole('article').filter({hasText:schoolCode});
      await expect(row).toHaveCount(1);
      await expect(row).toContainText('真实联调实验学校（修订）');
      await row.getByRole('button',{name:'查看详情',exact:true}).click();
      const detail = page.getByRole('region',{name:'学校详情'});
      await expect(detail.locator('.school-history li')).toHaveCount(2);
      await expect(detail.locator('.school-history li').first()).toContainText('v2 · 已停用');
      await expect(page.getByRole('button',{name:'登记学校',exact:true})).toHaveCount(0);
      await expect(detail.getByRole('button',{name:'编辑学校 / 启停',exact:true})).toHaveCount(0);
      step = 'operator: read historical school structure and class revisions';
      const structure = page.getByRole('region',{name:'学校组织结构',exact:true});
      await expect(structure).toHaveAttribute('aria-busy','false');
      await structure.getByLabel('组织学年',{exact:true}).fill('2026-2027');
      await structure.getByRole('button',{name:'查询组织',exact:true}).click();
      await expect(structure.getByRole('button',{name:/班级 联调一班 CLASS_A 停用/})).toBeVisible();
      await structure.getByRole('button',{name:/班级 联调一班 CLASS_A 停用/}).click();
      const nodeDetail = structure.getByRole('region',{name:'组织详情',exact:true});
      await expect(nodeDetail.locator('.structure-history li')).toHaveCount(2);
      await expect(nodeDetail.locator('.details')).toContainText('联调二年级');
      await expect(nodeDetail.locator('.details')).toContainText('已停用（自身、学校或上级组织停用）');
      await expect(structure.getByRole('button',{name:'登记组织',exact:true})).toHaveCount(0);
      await expect(nodeDetail.getByRole('button',{name:'编辑组织 / 移动 / 启停',exact:true})).toHaveCount(0);
      step = 'operator: read historical student enrollment and revisions';
      const roster = page.getByRole('region',{name:'学校学生名册',exact:true});
      await expect(roster).toHaveAttribute('aria-busy','false');
      await roster.getByLabel('名册学年',{exact:true}).fill('2026-2027');
      await roster.getByRole('button',{name:'查询名册',exact:true}).click();
      await roster.getByRole('button',{name:/联调学生 STU_WEB_01/}).click();
      const studentDetail = roster.getByRole('region',{name:'学生在读详情',exact:true});
      await expect(studentDetail.locator('.roster-history li')).toHaveCount(3);
      await expect(studentDetail.locator('.roster-history li').first()).toContainText('休学');
      await expect(roster.getByRole('button',{name:'登记学生',exact:true})).toHaveCount(0);
      await expect(studentDetail.getByRole('button',{name:'编辑学生 / 调班 / 状态',exact:true})).toHaveCount(0);

    }
    if (index === 0) {
      step = 'security_admin: evaluation dataset';
      await page.getByRole('button',{name:'评测数据集',exact:true}).click();
      await expect(page.getByRole('region',{name:'评测数据集列表'})).toBeVisible();
      await page.getByRole('button',{name:'新建数据集',exact:true}).click();
      const evaluationDialog=page.getByRole('dialog',{name:'新建评测数据集'});
      await evaluationDialog.getByLabel('数据集名称',{exact:true}).fill(`真实联调评测-${modelKey}`);
      await evaluationDialog.getByLabel('数据分组').selectOption('validation');
      await evaluationDialog.getByLabel('标注手册版本').fill('browser-handbook-v1');
      await evaluationDialog.getByRole('button',{name:'提交保存'}).click();
      await expect(evaluationDialog).not.toBeVisible();
      let evaluationDetail=page.getByRole('region',{name:'评测数据集详情'});
      await expect(evaluationDetail.getByRole('heading',{name:`真实联调评测-${modelKey}`,exact:true})).toBeVisible();
      await expect(evaluationDetail.getByRole('button',{name:'检查并锁定'})).toBeDisabled();
      await page.getByRole('button',{name:'刷新数据集列表'}).click();
      await page.getByRole('region',{name:'评测数据集列表'}).getByRole('button',{name:new RegExp(modelKey)}).click();
      evaluationDetail=page.getByRole('region',{name:'评测数据集详情'});
      await expect(evaluationDetail.getByRole('heading',{name:`真实联调评测-${modelKey}`,exact:true})).toBeVisible();
      step = 'security_admin: accounts';
      await page.getByRole('button',{name:'平台账号',exact:true}).click();
      const targetRow = page.getByRole('row').filter({hasText:accounts[1].email});
      await expect(targetRow).toBeVisible();
      await targetRow.getByRole('button',{name:'管理与历史',exact:true}).click();
      const governance = page.getByRole('dialog',{name:'平台账号管理',exact:true});
      await expect(governance.getByRole('heading',{name:accounts[1].email,exact:true})).toBeVisible();
      for (const [revision,status,reason] of [[1,'disabled','真实联调停用'],[2,'active','真实联调重新启用']]) {
        step = `security_admin: account ${status}`;
        await governance.getByRole('combobox',{name:'账号状态',exact:true}).selectOption(status);
        await governance.getByLabel('变更原因',{exact:true}).fill(reason);
        await governance.getByRole('button',{name:'保存账号变更',exact:true}).click();
        step = `security_admin: account ${status} revision response`;
        await expect(governance.getByText(`基于版本 ${revision} 提交`,{exact:true})).toBeVisible();
        step = `security_admin: account ${status} history response`;
        await expect(governance.getByRole('article').locator('.governance-reason').filter({hasText:reason})).toHaveCount(1);
      }
      step = 'security_admin: request independent promotion';
      await governance.getByRole('combobox',{name:'账号角色',exact:true}).selectOption('security_admin');
      await governance.getByLabel('变更原因',{exact:true}).fill('真实联调提交独立升权审批');
      await governance.getByRole('button',{name:'提交权限申请',exact:true}).click();
      await expect(governance.getByText('待独立审批',{exact:true})).toBeVisible();
      await expect(governance.getByText('由另一位有审批权限的管理员处理',{exact:true})).toBeVisible();
      await expect(governance.getByRole('button',{name:'批准申请',exact:true})).toHaveCount(0);
      await expect(governance.getByText('基于版本 2 提交',{exact:true})).toBeVisible();
      await expect(governance.getByRole('combobox',{name:'账号角色',exact:true})).toHaveValue('operator');
      await expect(governance.getByRole('article')).toHaveCount(3);
      await governance.getByRole('button',{name:'关闭账号管理',exact:true}).click();
      await expect(governance).not.toBeVisible();
      step = 'security_admin: audit';
      await page.getByRole('button',{name:'安全审计',exact:true}).click();
      const loginEvent=page.getByRole('row').filter({hasText:account.id})
        .getByText('登录成功',{exact:true});
      await expect(page.getByText(/^第 1 页 · 本页 \d+ 条$/)).toBeVisible();
      // Registration and revision events can move the original login beyond the first page.
      for (let auditPage=1; auditPage<10 && !await loginEvent.count(); auditPage++) {
        const next=page.getByRole('button',{name:'下一页',exact:true});
        if (!await next.isEnabled()) break;
        step = `security_admin: audit page ${auditPage+1}`;
        await next.click();
        await expect(page.getByText(new RegExp(`^第 ${auditPage+1} 页 · 本页 \\d+ 条$`))).toBeVisible();
      }
      await expect(loginEvent).toBeVisible();
      step = 'security_admin: revoke all';
      await page.getByRole('button',{name:'账号与会话',exact:true}).click();
      await page.getByRole('button',{name:'退出全部会话'}).click();
      await page.getByRole('button',{name:'确认退出全部'}).click();
    } else {
      step = 'operator: restricted navigation and logout';
      await expect(page.getByRole('button',{name:'评测数据集',exact:true})).toHaveCount(0);
      await expect(page.getByRole('button',{name:'平台账号',exact:true})).toHaveCount(0);
      await expect(page.getByRole('button',{name:'安全审计',exact:true})).toHaveCount(0);
      expect(restricted).toHaveLength(0);
      await page.getByRole('button',{name:'退出登录',exact:true}).click();
    }
    await expect(page.getByRole('heading',{name:'登录平台工作台'})).toBeVisible();
    await context.close();
  }
  expect(pageErrors).toBe(0);
  console.log(JSON.stringify({security_admin:'passed',operator:'passed',page_errors:pageErrors}));
} catch (error) {
  const reason = String(error?.message).includes('strict mode violation') ? 'ambiguous locator' : 'interaction or assertion';
  console.error(`Real API browser check failed at ${step} (${reason}). No credentials logged.`);
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  if (server) await new Promise(resolve => {server.httpServer.close(resolve); server.httpServer.closeAllConnections();});
}
