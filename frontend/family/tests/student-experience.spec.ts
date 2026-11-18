import { test, expect, type Page } from '@playwright/test';

const task = (id: string, due: string, state = 'open', current = true) => ({
  id, student_id: 's1', question_id: 'q-' + id, stem: id === 'due' ? '12 + 8 = ？' : '分数乘法，写下你的计算过程。',
  due_date: due, state, source_is_current: current,
  draft: { revision: 1, student_answer: '' }, attempts: [] as { id: string; outcome: string; verdict: string }[],
});

async function studentSession(page: Page, options: { empty?: boolean; paginated?: boolean } = {}) {
  let authenticated = false;
  let current = task('due', '2026-11-13');
  const tasks = options.empty ? [] : [task('future', '2027-01-01'), task('stale', '2026-11-01', 'open', false),
    task('waiting', '2026-11-01', 'awaiting_review'), current, task('today', '2026-11-14')];
  const requests: { path: string; body: unknown }[] = [];
  await page.clock.setFixedTime(new Date('2026-11-14T02:00:00Z'));
  await page.route('**/api/v1/**', async route => {
    const req = route.request(), path = new URL(req.url()).pathname.replace('/api/v1', '');
    const data = (value: unknown) => route.fulfill({ json: { data: value } });
    if (path === '/child-sessions' && req.method() === 'POST') {
      authenticated = true;
      return data({ access_token: 'test-only-token', token_type: 'bearer' });
    }
    if (path === '/auth/me') return authenticated
      ? data({ audience: 'child', tenant_id: 'family', student_id: 's1', role: null })
      : route.fulfill({ status: 401, json: { error: { code: 'authentication_required' } } });
    if (path === '/students/s1') return data({ id: 's1', nickname: '小禾', grade: 5, revision: 1, consent_active: true });
    if (path === '/students/s1/mistakes') return data({ items: [], next_cursor: null, pending_updates: 0 });
    if (path === '/students/s1/mistake-bank' || path === '/students/s1/mistake-sheets') return data({ items: [] });
    if (path === '/students/s1/review-tasks') return data({ items: tasks, next_cursor: options.paginated ? 'more' : null });
    if (path === '/review-tasks/due') return data(current);
    if (path === '/review-tasks/due/draft') {
      requests.push({ path, body: req.postDataJSON() });
      current = { ...current, draft: { revision: 2, student_answer: req.postDataJSON().student_answer } };
      return data(current);
    }
    if (path === '/review-tasks/due/attempts') {
      requests.push({ path, body: req.postDataJSON() });
      current = { ...current, state: 'awaiting_review', attempts: [{ id: 'a1', outcome: req.postDataJSON().outcome, verdict: 'pending_review' }] };
      return data(current);
    }
    if (path.endsWith('/submission-days')) return data({ items: [] });
    if (path === '/review-plans' || path === '/submissions' || path.endsWith('/terms')) return data({ items: [], next_cursor: null });
    if (path.endsWith('/homework-backlog')) return data({ items: [] });
    if (path.includes('/homework-days/')) return data({ items: [], scope_state: 'incomplete', pending_imports: 0, summary: { required: 0, completed: 0, verified: 0, no_homework: false, all_verified: false } });
    throw new Error(`Unexpected route: ${req.method()} ${path}`);
  });
  await page.goto('/?end=child');
  await page.getByRole('textbox', { name: '一次性配对码', exact: true }).fill('test-pair-code');
  await page.getByRole('button', { name: '连接并开始学习', exact: true }).click();
  const capture = page.getByRole('navigation', { name: '学习导航' }).getByRole('button', { name: '拍作业', exact: true });
  await expect(capture).toHaveClass(/\bchosen\b/);
  await expect(page.getByText('拍清完整题目和作答')).toBeVisible();
  return requests;
}
async function openLearning(page: Page) {
  await page.getByRole('navigation', { name: '学习导航' }).getByRole('button', { name: '学习页', exact: true }).click();
  await expect(page.getByText('小禾，今天从这里开始')).toBeVisible();
}

