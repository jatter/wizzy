import type { ReportType } from './api';

export function beijingDate(now = Date.now()) {
  return new Date(now + 8 * 60 * 60 * 1000).toISOString().slice(0, 10);
}
export function reportPeriod(value: string, type: ReportType) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const [year, month, day] = value.split('-').map(Number);
  if (year < 2000 || year > 9998) return null;
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.toISOString().slice(0, 10) !== value) return null;
  if (type === 'weekly') date.setUTCDate(date.getUTCDate() - (date.getUTCDay() + 6) % 7);
  else date.setUTCDate(1);
  if (date.getUTCFullYear() < 2000) return null;
  const start = date.toISOString().slice(0, 10);
  if (type === 'weekly') date.setUTCDate(date.getUTCDate() + 7);
  else date.setUTCMonth(date.getUTCMonth() + 1);
  const end = date.toISOString().slice(0, 10);
  date.setUTCDate(date.getUTCDate() - 1);
  return { start, end, last: date.toISOString().slice(0, 10) };
}
export function accuracy(value: number | null) {
  return value === null ? '暂无有效样本' : (value * 100).toFixed(1) + '%';
}
