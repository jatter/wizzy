import type { LessonChange, LessonFields, TermBody, TimetableSlot } from './api';

export const weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
export function dateValue(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith('0000-')) return null;
  const time = Date.parse(value + 'T00:00:00Z');
  return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === value ? time : null;
}
export function shiftDate(value: string, days: number) {
  return new Date(dateValue(value)! + days * 86400000).toISOString().slice(0, 10);
}
export function weekStart(value: string, startsOn: number) {
  const day = new Date(dateValue(value)!).getUTCDay() || 7;
  return shiftDate(value, -((day - startsOn + 7) % 7));
}
export function termError(body: TermBody) {
  const start = dateValue(body.start_date), end = dateValue(body.end_date);
  if (!body.name.trim() || body.name.length > 80) return '请填写 1–80 字的学期名称。';
  if (start === null || end === null || end < start || end - start >= 370 * 86400000) return '请填写有效的学期日期，结束不得早于开始，范围最多 370 天。';
  if (!Number.isInteger(body.week_starts_on) || body.week_starts_on < 1 || body.week_starts_on > 7) return '请选择有效的每周起始日。';
  return '';
}
export function lessonError(row: LessonFields) {
  if (!Number.isInteger(row.period) || row.period < 1 || row.period > 20) return '节次需为 1–20 的整数。';
  if (!row.subject.trim() || row.subject.length > 80) return '请填写 1–80 字的科目。';
  if ((row.room?.length ?? 0) > 100) return '教室最多 100 字。';
  const time = /^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/;
  if (row.start_time || row.end_time) {
    if (!row.start_time || !row.end_time || !time.test(row.start_time) || !time.test(row.end_time) || row.start_time >= row.end_time) return '上课和下课时间需成对填写，格式为 HH:MM，且上课早于下课。';
  }
  return '';
}
export function templateError(slots: TimetableSlot[]) {
  if (slots.length > 100) return '每周模板最多 100 节课程。';
  for (const [index, row] of slots.entries()) {
    const error = lessonError(row);
    if (error) return `课程 ${index + 1}：${error}`;
    if (!Number.isInteger(row.weekday) || row.weekday < 1 || row.weekday > 7 || !['all', 'odd', 'even'].includes(row.weeks)) return `课程 ${index + 1}：请选择星期和单双周。`;
  }
  return '';
}
export function activeChanges(history: LessonChange[]) {
  const latest = new Map<string, LessonChange>();
  for (const row of history) latest.set(row.slot_id + ':' + row.source_date, row);
  return [...latest.values()].filter(row => row.action !== 'restore');
}
