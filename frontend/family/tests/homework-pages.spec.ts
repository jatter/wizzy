import { test, expect, type Page } from '@playwright/test';
import type { HomeworkPageResult } from '../src/api';

const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aP1sAAAAASUVORK5CYII=', 'base64');
const record = (id: string, created_at: string) => ({ id, student_id: 's1', subject: 'auto', status: 'completed', source_revision: 1, created_at, page_count: 3 });
async function session(page: Page, legacy = false, failed = false) {
  await page.clock.setFixedTime(new Date('2026-11-14T02:00:00Z'));
  const rows = [record('new', '2026-11-14T01:00:00Z'), record('older', '2026-11-14T00:00:00Z'), record('yesterday', '2026-11-13T01:00:00Z')];
  let uploads = 0, retried = false;
  const submitted: unknown[] = [], dates: string[] = [];
  await page.route('**/api/v1/**', async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname.replace('/api/v1', '');
    const data = (value: unknown) => route.fulfill({ json: { data: value } });
    if (path === '/auth/me') return data({ audience: 'child', tenant_id: 'family', student_id: 's1', role: null });
    if (path === '/students/s1') return data({ id: 's1', nickname: '小禾', grade: 5, revision: 1, consent_active: true });
    if (path.endsWith('/submission-days')) return data({ items: [{ date: '2026-11-14', count: 2 }, { date: '2026-11-13', count: 1 }, { date: '2026-11-10', count: 1 }] });
    if (path === '/submissions' && req.method() === 'POST') {
      submitted.push(req.postDataJSON());
      return data({ submission_id: 'new', job_id: 'job-new', status: 'queued', source_revision: 1 });
    }
    if (path === '/submissions') {
      const date = url.searchParams.get('date_from')!; dates.push(date);
      return data({ items: rows.filter(r => r.created_at.startsWith(date)), next_cursor: null });
    }
    if (path.endsWith('/model-outputs')) return data({ items: [1, 2].map(sequence => ({
      id: 'o' + sequence, sequence, prompt_name: '批改' + sequence, model_name: 'vision', model_priority: 1, prompt_priority: sequence * 10,
      status: 'succeeded', content: '已保存的历史原始批改文本', error_message: null,
      page_results: legacy ? [] : ['math', 'english', 'chinese'].map((subject, index) => ({
        position: index + 1, subject, note: '', counts: { correct: index, incorrect: 1, unanswered: 1, unclear: 1 },
        questions: [{ number: '第' + (index + 1) + '题', verdict: 'incorrect', student_answer: '41', reasoning: '先确定两个加数', steps: ['40 + 2 = 42'], answer: '42' }],
      })),
    })) });
    if (path.endsWith('/regrade') && req.method() === 'POST') { retried = true; return data({ submission_id: 'new', job_id: 'job-new' }); }
    if (path.startsWith('/submissions/')) return data({ ...rows.find(r => path.endsWith(r.id))!, status: failed && !retried ? 'failed' : retried ? 'queued' : 'completed', job_id: 'job-new', pages: [1, 2, 3].map(position => ({ position, media_id: 'm' + position })) });
    if (path.startsWith('/jobs/')) return data({ id: 'job-new', status: failed && !retried ? 'failed' : retried ? 'queued' : 'succeeded', error_code: failed && !retried ? 'homework_grading_failed' : null });
    if (path === '/media/upload-intents') return data({ media_id: 'upload-' + ++uploads });
    if (path.startsWith('/media/')) return path.endsWith('/content') ? route.fulfill({ body: png, contentType: 'image/png' }) : data({});
    if (path.includes('/mistake-bank')) return data({ items: [{ id: 'item-1', stem: '第5题 鸡兔同笼', subject_label: '数学', incorrect_count: 2, correct_count: 1, latest_verdict: 'incorrect', latest_day: '2026年11月14日', repeated_count: 2 }] });
    if (path.includes('/mistake-sheets')) return data({ items: [] });
    if (path.includes('/mistakes') || path.includes('/review-tasks') || path === '/review-plans' || path.endsWith('/terms')) return data({ items: [], next_cursor: null });
    if (path.endsWith('/homework-backlog')) return data({ items: [] });
    if (path.includes('/homework-days/')) return data({ items: [], scope_state: 'incomplete', pending_imports: 0, summary: { required: 0, completed: 0, verified: 0, no_homework: false, all_verified: false } });
    throw new Error('Unexpected route ' + path);
  });
  await page.goto('/');
  await expect(nav(page, '拍作业')).toHaveClass(/\bchosen\b/);
  await expect(page.getByText('拍清完整题目和作答')).toBeVisible();
  return { submitted, dates };
}
const nav = (page: Page, name: string) => page.getByRole('navigation', { name: '学习导航' }).getByRole('button', { name, exact: true });

