import { test, expect, type Page, type Route } from '@playwright/test';

const student = (id: string) => ({ id, nickname: id === 's1' ? '小禾' : '小林', grade: 3, revision: 1, consent_active: true, created_at: '2026-10-26T01:00:00Z' });
const summary = (id = 'p1') => ({ id, student_id: 's1', type: 'weekly', period_start: '2026-10-26', period_end: '2026-11-02', timezone: 'Asia/Shanghai', report_id: 'report1', created_at: '2026-10-26T01:00:00Z', question_count: 1, estimated_minutes: 2, duration_basis: 'planning_estimate' });
const task = { id: 'task1', student_id: 's1', question_id: 'q1', stem: '12 + 8 = ？', state: 'awaiting_review', due_date: '2026-10-26', source_is_current: true, draft: { revision: 2, student_answer: '我的思路是先凑整。' }, attempts: [{ id: 'a1', outcome: 'submitted', verdict: 'pending_review' }] };
const detail = { ...summary(), stale: true, rules_version: '1', limits: { max_questions: 10, max_minutes: 20, minutes_per_question: 2 }, entries: [{ task_id: task.id, position: 1, due_date: task.due_date, priority: 'overdue', confirmed_incorrect_attempts: 0, task }] };
const button = (page: Page, name: string) => page.getByRole('button', { name, exact: true });
const panel = (page: Page) => page.locator('.plan-history');
type DetailHandler = (route: Route) => Promise<void>;
async function setup(page: Page, child = false) {
  const requests: URL[] = [];
  let handler: DetailHandler | undefined;
  let authenticated = false;
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url()), path = url.pathname.replace('/api/v1', '');
    requests.push(url);
    const data = (value: unknown) => route.fulfill({ json: { data: value } });
    if (path === '/auth/login' || (path === '/child-sessions' && route.request().method() === 'POST')) { authenticated = true; return data({ access_token: 'test-only-token', token_type: 'bearer' }); }
    if (path === '/auth/me' && !authenticated) return route.fulfill({ status: 401, json: { error: { code: 'authentication_required' } } });
    if (path === '/auth/me') return data({ audience: child ? 'child' : 'guardian', tenant_id: 'family', student_id: child ? 's1' : null, role: child ? null : 'owner' });
    if (path === '/auth/logout') { authenticated = false; return data({}); }
    if (path === '/tenants') return data({ items: [{ id: 'family', name: '小禾的家', role: 'owner' }] });
    if (path === '/students') return data({ items: [student('s1'), student('s2')], next_cursor: null });
    if (/^\/students\/s[12]$/.test(path)) return data(student(path.split('/').at(-1)!));
    if (path === '/evaluation-policy') return data({ version: 'v1', text: '可选评测', optional: true });
    if (path.endsWith('/evaluation-consent')) return data({ revision: 0, granted: false, active: false, reason: 'not_granted', policy_version: 'v1' });
    if (path === '/child-sessions') return data({ items: [] });
    if (path.endsWith('/submission-days')) return data({ items: [] });
    if (path.endsWith('/mistakes')) return data({ items: [], next_cursor: null, pending_updates: 0 });
    if (path.endsWith('/mistake-bank') || path.endsWith('/mistake-sheets')) return data({ items: [] });
    if (path.endsWith('/review-tasks') || path.endsWith('/terms') || ['/reports', '/submissions'].includes(path)) return data({ items: [], next_cursor: null });
    if (path === '/quota') return data({ page_limit: 100, used_pages: 0, reserved_pages: 0, remaining_pages: 100, period_start: '2026-10-01' });
    if (path === '/review-plans') {
      if (url.searchParams.get('student_id') === 's2' || url.searchParams.get('type') === 'monthly') return data({ items: [], next_cursor: null });
      return data({ items: [summary(url.searchParams.has('cursor') ? 'p2' : 'p1')], next_cursor: url.searchParams.has('cursor') ? null : 'next&page' });
    }
    if (/^\/review-plans\/p[12]$/.test(path)) return handler ? handler(route) : data(detail);
    if (path === '/review-tasks/task1') return data(task);
    if (path.endsWith('/homework-backlog')) return data({ items: [] });
    if (path.includes('/homework-days/')) return data({ items: [], scope_state: 'incomplete', pending_imports: 0, summary: { required: 0, completed: 0, verified: 0, no_homework: false, all_verified: false } });
    throw new Error(`Unexpected API route: ${route.request().method()} ${path}`);
  });
  await page.goto('/');
  if (child) {
    await button(page, '连接孩子的学习设备').click();
    await page.getByLabel('一次性配对码', { exact: true }).fill('test-pair-code');
    await button(page, '连接并开始学习').click();
  } else {
    await page.getByLabel('用户名', { exact: true }).fill('parent@example.test');
    await page.getByLabel('登录密码', { exact: true }).fill('test-password-1234');
    await button(page, '登录').click();
  }
  await expect(button(page, '拍作业')).toHaveClass(/\bchosen\b/);
  await button(page, '学习页').click();
  await expect(panel(page).locator('.plan-index')).toHaveCount(1);
  return { requests, setDetail: (value: DetailHandler) => { handler = value; } };
}

