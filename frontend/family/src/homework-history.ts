export function beijingDate(value = new Date()) {
  return new Date(value.getTime() + 8 * 60 * 60 * 1000).toISOString().slice(0, 10);
}
export function homeworkDays(items: { date: string; count: number }[], now = new Date()) {
  const today = beijingDate(now), yesterday = beijingDate(new Date(now.getTime() - 86400000));
  return [...new Set([today, yesterday, ...items.map(item => item.date)])].sort().reverse().map(date => ({
    date, label: date === today ? '今天' : date === yesterday ? '昨天' : date.slice(5).replace('-', '月') + '日',
    count: items.find(item => item.date === date)?.count ?? 0,
  }));
}
