import { ApiError, operationId } from './api.ts';
import type { FamilyApi } from './api.ts';
import { validateImages } from './homework.ts';
import type { HomeworkImage, UploadPage } from './homework.ts';
import type { DailySubject, HomeworkKind, HomeworkFields, HomeworkDay, HomeworkDraft, ImportBody, HomeworkImport } from './daily-homework-types.ts';
export const subjects: Record<DailySubject, string> = { chinese: '语文', math: '数学', english: '英语', other: '其他' };
export const kinds: Record<HomeworkKind, string> = { written: '书写', recitation: '背诵', reading: '阅读', preview: '预习', preparation: '准备事项', other: '其他' };
export const states = { pending: '未标完成', in_progress: '进行中', submitted: '已提交待检查', completed: '已标完成', exempted: '免做' };
export const importStates: Record<string, string> = { queued: '等待识别', processing: '正在识别', review_ready: '待核对', partial: '部分图片失败', failed: '识别失败', partially_applied: '部分已入清单', applied: '已入清单', discarded: '已结束导入', cancelled: '已取消' };
export function itemFields(item?: HomeworkFields): HomeworkFields {
  return item ? JSON.parse(JSON.stringify({ subject: item.subject, item_type: item.item_type, title: item.title, raw_text: item.raw_text, locator: item.locator, due_at: item.due_at })) :
    { subject: 'chinese', item_type: 'written', title: '', raw_text: '', locator: {}, due_at: null };
}
const subjectName: Record<string, DailySubject> = { 语文: 'chinese', 语: 'chinese', 数学: 'math', 数: 'math', 英语: 'english', 英: 'english', 其他: 'other' };

