// Disposable credentials arrive through stdin. Do not log credentials, tokens or API bodies.
import { chromium, expect } from '@playwright/test';
import { preview } from 'vite';

let input = '';
for await (const chunk of process.stdin) input += chunk;
const { apiOrigin, registration, owner } = JSON.parse(input);
let server, browser, step = 'start preview', pageErrors = 0;
const failedRequests = [];
const button = (page, name) => page.getByRole('button', { name, exact: true });
const label = (page, name) => page.getByLabel(name, { exact: true });
async function privateStorage(page) {
  // uni-app can cache its device identifier; session credentials must stay HttpOnly.
  expect(await page.evaluate(() => ({ local: Object.fromEntries(Object.entries(localStorage).filter(([key]) => key !== '__DC_STAT_UUID')), session: { ...sessionStorage }, cookie: document.cookie })))
    .toEqual({ local: {}, session: {}, cookie: '' });
}
async function parentSection(parent, name) {
  if (!await button(parent, name).isVisible()) await button(parent, '家长中心').click();
  await button(parent, name).click();
}
async function pair(parent, child, origin) {
  await parentSection(parent, '授权与设备');
  await button(parent, '生成孩子配对码').click();
  const code = await parent.getByTestId('pair-code').innerText();
  await child.goto(origin);
  await button(child, '连接孩子的学习设备').click();
  await label(child, '一次性配对码').fill(code);
  await button(child, '连接并开始学习').click();
  await expect(button(child, '退出登录')).toBeVisible();
  await button(child, '学习页').click();
  await expect(button(child, '刷新内容')).toBeEnabled();
}
try {
  server = await preview({ configFile: false, logLevel: 'silent', build: { outDir: 'dist/build/h5' }, preview: {
    host: '127.0.0.1', port: 0, strictPort: true, proxy: { '/api/v1': { target: apiOrigin } },
  } });
  browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome' });
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  const parentContext = await browser.newContext();
  const childContext = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const parent = await parentContext.newPage(), child = await childContext.newPage();
  for (const page of [parent, child]) {
    page.on('pageerror', () => { pageErrors++; });
    page.on('response', response => {
      const path = new URL(response.url()).pathname;
      if (path.startsWith('/api/v1/') && response.status() >= 400) failedRequests.push({ path, status: response.status() });
    });
  }

  step = 'registration and household';
  await parent.goto(origin);
  await button(parent, '创建账号').click();
  await label(parent, '用户名').fill(registration.email);
  await label(parent, '登录密码').fill(registration.password);
  await expect(button(parent, '创建账号')).toBeDisabled();
  await label(parent, '我已阅读并同意上述隐私说明').check();
  await button(parent, '创建账号').click();
  step = 'registration accepted and household form';
  await label(parent, '家庭名称').fill('浏览器验收家庭');
  step = 'create household';
  await button(parent, '创建家庭').click();
  step = 'open student management';
  await parentSection(parent, '家庭与学生');
  await label(parent, '学生昵称').fill('小禾');
  step = 'create student profile';
  await button(parent, '创建学生档案').click();
  await expect(button(parent, '开启作业服务授权')).toBeDisabled();

  step = 'student profile revision';
  await parentSection(parent, '家庭与学生');
  await button(parent, '编辑当前学生').click();
  await label(parent, '学生昵称').fill('小禾同学');
  await label(parent, '年级').fill('4');
  await button(parent, '保存学生资料').click();
  const service = label(parent, '我作为监护人，同意该学生使用上述作业服务');
  const evaluation = label(parent, '我自愿单独授权该学生参与上述离线评测');
  await expect(service).not.toBeChecked();
  await expect(evaluation).not.toBeChecked();
  await expect(evaluation).toBeDisabled();

  step = 'independent service and evaluation consents';
  await service.check();
  await button(parent, '开启作业服务授权').click();
  await expect(evaluation).not.toBeChecked();
  await expect(button(parent, '保存可选评测授权')).toBeDisabled();
  await evaluation.check();
  await button(parent, '保存可选评测授权').click();
  await button(parent, '撤回可选评测授权').click();
  await expect(evaluation).not.toBeChecked();
  await expect(button(parent, '生成孩子配对码')).toBeEnabled();

  step = 'child pairing and service withdrawal';
  await pair(parent, child, origin);
  await privateStorage(parent); await privateStorage(child);
  step = 'guardian and child reload restore their sessions';
  await parent.reload(); await child.reload();
  await expect(button(parent, '家长中心')).toBeVisible();
  await button(child, '学习页').click();
  await expect(button(child, '刷新内容')).toBeEnabled();
  await expect(button(child, '家庭与学生')).toHaveCount(0);
  await parentSection(parent, '授权与设备');
  step = 'child pairing and service withdrawal';
  await button(parent, '撤回作业服务授权').click();
  await button(parent, '确认撤回服务授权').click();
  await expect(button(parent, '生成孩子配对码')).toBeDisabled();
  await expect(parent.getByTestId('pair-code')).toHaveCount(0);
  await button(child, '刷新内容').click();
  await expect(button(child, '连接并开始学习')).toBeVisible();
  await expect(child.locator('.task-card')).toHaveCount(0);
  await privateStorage(child);
  await button(parent, '退出登录').click();
  await parent.reload();

  step = 'existing family login and enrollment';
  await label(parent, '用户名').fill(owner.email);
  await label(parent, '登录密码').fill(owner.password);
  await button(parent, '登录').click();
  step = 'existing household selection';
  await button(parent, '学习页').click();
  await expect(parent.getByText('小知，今天从这里开始', { exact: true })).toBeVisible();
  step = 'existing mistake read';
  await button(parent, '错题回看').click();
  await expect(parent.locator('.mistake-card')).toHaveCount(1);
  step = 'empty weekly and monthly reports';
  await parentSection(parent, '学习报告');
  await label(parent, '报告日期').fill('2000-01-03');
  await button(parent, '生成学习报告').click();
  await expect(parent.locator('.report-folio .accuracy').first()).toHaveText('暂无有效样本');
  await expect(parent.locator('.source-row')).toHaveCount(0);
  await button(parent, '月报').click();
  await button(parent, '生成学习报告').click();
  await expect(parent.locator('.report-folio .accuracy').first()).toHaveText('暂无有效样本');
  await expect(parent.locator('.report-history .report-index')).toHaveCount(2);

  step = 'monthly report with real source evidence';
  const reportToday = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(new Date());
  await label(parent, '报告日期').fill(reportToday);
  await button(parent, '生成学习报告').click();
  await expect(parent.locator('.source-row')).not.toHaveCount(0);
  await expect(parent.locator('.report-folio')).toContainText('错误 1');
  const originalSourceCount = await parent.locator('.source-row').count();
  await button(parent, '只看周报').click();
  await expect(parent.locator('.report-history .report-index')).toHaveCount(1);
  await button(parent, '全部报告').click();
  await expect(parent.locator('.report-history .report-index')).toHaveCount(3);

  step = 'committed plan response loss and tab-safe retry';
  let planRequests = 0, firstPlanBody, firstPlanKey;
  await parent.route('**/api/v1/review-plans', async route => {
    const request = route.request();
    if (request.method() !== 'POST') return route.continue();
    planRequests++;
    if (planRequests === 1) {
      firstPlanBody = request.postData(); firstPlanKey = request.headers()['idempotency-key'];
      const response = await route.fetch(); expect(response.status()).toBe(201);
      return route.abort('failed');
    }
    expect(request.postData()).toBe(firstPlanBody);
    expect(request.headers()['idempotency-key']).toBe(firstPlanKey);
    await route.continue();
  });
  await button(parent, '生成复习计划').click();
  await expect(button(parent, '重试生成复习计划')).toBeEnabled();
  await expect(parent.getByRole('alert')).toBeVisible();
  await button(parent, '学习页').click();
  await parentSection(parent, '学习报告');
  await button(parent, '重试生成复习计划').click();
  await expect(parent.locator('.plan-entry')).toHaveCount(1);
  expect(planRequests).toBe(2);
  await expect(parent.locator('.plan-summary')).toHaveText('1 题 · 预计 2 分钟');
  await parent.unroute('**/api/v1/review-plans');
  await button(parent, '去学习页查看任务').click();
  await button(parent, '复习清单').click();
  await expect(parent.locator('.task-card')).toHaveCount(1);
  await button(parent, '学习页').click();
  step = 'persisted review plan history';
  await expect(parent.locator('.plan-history .plan-index')).toHaveCount(1);
  await parent.locator('.plan-history .plan-index').click();
  await expect(parent.locator('.plan-detail-total')).toHaveText('1 题 · 预计 2 分钟');
  await expect(parent.locator('.plan-detail .history-task')).toHaveCount(1);
  await button(parent, '收起计划详情').click();
  step = 'existing student child pairing';
  await pair(parent, child, origin);
  await button(child, '复习清单').click();
  await expect(child.locator('.task-card')).toHaveCount(1);
  await button(child, '学习页').click();
  await expect(child.locator('.plan-history .plan-index')).toHaveCount(1);
  await child.locator('.plan-history .plan-index').click();
  await expect(child.locator('.plan-detail .history-task')).toHaveCount(1);
  await button(child, '收起计划详情').click();
  await expect(button(child, '学习报告')).toHaveCount(0);

  step = 'timetable term and odd-even templates';
  await button(parent, '学习课表').click();
  await button(parent, '创建学期').click();
  await label(parent, '学期名称').fill('浏览器验收秋季');
  await label(parent, '学期开始日期').fill('2026-10-26');
  await label(parent, '学期结束日期').fill('2026-11-08');
  await button(parent, '保存学期').click();
  await button(parent, '编辑每周课程').click();
  await button(parent, '添加课程').click();
  // Clearing the subject must remain a validation error, never a component crash.
  await label(parent, '课程1科目').fill('临时'); await label(parent, '课程1科目').fill('');
  await button(parent, '保存每周模板').click();
  await expect(parent.getByRole('alert')).toContainText('请填写 1–80 字的科目');
  await label(parent, '课程1科目').fill('数学');
  await button(parent, '课程1单周').click();
  await button(parent, '添加课程').click();
  await label(parent, '课程2科目').fill('英语');
  await button(parent, '课程2双周').click();
  await button(parent, '保存每周模板').click();
  await expect(parent.locator('.lesson-card')).toHaveCount(1);
  await expect(parent.locator('.lesson-card')).toContainText('数学');
  await button(parent, '下一周').click();
  await expect(parent.locator('.lesson-card')).toContainText('英语');
  await button(parent, '上一周').click();

  step = 'timetable conflict explicitly confirmed';
  await button(parent, '编辑每周课程').click(); await button(parent, '添加课程').click();
  await label(parent, '课程3科目').fill('美术'); await button(parent, '保存每周模板').click();
  await expect(parent.locator('.conflict-confirm')).toContainText('2026-10-26 · 节次重复');
  await expect(parent.locator('.lesson-card')).toHaveCount(1);
  await expect(label(parent, '课程3科目')).toBeDisabled();
  await button(parent, '确认保留冲突并保存').click();
  await expect(parent.locator('.lesson-card')).toHaveCount(2);
  await expect(parent.locator('.conflicts')).toContainText('2026-11-02');
  await button(parent, '编辑每周课程').click(); await button(parent, '移除课程 3').click();
  await button(parent, '保存每周模板').click();
  await expect(parent.locator('.conflicts')).toHaveCount(0);

  step = 'single lesson move cancel restore and child read-only timetable';
  await parent.locator('.lesson-card').getByRole('button', { name: /^调课 / }).click();
  await label(parent, '调课日期').fill('2026-10-27'); await label(parent, '调整原因').fill('学校活动调课');
  await button(parent, '保存单次调课').click();
  await expect(parent.locator('.lesson-card')).toContainText('由 2026-10-26 调整至此');
  await button(child, '学习课表').click();
  await expect(child.locator('.lesson-card')).toContainText('数学');
  await expect(child.locator('.lesson-card')).toContainText('由 2026-10-26 调整至此');
  for (const name of ['创建学期', '编辑每周课程', '恢复原课程']) await expect(button(child, name)).toHaveCount(0);
  expect(await child.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await parent.locator('.lesson-card').getByRole('button', { name: /^停课 / }).click();
  await label(parent, '调整原因').fill('临时停课'); await button(parent, '确认单次停课').click();
  await expect(parent.locator('.lesson-card')).toHaveCount(0);
  await label(parent, '恢复原因').fill('恢复正常上课');
  await parent.getByRole('button', { name: /^恢复 2026-10-26 / }).click();
  await expect(parent.locator('.lesson-card')).toContainText('数学');
  await expect(parent.locator('.lesson-card .change-note')).toHaveCount(0);
  await expect(parent.locator('.history-row')).toHaveCount(3);

  step = 'timetable concurrent revision requires reload';
  let competed = false;
  await parent.route('**/api/v1/timetables/*', async route => {
    if (route.request().method() !== 'PUT' || competed) return route.continue();
    competed = true;
    // Commit a real competing write, then send the stale browser command unchanged.
    const response = await route.fetch(); expect(response.status()).toBe(200);
    await route.continue();
  });
  await button(parent, '编辑每周课程').click(); await label(parent, '课程1教室').fill('并发教室');
  await button(parent, '保存每周模板').click();
  await expect(parent.getByRole('alert')).toContainText('课表已被其他操作更新');
  await expect(button(parent, '保存每周模板')).toBeDisabled();
  await button(parent, '放弃草稿并刷新课表').click();
  await expect(parent.locator('.lesson-card')).toContainText('并发教室');
  await parent.unroute('**/api/v1/timetables/*');

  step = 'committed timetable response loss reconciles without duplicate';
  await parent.route('**/api/v1/lesson-overrides', async route => {
    const response = await route.fetch(); expect(response.status()).toBe(201);
    await route.abort('failed');
  });
  await parent.locator('.lesson-card').getByRole('button', { name: /^停课 / }).click();
  await label(parent, '调整原因').fill('回执丢失后核对'); await button(parent, '确认单次停课').click();
  await expect(parent.getByRole('alert')).toContainText('保存结果尚未确认');
  await button(parent, '放弃草稿并刷新课表').click();
  await expect(parent.locator('.lesson-card')).toHaveCount(0);
  await expect(parent.locator('.history-row')).toHaveCount(4);
  await parent.unroute('**/api/v1/lesson-overrides');
  await button(child, '刷新课表').click(); await expect(child.locator('.lesson-card')).toHaveCount(0);
  await button(child, '学习页').click(); await button(parent, '学习页').click();

  step = 'child draft and unverified submission';
  await button(child, '复习清单').click();
  await button(child, '打开复习').click();
  await label(child, '我的复习思路').fill('先算 12 + 8，答案是 20。');
  await button(child, '保存草稿').click();
  await expect(child.getByText('草稿已保存，尚未提交。', { exact: true })).toBeVisible();
  await button(child, '收起复习').click();
  await button(child, '打开复习').click();
  await expect(label(child, '我的复习思路')).toHaveValue('先算 12 + 8，答案是 20。');
  await button(child, '提交复习记录').click();
  await expect(child.locator('.attempt-row')).toHaveText('已提交作答 · 待核验');
  await expect(child.locator('.task-card .badge')).toHaveText('已提交待核验');
  await expect(button(child, '提交复习记录')).toHaveCount(0);
  await expect(child.locator('.submission-feedback')).toContainText('等待家长核对');
  await expect(label(child, '我的复习思路')).toBeDisabled();
  await button(parent, '学习页').click();
  await button(parent, '刷新内容').click();
  await button(parent, '复习清单').click();
  await button(parent, '查看复习记录').click();
  await expect(parent.locator('.exercise')).toContainText('先算 12 + 8，答案是 20。');
  await expect(parent.locator('.attempt-row')).toHaveText('已提交作答 · 待核验');
  await expect(label(parent, '我的复习思路')).toHaveCount(0);
  step = 'plan history reads live submitted task';
  await button(parent, '学习页').click();
  await parent.locator('.plan-history .plan-index').click();
  await expect(parent.locator('.plan-detail .history-task')).toContainText('已提交待核验');
  await button(parent, '收起计划详情').click();
  await privateStorage(parent); await privateStorage(child);

  step = 'stale report, live plan and explicit rebuild';
  await parentSection(parent, '学习报告');
  await button(parent, '刷新报告状态').click();
  await expect(button(parent, '重建学习报告')).toBeVisible();
  await button(parent, '刷新复习计划').click();
  await expect(parent.locator('.plan-entry')).toContainText('已提交待核验');
  await button(parent, '重建学习报告').click();
  await expect(button(parent, '重建学习报告')).toHaveCount(0);
  await expect(parent.locator('.folio-heading')).toContainText('版本 2');
  await expect(parent.locator('.source-row')).toHaveCount(originalSourceCount + 1);
  await expect(parent.locator('.evidence-block').last()).toContainText('已提交待核验 1 次');
  await expect(parent.locator('.report-history .report-index')).toHaveCount(4);

  step = 'mobile report layout';
  await parent.setViewportSize({ width: 390, height: 844 });
  await expect(button(parent, '学习报告')).toBeVisible();
  expect(await parent.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await parent.setViewportSize({ width: 1280, height: 800 });

  step = 'image selection, ordering and crop';
  const pictures = await child.evaluate(() => {
    const canvas = document.createElement('canvas'); canvas.width = 200; canvas.height = 100;
    const context = canvas.getContext('2d');
    context.fillStyle = '#ff0000'; context.fillRect(0, 0, 100, 100);
    context.fillStyle = '#0000ff'; context.fillRect(100, 0, 100, 100);
    const split = canvas.toDataURL('image/png').split(',')[1];
    context.fillStyle = '#00ff00'; context.fillRect(0, 0, 200, 100);
    return { split, green: canvas.toDataURL('image/png').split(',')[1] };
  });
  await child.getByRole('navigation', { name: '学习导航' }).getByRole('button', { name: '拍作业', exact: true }).click();
  step = 'select generated image files';
  await label(child, '选择作业图片').setInputFiles([
    { name: 'green.png', mimeType: 'image/png', buffer: Buffer.from(pictures.green, 'base64') },
    { name: 'split.png', mimeType: 'image/png', buffer: Buffer.from(pictures.split, 'base64') },
  ]);
  step = 'selected image count';
  await expect(child.locator('.upload-page')).toHaveCount(2);
  step = 'reorder selected pages';
  await button(child, '第2页前移').click();
  await expect(child.locator('.page-filename').first()).toHaveText('split.png');
  await expect(button(child, '提交已完成的作业')).toBeEnabled();
  step = 'open selected page editor';
  await button(child, '编辑第1页').click();
  step = 'rotate selected page';
  await button(child, '顺时针旋转 90°').click();
  step = 'crop selected page';
  await label(child, '裁剪上边距百分比').fill('50');
  await label(child, '裁剪高度百分比').fill('50');
  await button(child, '应用图片调整').click();
  step = 'verify edited page replacement';
  await expect(child.locator('.page-filename').first()).toHaveText('split-已调整.jpg');

  step = 'lost submission receipt and stable retry';
  const submissions = []; let intents = 0, uploads = 0;
  child.on('request', request => {
    const path = new URL(request.url()).pathname;
    if (request.method() === 'POST' && path === '/api/v1/media/upload-intents') intents++;
    if (request.method() === 'PUT' && path.endsWith('/upload')) uploads++;
  });
  await child.route('**/api/v1/submissions', async route => {
    const request = route.request();
    if (request.method() !== 'POST') return route.continue();
    submissions.push({ body: request.postDataJSON(), key: request.headers()['idempotency-key'] });
    if (submissions.length === 1) {
      const response = await route.fetch(); expect(response.status()).toBe(202);
      return route.abort('connectionfailed');
    }
    return route.continue();
  });
  await button(child, '提交已完成的作业').click();
  await expect(button(child, '重试确认作业提交')).toBeEnabled();
  await expect(child.locator('.upload-message')).toContainText('连接未完成');
  await expect(button(child, '编辑第1页')).toBeDisabled();
  await expect(button(child, '移除第1页')).toBeDisabled();
  await expect(label(child, '选择作业图片')).toBeDisabled();
  await button(child, '重试确认作业提交').click();
  await expect(child.locator('.homework-detail')).toContainText('2 张作业');
  await expect(child.locator('.upload-page')).toHaveCount(0);
  expect(submissions).toHaveLength(2); expect(submissions[1]).toEqual(submissions[0]);
  expect(intents).toBe(2); expect(uploads).toBe(2);
  await child.unroute('**/api/v1/submissions');

  step = 'private originals preserve edited pixels and page order';
  const originals = [];
  for (const position of [1, 2]) {
    step = 'select private original ' + position;
    await child.getByRole('tab', { name: '第 ' + position + ' 张', exact: true }).click();
    const image = child.locator('.original-image img');
    step = 'load private original ' + position;
    await expect(image).toHaveCount(1);
    await expect.poll(() => image.evaluate(async image => {
      await image.decode();
      return [image.naturalWidth, image.naturalHeight];
    })).toEqual(position === 1 ? [100, 100] : [200, 100]);
    step = 'read private original pixels ' + position;
    originals.push(await image.evaluate(async image => {
      await image.decode();
      const canvas = document.createElement('canvas'); canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
      const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
      return { width: canvas.width, height: canvas.height, pixel: [...context.getImageData(canvas.width / 2, canvas.height / 2, 1, 1).data] };
    }));
  }
  step = 'verify private original dimensions';
  expect(originals.map(({ width, height }) => [width, height])).toEqual([[100, 100], [200, 100]]);
  step = 'verify private original colors';
  expect(originals[0].pixel[2]).toBeGreaterThan(240); expect(originals[0].pixel[0]).toBeLessThan(15);
  expect(originals[1].pixel[1]).toBeGreaterThan(240); expect(originals[1].pixel[0]).toBeLessThan(15);
  await privateStorage(child);

  step = 'cancellation and date-tab history';
  await button(child, '取消本次处理').click(); await button(child, '确认取消作业').click();
  await expect(child.locator('.homework-detail > .section-head > .badge')).toHaveText('已取消');
  await expect(child.locator('.upload-message')).toContainText('未使用的预占页数已释放');
  await button(child, '返回作业记录').click();
  await expect(child.locator('.submission-row')).toHaveCount(2);
  const beforeRefresh = await child.locator('.submission-row').allTextContents();
  await child.route('**/api/v1/submissions?**', route => route.abort('connectionfailed'));
  await button(child, '刷新记录').click();
  await expect(child.locator('.upload-message')).toContainText('连接未完成');
  expect(await child.locator('.submission-row').allTextContents()).toEqual(beforeRefresh);
  await child.unroute('**/api/v1/submissions?**');
  await button(child, '刷新记录').click();
  await expect(child.locator('.date-tab').first()).toContainText('今天');
  await expect(child.locator('.date-tab').nth(1)).toContainText('昨天');
  await expect(child.getByRole('textbox', { name: '作业开始日期' })).toHaveCount(0);
  await child.getByRole('tab', { name: /昨天/ }).click();
  await expect(child.locator('.submission-row')).toHaveCount(0);
  await child.getByRole('tab', { name: /今天/ }).click();
  await expect(child.locator('.submission-row')).toHaveCount(2);
  await privateStorage(child);

  step = 'device revocation';
  await child.getByRole('navigation', { name: '学习导航' }).getByRole('button', { name: '学习页', exact: true }).click();
  await parentSection(parent, '授权与设备');
  await button(parent, '刷新内容').click();
  await expect(parent.locator('.device-row')).toHaveCount(1);
  await button(parent, '撤销设备访问').click();
  await expect(parent.locator('.device-row')).toHaveCount(0);
  await button(child, '刷新内容').click();
  await expect(button(child, '连接并开始学习')).toBeVisible();
  await expect(child.locator('.exercise')).toHaveCount(0);
  await privateStorage(child);
  step = 'same device learning requires parent reauthentication';
  await button(parent, '开始学习').click();
  await button(parent, '学习页').click();
  await expect(button(parent, '刷新内容')).toBeEnabled();
  await expect(button(parent, '开始学习')).toHaveCount(0);
  await parent.reload();
  await button(parent, '学习页').click();
  await expect(button(parent, '刷新内容')).toBeEnabled();
  await button(parent, '家长中心').click();
  await expect(button(parent, '登录家长中心')).toBeVisible();
  await label(parent, '用户名').fill(owner.email);
  await label(parent, '登录密码').fill('incorrect-password');
  await button(parent, '登录家长中心').click();
  await expect(parent.getByRole('alert')).toBeVisible();
  await button(parent, '继续学习').click();
  await button(parent, '学习页').click();
  await expect(button(parent, '刷新内容')).toBeEnabled();
  await button(parent, '家长中心').click();
  await label(parent, '登录密码').fill(owner.password);
  await button(parent, '登录家长中心').click();
  await expect(button(parent, '授权与设备')).toBeVisible();
  step = 'failed logout cannot restore the old cookie session';
  await parent.route('**/api/v1/auth/logout', route => route.abort('failed'));
  await button(parent, '退出登录').click();
  await expect(button(parent, '登录')).toBeVisible();
  step = 'reload after failed logout';
  await parent.reload();
  await expect(button(parent, '登录')).toBeVisible();
  await expect(button(parent, '家长中心')).toHaveCount(0);
  step = 'retry pending logout after network recovery';
  await parent.unroute('**/api/v1/auth/logout');
  await parent.reload();
  await expect(button(parent, '登录')).toBeVisible();
  step = 'successful pending logout clears browser storage';
  await privateStorage(parent);
  step = 'confirmed logout remains signed out after another reload';
  await parent.reload();
  await expect(button(parent, '登录')).toBeVisible();
  expect(pageErrors).toBe(0);
  console.log(JSON.stringify({ registration: 'passed', timetable: 'passed', reports: 'passed', review: 'passed', upload: 'passed', history: 'passed', page_errors: pageErrors }));
} catch (error) {
  const reason = String(error?.message).includes('strict mode violation') ? 'ambiguous locator' : 'interaction or assertion';
  const storageKeys = browser ? await browser.contexts()[0]?.pages()[0]?.evaluate(() => Object.keys(localStorage)).catch(() => []) : [];
  const locations = String(error?.stack).match(/real-api\.mjs:\d+:\d+/g) ?? [];
  console.error(`Real family API browser check failed at ${step} (${reason}; ${locations.join(', ')}). No credentials logged. Storage keys: ${JSON.stringify(storageKeys)}. Requests: ${JSON.stringify(failedRequests.slice(-5))}`);
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  if (server) await new Promise(resolve => { server.httpServer.close(resolve); server.httpServer.closeAllConnections(); });
}
