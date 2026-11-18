import test from 'node:test';
import assert from 'node:assert/strict';
import { accuracy, beijingDate, reportPeriod } from '../src/reports.ts';
import { FamilyApi } from '../src/api.ts';

test('weekly periods normalize Monday and cross month/year boundaries', () => {
  assert.deepEqual(reportPeriod('2026-10-25', 'weekly'), { start: '2026-10-19', end: '2026-10-26', last: '2026-10-25' });
  assert.deepEqual(reportPeriod('2026-10-26', 'weekly'), { start: '2026-10-26', end: '2026-11-02', last: '2026-11-01' });
  assert.deepEqual(reportPeriod('2027-01-01', 'weekly'), { start: '2026-12-28', end: '2027-01-04', last: '2027-01-03' });
});
test('monthly periods honor leap years and exclusive upper bounds', () => {
  assert.deepEqual(reportPeriod('2024-02-29', 'monthly'), { start: '2024-02-01', end: '2024-03-01', last: '2024-02-29' });
  assert.deepEqual(reportPeriod('2026-12-31', 'monthly'), { start: '2026-12-01', end: '2027-01-01', last: '2026-12-31' });
  for (const date of ['2026-02-29', '2026-02-30', '2026-00-01', '2026-13-01', '26-10-01', '1999-12-31', '9999-01-01', '']) assert.equal(reportPeriod(date, 'monthly'), null);
  assert.equal(reportPeriod('2000-01-01', 'weekly'), null);
});
test('default date uses Beijing midnight independently of browser time zone', () => {
  assert.equal(beijingDate(Date.parse('2026-10-25T15:59:59Z')), '2026-10-25');
  assert.equal(beijingDate(Date.parse('2026-10-25T16:00:00Z')), '2026-10-26');
});
test('zero correct and no denominator produce different copy', () => {
  assert.equal(accuracy(0), '0.0%'); assert.equal(accuracy(null), '暂无有效样本'); assert.equal(accuracy(1 / 3), '33.3%');
});
test('report reads encode identifiers, filters and cursors; rebuild has no invented body', async () => {
  const calls = [];
  const api = new FamilyApi(async (path, options) => { calls.push({ path, ...options }); return { status: 200, body: { data: {} } }; });
  await api.reports('student/a', 'monthly', 'cursor&next');
  await api.generateReport('student/a', 'weekly', '2026-10-19');
  await api.report('report/a'); await api.reportSources('report/a', 'cursor&next'); await api.rebuildReport('report/a');
  assert.equal(calls[0].path, '/api/v1/reports?limit=20&cursor=cursor%26next&student_id=student%2Fa&type=monthly');
  assert.deepEqual(calls[1].body, { student_id: 'student/a', type: 'weekly', period_start: '2026-10-19' });
  assert.equal(calls[2].path, '/api/v1/reports/report%2Fa');
  assert.equal(calls[3].path, '/api/v1/reports/report%2Fa/sources?limit=20&cursor=cursor%26next');
  assert.equal(calls[4].path, '/api/v1/reports/report%2Fa/rebuild');
  assert.equal(calls[4].method, 'POST'); assert.equal(calls[4].body, undefined);
});
test('retrying a plan sends the same operation key and frozen body', async () => {
  const calls = [];
  const api = new FamilyApi(async (path, options) => {
    calls.push({ path, ...options }); if (calls.length === 1) throw new Error('network');
    return { status: 200, body: { data: { id: 'plan/a' } } };
  });
  const body = { student_id: 's', report_id: 'r', type: 'monthly', period_start: '2026-10-01', max_questions: 20, max_minutes: 20, minutes_per_question: 2 };
  await assert.rejects(api.generatePlan(body, 'same-key'), { status: 0 });
  await api.generatePlan(body, 'same-key'); await api.plan('plan/a');
  assert.equal(calls[0].headers['Idempotency-Key'], 'same-key');
  assert.deepEqual(calls[0], calls[1]); assert.equal(calls[2].path, '/api/v1/review-plans/plan%2Fa');
});

test('plan history encodes student and cursor and sends only the selected period filter', async () => {
  const calls = [];
  const api = new FamilyApi(async (path, options) => {
    calls.push({ path, ...options });
    return { status: 200, body: { data: { items: [], next_cursor: null } } };
  });
  await api.plans('student/a');
  await api.plans('student/a', 'weekly', 'cursor&next');
  await api.plans('student/a', 'monthly');
  assert.deepEqual(calls.map(c => c.path), [
    '/api/v1/review-plans?limit=20&student_id=student%2Fa',
    '/api/v1/review-plans?limit=20&cursor=cursor%26next&student_id=student%2Fa&type=weekly',
    '/api/v1/review-plans?limit=20&student_id=student%2Fa&type=monthly',
  ]);
  assert.ok(calls.every(c => c.method === 'GET' && c.body === undefined));
});