test('mixed images submit without subject selection or confirmation checkbox into a separate record screen', async ({ page }) => {
  const { submitted } = await session(page);
  await nav(page, '拍作业').click();
  await expect(page.locator('.submission-row')).toHaveCount(0);
  await expect(page.getByRole('combobox')).toHaveCount(0);
  await expect(page.getByRole('checkbox')).toHaveCount(0);
  await page.getByLabel('选择作业图片', { exact: true }).setInputFiles(['math', 'english', 'chinese'].map(name => ({ name: name + '.png', mimeType: 'image/png', buffer: png })));
  await expect(page.locator('.upload-page')).toHaveCount(3);
  await page.getByRole('button', { name: '提交已完成的作业', exact: true }).click();
  await expect(page.locator('.homework-detail')).toBeVisible();
  expect(submitted).toHaveLength(1);
  expect(submitted[0]).toMatchObject({ subject: 'auto', completed_attempt: true, media_ids: ['upload-1', 'upload-2', 'upload-3'] });
  await expect(page.locator('.upload-panel')).toHaveCount(0);
});

test('history uses date tabs and each image shows its own original, counts and full solution', async ({ page }) => {
  const { dates } = await session(page);
  await nav(page, '作业记录').click();
  await expect(page.locator('.date-tab').first()).toContainText('今天');
  await expect(page.locator('.date-tab').nth(1)).toContainText('昨天');
  await expect(page.locator('.submission-row')).toHaveCount(2);
  await expect(page.locator('.submission-row').first()).toContainText('09:00');
  await expect(page.getByRole('textbox')).toHaveCount(0);
  await page.getByRole('tab', { name: /昨天/ }).click();
  await expect(page.locator('.submission-row')).toHaveCount(1);
  expect(dates.at(-1)).toBe('2026-11-13');
  await page.getByRole('button', { name: '查看详情', exact: true }).click();
  await expect(page.locator('.original-image')).toBeVisible();
  await expect(page.getByRole('tab', { name: '错题(1)', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('tab', { name: '对题(0)', exact: true })).toBeVisible();
  await expect(page.locator('.answers-column')).toContainText('这 1 题先看一看，弄懂了就赚到啦');
  await expect(page.locator('.cheer')).toHaveText(/继续加油|差一点点|没关系|这题有点绕/);
  await expect(page.locator('.answers-column')).toContainText('还有 1 题未作答、1 题待核对');
  await expect(page.locator('.answers-column')).toContainText('解题思路');
  await expect(page.locator('.solution-step')).toContainText('40 + 2 = 42');
  await expect(page.locator('.final-answer')).toHaveText('42');
  await page.getByRole('tab', { name: '对题(0)', exact: true }).click();
  await expect(page.locator('.answers-column')).toContainText('这一栏暂时没有可展开的题目');
  await expect(page.locator('.solution-step')).toHaveCount(0);
  await page.getByRole('tab', { name: '错题(1)', exact: true }).click();
  await expect(page.locator('.solution-step')).toContainText('40 + 2 = 42');
  await page.getByRole('tab', { name: '全部', exact: true }).click();
  await expect(page.locator('.final-answer')).toHaveText('42');
  await expect(page.locator('.answers-column')).toContainText('模型判断供参考');
  for (const [position, subject, correct] of [[2, '英语', 1], [3, '语文', 2]] as const) {
    await page.getByRole('tab', { name: '第 ' + position + ' 张', exact: true }).click();
    await expect(page.locator('.original-column')).toContainText(subject);
    await expect(page.getByRole('tab', { name: '错题(1)', exact: true })).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByRole('tab', { name: '对题(' + correct + ')', exact: true })).toBeVisible();
    await expect(page.locator('.original-image')).toHaveAttribute('alt', '已上传作业第' + position + '张原图');
  }
  for (const width of [320, 390, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  await page.getByRole('button', { name: '返回作业记录', exact: true }).click();
  await expect(page.locator('.date-tabs')).toBeVisible();
  await expect(page.locator('.homework-detail')).toHaveCount(0);
});

test('mistake bank and review list open as separate pages', async ({ page }) => {
  await session(page);
  await nav(page, '错题回看').click();
  await expect(page.getByText('第5题 鸡兔同笼')).toBeVisible();
  await expect(page.getByText('还没有复习任务')).toHaveCount(0);
  await nav(page, '复习清单').click();
  await expect(page.getByText('还没有复习任务')).toBeVisible();
  await expect(page.getByText('第5题 鸡兔同笼')).toHaveCount(0);
  await expect(page.getByText('还没有复习卷')).toBeVisible();
});

test('legacy outputs keep originals and raw responses without fabricated zero counts', async ({ page }) => {
  await session(page, true);
  await nav(page, '作业记录').click();
  await page.getByRole('button', { name: '查看详情', exact: true }).first().click();
  await expect(page.locator('.original-image')).toBeVisible();
  await expect(page.getByLabel('本张对错统计')).toHaveCount(0);
  await expect(page.getByText('这份记录尚无可核对的逐图统计。可在下方查看已保存的原始批改记录。')).toBeVisible();
  await page.getByRole('button', { name: '查看批改记录', exact: true }).click();
  await expect(page.locator('.model-answer').first()).toHaveText('已保存的历史原始批改文本');
});

test('failed grading can be retried from the button after refresh status', async ({ page }) => {
  await session(page, true, true);
  await nav(page, '作业记录').click();
  await page.getByRole('button', { name: '查看详情', exact: true }).first().click();
  const actions = page.locator('.detail-actions');
  await expect(actions.getByRole('button')).toHaveText(['刷新处理状态', '重新批改', '查看批改记录']);
  await actions.getByRole('button', { name: '重新批改', exact: true }).click();
  await expect(page.getByText('已重新开始批改，完成后会显示在这里。')).toBeVisible();
  await expect(actions.getByRole('button', { name: '重新批改', exact: true })).toHaveCount(0);
  await expect(page.getByText('等待处理', { exact: true })).toBeVisible();
});

test('dictation accepts optional reference and sends the selected mode', async ({ page }) => {
  const { submitted } = await session(page);
  await page.getByRole('button', { name: '单词听写', exact: true }).click();
  await expect(page.getByText('未提供参考时仅检查拼写，不能核对听写目标、漏写或顺序。')).toBeVisible();
  await page.getByLabel('参考词表（可选）', { exact: true }).fill('第1张：\n1. whether\n2. take care of');
  await page.getByLabel('选择作业图片', { exact: true }).setInputFiles({ name: 'words.png', mimeType: 'image/png', buffer: png });
  await page.getByRole('button', { name: '提交已完成的作业', exact: true }).click();
  await expect(page.locator('.homework-detail')).toBeVisible();
  expect(submitted[0]).toMatchObject({ activity_type: 'dictation', reference_text: '第1张：\n1. whether\n2. take care of' });
});

async function wordResults(page: Page, questions: HomeworkPageResult['questions']) {
  await page.route('**/api/v1/submissions/*/model-outputs', route => route.fulfill({ json: { data: { items: [{
    id: 'words', sequence: 2, status: 'succeeded', content: '', error_message: null,
    page_results: [{ position: 1, subject: 'english', note: '', questions,
      counts: Object.fromEntries(['correct', 'incorrect', 'unanswered', 'unclear'].map(verdict => [verdict, questions.filter(question => question.verdict === verdict).length])) }],
  }] } } }));
  await nav(page, '作业记录').click();
  await page.getByRole('button', { name: '查看详情', exact: true }).first().click();
}
const word: HomeworkPageResult['questions'][number] = { number: '左列第3项', item_type: 'word', grading_basis: 'none', verdict: 'unclear', student_answer: 'w?rd', reasoning: '字迹不清，请核对', steps: [], answer: '' };

test('unclear word results open all items without claiming a perfect score', async ({ page }) => {
  await session(page);
  await wordResults(page, [word]);
  await expect(page.getByRole('tab', { name: '全部', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('.answer-question')).toContainText('左列第3项');
  await expect(page.locator('.answer-question')).toContainText('待核对');
  await expect(page.getByText('这张没有错题，你真棒')).toHaveCount(0);
  await page.getByRole('tab', { name: '错题(0)', exact: true }).click();
  await expect(page.getByText('这张没有错题，你真棒')).toHaveCount(0);
});

test('spelling-only success is explicitly scoped even in mixed results', async ({ page }) => {
  await session(page);
  await wordResults(page, [word, { ...word, number: '右列第1项', grading_basis: 'spelling_only', verdict: 'correct', student_answer: 'weather', reasoning: '词形存在，听写目标待核对' }]);
  await expect(page.locator('.answers-column')).toContainText('仅检查拼写');
  await expect(page.locator('.answers-column')).toContainText('不代表听写正确');
  await page.getByRole('tab', { name: '通过(1)', exact: true }).click();
  await expect(page.locator('.answer-question .badge')).toHaveText('拼写未见错误');
  await expect(page.locator('.answers-column')).not.toContainText('做对了');
  await expect(page.locator('.cheer')).toHaveCount(0);
});

test('word and phrase errors retain column positions, original spelling and correction', async ({ page }) => {
  await session(page);
  await wordResults(page, [
    { ...word, number: '左列第3项', grading_basis: 'spelling_only', verdict: 'incorrect', student_answer: 'applle', reasoning: '多写一个l', steps: ['去掉多余的l'], answer: 'apple' },
    { ...word, number: '右列第2项', item_type: 'phrase', grading_basis: 'task_reference', verdict: 'incorrect', student_answer: 'take care', reasoning: '参考要求完整短语', steps: [], answer: 'take care of' },
  ]);
  await expect(page.getByRole('tab', { name: '错题(2)', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('.answer-question')).toHaveCount(2);
  await expect(page.locator('.answer-question').first()).toContainText('左列第3项');
  await expect(page.locator('.answer-question').first()).toContainText('applle');
  await expect(page.locator('.answer-question').first()).toContainText('检查依据');
  await expect(page.locator('.answer-question').first()).toContainText('订正建议');
  await expect(page.locator('.final-answer')).toHaveText(['apple', 'take care of']);
  for (const width of [320, 390, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  await page.screenshot({ path: test.info().outputPath('dictation-result.png'), fullPage: true });
});