test('今日学习优先打开到期且来源有效的任务，提交后展示实际待核验状态', async ({ page }) => {
  const requests = await studentSession(page);
  await openLearning(page);
  const overview = page.getByRole('region', { name: '学习概况' });
  await expect(overview).toContainText(/2\s*题待复习/);
  await expect(overview).toContainText(/1\s*题待核对/);
  await page.getByRole('button', { name: '继续复习', exact: true }).click();
  await expect(page.locator('.exercise')).toContainText('12 + 8 = ？');
  await page.getByRole('textbox', { name: '我的复习思路' }).fill('先把 12 分成 10 和 2，2 加 8 得到 10，答案是 20。');
  await page.getByRole('button', { name: '提交复习记录', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '这次复习已记录' })).toBeVisible();
  await expect(page.locator('.submission-feedback')).toContainText('等待家长核对');
  expect(requests.map(r => r.path)).toEqual(['/review-tasks/due/draft', '/review-tasks/due/attempts']);
  expect(requests[1].body).toMatchObject({ outcome: 'submitted', expected_draft_revision: 2 });
  await page.getByRole('button', { name: '返回复习清单', exact: true }).click();
  await expect(page.locator('.exercise')).toHaveCount(0);
  await page.getByRole('navigation', { name: '学习导航' }).getByRole('button', { name: '学习页', exact: true }).click();
  await expect(overview).toContainText(/1\s*题待复习/);
  await expect(overview).toContainText(/2\s*题待核对/);
});

test('分页记录明确标识统计范围', async ({ page }) => {
  await studentSession(page, { paginated: true });
  await openLearning(page);
  await expect(page.getByRole('region', { name: '学习概况' })).toContainText(/2\+\s*题待复习/);
  await expect(page.getByText('基于已加载记录，还有更多复习待查看。')).toBeVisible();
});

test('没有学习记录时提供真实下一步，不生成成绩或连胜', async ({ page }) => {
  await studentSession(page, { empty: true });
  await openLearning(page);
  await expect(page.getByRole('button', { name: '拍一份作业', exact: true })).toBeVisible();
  await page.getByRole('navigation', { name: '学习导航' }).getByRole('button', { name: '复习清单', exact: true }).click();
  await expect(page.getByText('还没有复习任务', { exact: true })).toBeVisible();
  await expect(page.getByText(/连续学习.*天|已掌握.*%/)).toHaveCount(0);
});

test('学生学习桌在手机、平板与桌面保持可读且无横向溢出', async ({ page }) => {
  await studentSession(page);
  for (const width of [320, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.reload();
    await expect(page.getByRole('navigation', { name: '学习导航' }).getByRole('button', { name: '拍作业', exact: true })).toHaveClass(/\bchosen\b/);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `capture width=${width}`).toBe(true);
    await openLearning(page);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `width=${width}`).toBe(true);
    await expect(page.getByRole('button', { name: '继续复习', exact: true })).toBeVisible();
    await expect(page.locator('.family-app')).toHaveCount(1);
    await page.screenshot({ path: test.info().outputPath(`student-${width}.png`), animations: 'disabled' });
  }
  await page.getByRole('navigation', { name: '学习导航' }).getByRole('button', { name: '拍作业', exact: true }).click();
  await expect(page.getByRole('navigation', { name: '学习导航' }).getByRole('button', { name: '拍作业', exact: true })).toHaveClass(/\bchosen\b/);
  await expect(page.getByRole('button', { name: '学习页', exact: true })).not.toHaveClass(/\bchosen\b/);
  const file = page.getByLabel('选择作业图片', { exact: true });
  await expect(file).toHaveCSS('opacity', '0');
  await expect(file).toHaveCSS('position', 'absolute');
  await page.screenshot({ path: test.info().outputPath('homework-desktop.png'), animations: 'disabled' });
});