test('计划历史分页、周期筛选、当前任务与离开后重开', async ({ page }) => {
  const { requests } = await setup(page);
  await button(page, '加载更多计划').click();
  await expect(panel(page).locator('.plan-index')).toHaveCount(2);
  expect(requests.some(url => url.searchParams.get('cursor') === 'next&page' && url.search.includes('next%26page'))).toBe(true);
  await button(page, '月计划').click();
  await expect(panel(page)).toContainText('这个周期还没有复习计划');
  await button(page, '周计划').click();
  await expect(panel(page).locator('.plan-index')).toHaveCount(1);
  expect(requests.some(url => url.searchParams.get('type') === 'weekly')).toBe(true);
  await button(page, '全部计划').click();
  await panel(page).locator('.plan-index').click();
  await expect(panel(page).locator('.plan-detail')).toContainText('1 题 · 预计 2 分钟');
  await expect(panel(page).locator('.history-task')).toContainText('已提交待核验');
  await expect(panel(page)).toContainText('计划来源已变化');
  await expect(panel(page)).toContainText('预计时长不是实际学习用时');
  await panel(page).getByRole('button', { name: '查看复习记录', exact: true }).click();
  await expect(page.locator('.exercise')).toContainText(task.draft.student_answer);
  await button(page, '家长中心').click();
  await button(page, '学习报告').click();
  await button(page, '学习页').click();
  await expect(panel(page).locator('.plan-index')).toHaveCount(1);
});

for (const status of [0, 403, 404, 401]) {
  test(`刷新详情失败 ${status} 时清除旧题目和相应授权数据`, async ({ page }) => {
    const api = await setup(page);
    await panel(page).locator('.plan-index').click();
    await expect(panel(page).locator('.plan-detail')).toContainText(task.stem);
    api.setDetail(route => status ? route.fulfill({ status, json: { error: { code: 'unavailable' } } }) : route.abort('failed'));
    await button(page, '刷新计划详情').click();
    await expect(page.locator('.plan-detail')).toHaveCount(0);
    if (status === 401) {
      await expect(button(page, '登录')).toBeVisible();
      await expect(panel(page)).toHaveCount(0);
    } else {
      await expect(panel(page).getByRole('alert')).toBeVisible();
      await expect(panel(page).locator('.plan-index')).toHaveCount(status ? 0 : 1);
    }
  });
}

for (const action of ['student', 'logout']) {
  test(`迟到详情在${action === 'student' ? '切换学生' : '退出'}后不能重现`, async ({ page }) => {
    const api = await setup(page);
    let release!: () => void, arrived!: () => void;
    const blocked = new Promise<void>(resolve => { release = resolve; });
    const started = new Promise<void>(resolve => { arrived = resolve; });
    const response = page.waitForResponse(url => url.url().endsWith('/review-plans/p1'));
    api.setDetail(async route => { arrived(); await blocked; await route.fulfill({ json: { data: detail } }); });
    await panel(page).locator('.plan-index').click();
    await started;
    if (action === 'student') {
      await page.locator('.student-option').filter({ hasText: '小林' }).click();
      await expect(panel(page)).toContainText('还没有复习计划');
    } else await button(page, '退出登录').click();
    release(); await response;
    await expect(page.locator('.plan-detail')).toHaveCount(0);
    await expect(page.getByText(task.stem, { exact: true })).toHaveCount(0);
    if (action === 'logout') await expect(button(page, '登录')).toBeVisible();
  });
}

test('孩子在390px可回看计划并打开复习，页面无横向溢出', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page, true);
  await panel(page).locator('.plan-index').click();
  await expect(panel(page).locator('.history-task')).toContainText(task.stem);
  await expect(button(page, '学习报告')).toHaveCount(0);
  await expect(panel(page).getByRole('button', { name: '去学习报告安排复习', exact: true })).toHaveCount(0);
  await panel(page).scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: '/tmp/wizzy-plans-family-mobile.png', fullPage: true });
  await panel(page).getByRole('button', { name: '打开复习', exact: true }).click();
  await expect(page.locator('.exercise')).toContainText(task.stem);
  await expect(page.getByLabel('我的复习思路', { exact: true })).toHaveValue(task.draft.student_answer);
});
