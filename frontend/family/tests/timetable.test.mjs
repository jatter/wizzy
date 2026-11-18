import test from 'node:test';
import assert from 'node:assert/strict';
import { activeChanges, dateValue, lessonError, shiftDate, templateError, termError, weekStart } from '../src/timetable.ts';
import { FamilyApi } from '../src/api.ts';

const lesson = { id: 'slot', weekday: 1, weeks: 'odd', period: 1, subject: '数学', start_time: null, end_time: null, room: null };
const term = { name: '秋季', start_date: '2026-10-26', end_date: '2027-01-31', week_starts_on: 1 };
test('timetable dates reject invalid calendar days and support configurable week boundaries', () => {
  for (const value of ['2026-02-29', '2026-02-30', '0000-01-01', '26-10-26', '']) assert.equal(dateValue(value), null);
  assert.notEqual(dateValue('2024-02-29'), null);
  assert.equal(shiftDate('2026-12-31', 1), '2027-01-01');
  assert.equal(weekStart('2027-01-01', 1), '2026-12-28');
  assert.equal(weekStart('2027-01-01', 7), '2026-12-27');
  assert.equal(weekStart('2026-10-28', 3), '2026-10-28');
});
test('term dates include both endpoints and at most 370 days', () => {
  assert.equal(termError(term), '');
  assert.equal(termError({ ...term, end_date: term.start_date }), '');
  assert.equal(termError({ ...term, end_date: shiftDate(term.start_date, 369) }), '');
  for (const change of [{ name: ' ' }, { end_date: '2026-10-25' }, { end_date: shiftDate(term.start_date, 370) }, { week_starts_on: 0 }, { week_starts_on: 1.5 }]) assert.ok(termError({ ...term, ...change }));
});
test('course validation accepts optional paired times and rejects incomplete or overlapping bounds', () => {
  assert.equal(lessonError(lesson), '');
  assert.equal(lessonError({ ...lesson, start_time: '08:00', end_time: '08:40' }), '');
  for (const change of [{ subject: '' }, { subject: ' ' }, { period: 0 }, { period: 21 }, { period: 1.5 }, { start_time: '08:00' }, { start_time: '24:00', end_time: '24:01' }, { start_time: '08:40', end_time: '08:00' }, { start_time: '08:00', end_time: '08:00' }, { room: 'a'.repeat(101) }]) assert.ok(lessonError({ ...lesson, ...change }));
  assert.equal(templateError([]), '');
  assert.equal(templateError([lesson]), '');
  assert.ok(templateError([{ ...lesson, weeks: 'invalid' }]));
  assert.ok(templateError([{ ...lesson, weekday: 8 }]));
  assert.ok(templateError(Array.from({ length: 101 }, () => lesson)));
});
test('latest override wins per original instance and restore removes an active change', () => {
  const move = { id: 'a', slot_id: 'slot', source_date: '2026-10-26', action: 'move' };
  const cancel = { ...move, id: 'b', action: 'cancel' };
  const other = { ...move, id: 'c', source_date: '2026-11-02' };
  assert.deepEqual(activeChanges([move, cancel, other]), [cancel, other]);
  assert.deepEqual(activeChanges([move, cancel, other, { ...cancel, id: 'd', action: 'restore' }]), [other]);
});
test('timetable API preserves revisions, dates, and explicit conflict confirmation', async () => {
  const calls = [];
  const api = new FamilyApi(async (path, options) => { calls.push({ path, ...options }); return { status: 200, body: { data: {} } }; });
  await api.terms('student/a', 'a&b'); await api.createTerm('student/a', term);
  await api.timetable('term/a'); await api.lessons('term/a', '2026-10-26', '2026-11-01');
  const template = { expected_revision: 3, slots: [lesson], confirm_conflicts: true };
  const override = { term_id: 'term/a', expected_revision: 4, slot_id: 'slot', source_date: '2026-10-26', action: 'cancel', reason: '学校放假', confirm_conflicts: false };
  await api.saveTimetable('term/a', template); await api.changeLesson(override);
  assert.equal(calls[0].path, '/api/v1/students/student%2Fa/terms?limit=20&cursor=a%26b');
  assert.deepEqual(calls[1].body, term);
  assert.equal(calls[2].path, '/api/v1/timetables/term%2Fa');
  assert.equal(calls[3].path, '/api/v1/timetables/term%2Fa/instances?start_date=2026-10-26&end_date=2026-11-01');
  assert.equal(calls[4].method, 'PUT'); assert.deepEqual(calls[4].body, template);
  assert.equal(calls[5].path, '/api/v1/lesson-overrides'); assert.deepEqual(calls[5].body, override);
});
test('conflict errors expose only allowlisted dates and reasons, never server messages or identifiers', async () => {
  const api = new FamilyApi(async () => ({ status: 409, body: { error: { code: 'schedule_conflict', message: 'secret', field_errors: [
    { date: '2026-10-26', reason: 'same_period', slot_ids: ['secret'] },
    { date: '2026-10-27', reason: 'time_overlap', instance_ids: ['secret'] },
    { date: 'secret', reason: 'same_period' }, { date: '2026-10-28', reason: 'secret' }, null,
  ] } } }));
  await assert.rejects(api.timetable('term'), error => {
    assert.equal(error.code, 'schedule_conflict');
    assert.deepEqual(error.conflicts, [{ date: '2026-10-26', reason: 'same_period' }, { date: '2026-10-27', reason: 'time_overlap' }]);
    assert.ok(!JSON.stringify(error).includes('secret')); assert.ok(!error.message.includes('secret')); return true;
  });
});
