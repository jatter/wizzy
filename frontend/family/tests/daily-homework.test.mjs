import test from 'node:test';
import assert from 'node:assert/strict';
import { FamilyApi } from '../src/api.ts';
import { HomeworkImportBatch, dailySummary, mergeDraft, itemFields, recognizeHomeworkText } from '../src/daily-homework.ts';

const picture = id => ({ id, name: id, size: 3, type: 'image/png', preview: '', read: async () => new ArrayBuffer(3), release() {} });
test('daily APIs encode child/date/cursors and preserve write operation payloads', async () => {
  const calls = [];
  const api = new FamilyApi(async (path, options) => { calls.push({ path, ...options }); return { status: 200, body: { data: {} } }; });
  await api.homeworkDay('s/a', '2026-11-13');
  await api.homeworkImports('s/a', 'next&one');
  const body = { client_request_id: 'operation', expected_revision: 2, completed: false, reason: '还缺一页' };
  await api.verifyHomework('i/a', body);
  await api.retryHomeworkImport('r/a', { client_request_id: 'retry', expected_revision: 3, image_ids: ['one'], confirm_unknown_cost: true });
  assert.equal(calls[0].path, '/api/v1/students/s%2Fa/homework-days/2026-11-13');
  assert.equal(calls[1].path, '/api/v1/homework-imports?limit=20&cursor=next%26one&student_id=s%2Fa');
  assert.deepEqual(calls[2].body, body);
  assert.deepEqual(calls[3].body.image_ids, ['one']);
});
test('lost import response freezes original image order/date/source and reuses exact UUID', async () => {
  const calls = []; let serial = 0, fail = true;
  const api = { generation: 0, uploadIntent: async () => ({ media_id: String(++serial) }), upload: async () => {}, completeMedia: async () => {},
    async createHomeworkImport(body) { calls.push(structuredClone(body)); if (fail) { fail = false; throw new Error('lost'); } return { id: 'saved' }; } };
  const batch = new HomeworkImportBatch(api, 'child');
  batch.add([picture('one'), picture('two')]); batch.move(1, -1);
  await assert.rejects(batch.send('六10班', '2026-11-13'));
  assert.throws(() => batch.add([picture('three')]));
  await batch.send('changed', '2026-11-14');
  assert.deepEqual(calls[0], calls[1]);
  assert.equal(serial, 2); assert.equal(calls[0].source_label, '六10班');
  assert.equal(calls[0].target_date, '2026-11-13'); assert.equal(batch.pages[0].name, 'two');
});
test('switching identities during import upload prevents enqueue', async () => {
  let queued = false;
  const api = { generation: 0, uploadIntent: async () => ({ media_id: 'one' }), upload: async () => { api.generation++; }, completeMedia: async () => {}, createHomeworkImport: async () => { queued = true; } };
  const batch = new HomeworkImportBatch(api, 'child'); batch.add([picture('one')]);
  await assert.rejects(batch.send('班级', null), { code: 'stale' }); assert.equal(queued, false);
});
test('new OCR candidates append while local human changes and server applied versions survive', () => {
  const local = { items: [{ id: 'one', title: '家长改过', decision: 'include' }], no_homework: [], notes: [] };
  const remote = { items: [{ id: 'one', title: 'OCR原文' }, { id: 'two', title: '新图' }], no_homework: [{ id: 'none' }], notes: ['新疑点'] };
  assert.equal(mergeDraft(local, remote, {}).items[0].title, '家长改过');
  assert.equal(mergeDraft(local, remote, {}).items.length, 2);
  assert.equal(mergeDraft(local, remote, { one: 'item' }).items[0].title, 'OCR原文');
  assert.equal(local.items.length, 1);
});
test('summary never claims complete for empty/unrecorded or pending sources', () => {
  const day = { items: [], scope_state: 'incomplete', pending_imports: 0, summary: { required: 0, completed: 0, verified: 0, no_homework: false, all_verified: false } };
  assert.equal(dailySummary(day), '未录入 · 清单待补充');
  assert.equal(dailySummary({ ...day, summary: { ...day.summary, no_homework: true } }), '今日无作业');
  assert.equal(dailySummary({ ...day, items: [{}], summary: { ...day.summary, required: 2, completed: 1 } }), '已标完成 1/2 项 · 清单待补充');
});
test('pasted homework fills subjects, locators and deadline without treating quantity as pages', () => {
  const text = '11月15日 星期五\n语文：大册第15课、小册第15课、预习作文\n数学：练习册P13-P15\n数学：无\n英语：抄写U1单词3遍\n准备2本语文本\n截止：2026-11-16 20:30';
  const { items, notes } = recognizeHomeworkText(text, 2026);
  assert.deepEqual(items.map(item => [item.subject, item.item_type, item.title]), [
    ['chinese', 'written', '大册第15课'],
    ['chinese', 'written', '小册第15课'],
    ['chinese', 'preview', '预习作文'],
    ['math', 'written', '练习册P13-P15'],
    ['english', 'written', '抄写U1单词3遍'],
    ['other', 'preparation', '准备2本语文本'],
  ]);
  assert.equal(items[0].locator.book_alias, '大册');
  assert.equal(items[0].locator.lesson, '15');
  assert.equal(items[3].locator.pages, 'P13-P15');
  assert.equal(items[4].locator.units, 'U1');
  assert.equal(items[4].locator.repetition_raw, '3遍');
  assert.equal(items[0].due_at, '2026-11-16T20:30:00+08:00');
  assert.equal(items.some(item => item.title === '无'), false);
  assert.equal(notes.some(note => note.includes('无作业')), true);
  const quantity = recognizeHomeworkText('数学：口算2页\n做1+1每天1页', 2026);
  assert.equal(quantity.items[0].locator.pages ?? null, null);
  assert.equal(quantity.items[0].locator.book_alias, '口算');
  assert.equal(quantity.items[1].locator.book_alias, '1+1');
  assert.equal(quantity.items[1].locator.pages ?? null, null);
  assert.equal(recognizeHomeworkText('语文：', 2026).items.length, 0);
  assert.equal(recognizeHomeworkText('阅读课文', 2026).items[0].item_type, 'reading');
  assert.equal(recognizeHomeworkText('', 2026).notes[0], '请先粘贴作业文字。');
});
test('editable fields retain locator array ranges without rewriting page meaning', () => {
  const item = { subject: 'math', item_type: 'written', title: '小册2页', raw_text: '小册2页', locator: { pages: ['P3', 'P4'], book_id: null }, due_at: null };
  assert.deepEqual(itemFields(item).locator.pages, ['P3', 'P4']);
});