function clip(value: string, max: number) { return value.length > max ? value.slice(0, max) : value; }
function addNote(notes: string[], note: string) { if (note && !notes.includes(note)) notes.push(note); }
function normalizePaste(source: string) {
  return source.replace(/\r\n/g, '\n').replace(/[０-９]/g, char => String.fromCharCode(char.charCodeAt(0) - 0xFEE0)).replace(/：/g, ':').replace(/；/g, ';').replace(/[ \t\u3000]+/g, ' ').trim();
}
function validDay(year: number, month: number, day: number) {
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
}
function readDue(fragment: string, year: number) {
  const time = fragment.match(/(\d{1,2}):(\d{2})/);
  const full = fragment.match(/(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})/);
  const monthDay = fragment.match(/(\d{1,2})月(\d{1,2})日/);
  if (!full && !monthDay) return null;
  const resolvedYear = full ? Number(full[1]) : year;
  const month = Number(full ? full[2] : monthDay![1]);
  const day = Number(full ? full[3] : monthDay![2]);
  if (!validDay(resolvedYear, month, day)) return null;
  const date = `${resolvedYear}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
  if (!time) return { date, time: '' };
  const hour = Number(time[1]), minute = Number(time[2]);
  if (hour > 23 || minute > 59) return { date, time: '' };
  return { date, time: `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}` };
}
function isContextDate(line: string) {
  return /^(?:星期[一二三四五六日天]|周[一二三四五六日])?\s*(?:20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}日?|\d{1,2}月\d{1,2}日)(?:\s*(?:星期[一二三四五六日天]|周[一二三四五六日]))?\s*$/.test(line);
}
function isNoise(line: string) { return /^(?:今日作业|今天作业|今晚作业|周末作业|家庭作业|晚托作业|作业安排|作业)[:]?$/.test(line); }
function isNone(text: string) { return /^(?:无|没有|无作业|今天无|本日无|暂无)[。.!！]?$/.test(text.replace(/\s/g, '')); }
function isPreparation(text: string) {
  if (/默写|背诵|预习|抄写|练习|课文|第\s*\d+\s*课|[Pp]\s*\d+/.test(text)) return false;
  return /^(?:请?准备|带上|带来|记得带|自备)/.test(text);
}
function boundary(text: string, index: number) {
  if (index === 0) return true;
  const previous = text[index - 1]!;
  return previous === '\n' || /[^\u4e00-\u9fffA-Za-z0-9]/.test(previous);
}
function sectionsOf(text: string) {
  const marks: { index: number; subject: DailySubject; end: number }[] = [];
  for (const match of text.matchAll(/(语文|数学|英语|其他|语|数|英)\s*:/g)) {
    const index = match.index ?? 0;
    if (!boundary(text, index)) continue;
    marks.push({ index, subject: subjectName[match[1]!]!, end: index + match[0].length });
  }
  if (!marks.length) return [{ subject: null as DailySubject | null, body: text }];
  const sections: { subject: DailySubject | null; body: string }[] = [];
  const lead = text.slice(0, marks[0]!.index).trim();
  if (lead) sections.push({ subject: null, body: lead });
  marks.forEach((mark, index) => {
    const end = index + 1 < marks.length ? marks[index + 1]!.index : text.length;
    sections.push({ subject: mark.subject, body: text.slice(mark.end, end).trim() });
  });
  return sections;
}
function splitOutside(text: string, delimiters: string) {
  const parts: string[] = [];
  let current = '', depth = 0;
  for (const char of text) {
    if (char === '（' || char === '(') depth++;
    else if ((char === '）' || char === ')') && depth) depth--;
    if (depth === 0 && delimiters.includes(char)) { if (current.trim()) parts.push(current.trim()); current = ''; continue; }
    current += char;
  }
  if (current.trim()) parts.push(current.trim());
  return parts;
}
function isPageToken(text: string) {
  return /^(?:[Pp]\s*\d+(?:\s*[-~～到至]\s*(?:[Pp]\s*)?\d+)?(?:\s*页)?|第\s*\d+\s*页(?:\s*[-~～到至]\s*第?\s*\d+\s*页)?)$/.test(text.trim());
}
function splitTasks(body: string) {
  const tasks: string[] = [];
  for (const line of body.split(/\n+/)) {
    const stripped = line.trim().replace(/^(?:[（(]\d+[）)]|\d+[.、．)])\s*/, '');
    if (!stripped) continue;
    for (const chunk of splitOutside(stripped, ';；')) {
      const bits = splitOutside(chunk, '、，,');
      let bucket = '';
      for (const bit of bits) {
        if (!bucket) bucket = bit;
        else if (isPageToken(bit)) bucket += '、' + bit;
        else { tasks.push(bucket); bucket = bit; }
      }
      if (bucket) tasks.push(bucket);
    }
  }
  return tasks.map(task => task.trim()).filter(Boolean);
}
function kindOf(text: string): HomeworkKind {
  if (isPreparation(text)) return 'preparation';
  if (/背诵|背熟|会背|背课文/.test(text)) return 'recitation';
  if (/预习/.test(text)) return 'preview';
  if (/阅读|朗读|看书|晨读|读课文/.test(text)) return 'reading';
  if (/抄写|默写|写字|练字|口算|计算|订正|练习|完成|做|写|笔算/.test(text)) return 'written';
  if (/课|册|页|题|单元|U\d/i.test(text)) return 'written';
  return 'other';
}
function compactRange(value: string) { return value.replace(/\s/g, '').replace(/到|至|~/g, '-'); }
function locatorOf(text: string) {
  const locator: HomeworkFields['locator'] = {};
  const book = text.match(/《[^》]{1,30}》|大册|小册|练习册|口算本|口算|语文本|作文本|1\s*\+\s*1|2\s*\+\s*1/);
  if (book) locator.book_alias = book[0].replace(/\s/g, '');
  const lesson = text.match(/第\s*(\d{1,3})\s*课/);
  if (lesson) locator.lesson = lesson[1]!;
  const unit = text.match(/U\s*\d+(?:\s*[-~～到至]\s*U?\s*\d+)?|第\s*\d+\s*单元/i);
  if (unit) locator.units = compactRange(unit[0]).replace(/u/g, 'U');
  const pages = [...text.matchAll(/[Pp]\s*\d+(?:\s*[-~～到至]\s*(?:[Pp]\s*)?\d+)?(?:\s*页)?|第\s*\d+\s*页(?:\s*[-~～到至]\s*第?\s*\d+\s*页)?/g)].map(match => compactRange(match[0]));
  if (pages.length) locator.pages = pages.join('、');
  const question = text.match(/(?:第\s*)?\d+\s*[-~～到至]\s*\d+\s*题|第\s*\d+\s*题/);
  if (question) locator.question_range = question[0].replace(/\s/g, '');
  const repetition = text.match(/\d+\s*遍|[一二两三四五六七八九十]+\s*遍|\d+\s*次|[一二两三四五六七八九十]+\s*次/);
  if (repetition) locator.repetition_raw = repetition[0].replace(/\s/g, '');
  return locator;
}
function quantityPages(text: string) {
  return [...text.matchAll(/(\d+|[一二两俩三四五六七八九十]+)\s*页/g)].flatMap(match => {
    const index = match.index ?? 0;
    return /[Pp第]/.test(text.slice(Math.max(0, index - 1), index)) ? [] : [match[0].replace(/\s/g, '')];
  });
}
function takeSchedule(text: string, year: number) {
  const notes: string[] = [], kept: string[] = [];
  let due: string | null = null;
  const remember = (found: { date: string; time: string } | null) => {
    if (!found) return false;
    if (found.time) due = `${found.date}T${found.time}:00+08:00`;
    else addNote(notes, `识别到截止日期 ${found.date}，没有时刻，未自动填写。`);
    return true;
  };
  for (const rawLine of text.split('\n')) {
    const line = rawLine.trim();
    if (!line || isContextDate(line) || isNoise(line)) continue;
    const inline = line.match(/[，,、;；]?\s*(?:截止|上交|交作业)\s*:?\s*(?:20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}日?|\d{1,2}月\d{1,2}日)(?:\s*\d{1,2}:\d{2})?/);
    if (inline && remember(readDue(inline[0], year))) {
      const remain = line.replace(inline[0], '').trim();
      if (remain) kept.push(remain);
      continue;
    }
    if (/截止|上交|交作业/.test(line) && remember(readDue(line, year))) continue;
    kept.push(line);
  }
  return { text: kept.join('\n'), due, notes };
}

// Paste a message, fill the fields. This only structures text; it never records completion.
export function recognizeHomeworkText(source: string, year = new Date().getFullYear()) {
  const notes: string[] = [];
  const text = normalizePaste(source);
  if (!text) return { items: [] as HomeworkFields[], notes: ['请先粘贴作业文字。'] };
  const schedule = takeSchedule(text, year);
  notes.push(...schedule.notes);
  const items: HomeworkFields[] = [];
  let assumedChinese = false;
  for (const section of sectionsOf(schedule.text)) {
    const label = section.subject ? subjects[section.subject] : '这段';
    if (!section.body.trim()) { addNote(notes, `${label}没有写内容。空白不会当成无作业。`); continue; }
    if (isNone(section.body)) { addNote(notes, `${label}原文写的是无作业，没有建成任务。请到核对范围确认。`); continue; }
    for (const task of splitTasks(section.body)) {
      if (isNone(task)) { addNote(notes, `${label}原文写的是无作业，没有建成任务。请到核对范围确认。`); continue; }
      let subject = section.subject;
      if (isPreparation(task)) {
        if (subject && subject !== 'other') addNote(notes, `「${task}」按准备事项归入其他。`);
        subject = 'other';
      } else if (!subject) { subject = 'chinese'; assumedChinese = true; }
      const locator = locatorOf(task);
      for (const quantity of quantityPages(task)) if (!locator.pages) addNote(notes, `「${task}」里的“${quantity}”按数量保留，没有写成页码。`);
      const title = clip(task.replace(/[。.!！]+$/g, '').trim(), 1000);
      if (!title) continue;
      items.push({ subject, item_type: kindOf(task), title, raw_text: clip(task, 4000), locator, due_at: schedule.due });
      if (items.length >= 30) { addNote(notes, '只填入前30项，其余请再识别一次。'); return { items, notes }; }
    }
  }
  if (assumedChinese) addNote(notes, '有内容没有标明科目，已按语文填入，请核对。');
  if (!items.length && !notes.length) notes.push('没有识别出可保存的作业。');
  return { items, notes };
}

export function dailySummary(day: HomeworkDay | null) {
  if (!day) return '正在读取清单';
  if (day.summary.no_homework) return '今日无作业';
  if (day.summary.all_verified) return '今日作业已核对完成';
  const prefix = day.items.length ? `已标完成 ${day.summary.completed}/${day.summary.required} 项` : '未录入';
  return prefix + (day.scope_state === 'complete' && !day.pending_imports ? ' · 清单已核对完整' : ' · 清单待补充');
}
export function mergeDraft(local: HomeworkDraft, remote: HomeworkDraft, applied: Record<string, string>): HomeworkDraft {
  const merge = <T extends { id: string }>(previous: T[], fresh: T[]) => fresh.map(entry => applied[entry.id] ? entry : previous.find(old => old.id === entry.id) ?? entry);
  return JSON.parse(JSON.stringify({ items: merge(local.items, remote.items), no_homework: merge(local.no_homework, remote.no_homework), notes: remote.notes }));
}
// Uploads and operation IDs survive response loss. Edits freeze once enqueue may have committed.
export class HomeworkImportBatch {
  pages: UploadPage[] = []; frozen: ImportBody | null = null; running = false; active = true;
  private api: FamilyApi; private student: string; private valid: () => boolean;
  constructor(api: FamilyApi, student: string, valid = () => true) { this.api = api; this.student = student; this.valid = valid; }
  check() { if (!this.active || !this.valid()) throw new ApiError(0, 'stale'); }
  editable() { this.check(); if (this.running || this.frozen) throw new Error('请先重试确认本次识别提交。'); }
  add(images: HomeworkImage[]) { this.editable(); validateImages(images, this.pages.length); this.pages.push(...images.map(image => ({ ...image, phase: 'waiting' as const }))); }
  move(index: number, offset: number) { if (this.running || this.frozen) return; const next = index + offset; if (next >= 0 && next < this.pages.length) [this.pages[index], this.pages[next]] = [this.pages[next]!, this.pages[index]!]; }
  remove(index: number) { this.editable(); this.pages.splice(index, 1).forEach(page => page.release()); }
  replace(index: number, image: HomeworkImage) { this.editable(); validateImages([image], this.pages.length - 1); const old = this.pages[index]; if (!old) throw new Error('图片已移除'); this.pages[index] = { ...image, phase: 'waiting' }; old.release(); }
  dispose() { this.active = false; this.pages.forEach(page => page.release()); this.pages = []; }
  async send(source: string, date: string | null, changed = () => {}): Promise<HomeworkImport> {
    this.check(); if (this.running || !this.pages.length) throw new Error('请先选择作业清单图片。');
    const generation = this.api.generation;
    const check = () => { this.check(); if (generation !== this.api.generation) throw new ApiError(0, 'stale'); };
    this.running = true; changed();
    try {
      if (!this.frozen) {
        for (const page of this.pages) {
          check(); if (page.mediaId) continue;
          page.phase = 'reading'; changed(); const bytes = await page.read(); check();
          if (bytes.byteLength !== page.size) throw new Error('图片已变化，请重新选择。');
          const intent = await this.api.uploadIntent(this.student, page.type, bytes.byteLength); check();
          page.phase = 'uploading'; changed(); await this.api.upload(intent.media_id, bytes, page.type); check();
          page.phase = 'checking'; changed(); await this.api.completeMedia(intent.media_id); check();
          page.mediaId = intent.media_id; page.phase = 'ready'; changed();
        }
        this.frozen = { student_id: this.student, source_kind: 'manual_image', source_label: source.trim() || '群作业截图', target_date: date || null,
          images: this.pages.map(page => ({ media_id: page.mediaId!, crop_rect: null })), client_request_id: operationId() };
      }
      const result = await this.api.createHomeworkImport(this.frozen); check(); return result;
    } finally { this.running = false; changed(); }
  }
}
