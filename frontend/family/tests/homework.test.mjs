import test from 'node:test';
import assert from 'node:assert/strict';
import { HomeworkBatch } from '../src/homework.ts';

const image = name => ({ id: name, name, size: 3, type: 'image/png', preview: '', read: async () => new ArrayBuffer(3), release() {} });
function harness() {
  const calls = []; let serial = 0;
  const api = { generation: 0,
    async uploadIntent(...args) { calls.push(['intent', ...args]); return { media_id: 'media-' + ++serial }; },
    async upload(...args) { calls.push(['upload', ...args]); },
    async completeMedia(...args) { calls.push(['complete', ...args]); },
    async submit(body, key) { calls.push(['submit', structuredClone(body), key]); return { submission_id: 'saved' }; },
  };
  return { api, calls, batch: new HomeworkBatch(api, 'student') };
}
test('lost receipt retries exact body/key, freezes edits and preserves page order', async () => {
  const { api, calls, batch } = harness();
  batch.add([image('one'), image('two')]); batch.move(1, -1);
  const original = api.submit; let lost = true;
  api.submit = async (...args) => { const value = await original(...args); if (lost) { lost = false; throw new Error('offline'); } return value; };
  await assert.rejects(batch.send(false)); assert.equal(calls.length, 0);
  await assert.rejects(batch.send(true), /offline/);
  batch.move(0, 1); batch.remove(0); assert.throws(() => batch.add([image('three')]));
  await batch.send(true);
  const submissions = calls.filter(call => call[0] === 'submit');
  assert.deepEqual(submissions[0], submissions[1]);
  assert.deepEqual(submissions[0][1].media_ids, ['media-1', 'media-2']);
  assert.equal(batch.pages[0].name, 'two');
  assert.equal(calls.filter(call => call[0] === 'upload').length, 2);
});
test('failed page gets fresh upload intent while ready pages survive retry', async () => {
  const { api, calls, batch } = harness(); let fail = true;
  const original = api.completeMedia;
  api.completeMedia = async id => { if (id === 'media-2' && fail) { fail = false; throw new Error('offline'); } await original(id); };
  batch.add([image('one'), image('two')]); await assert.rejects(batch.send(true));
  assert.equal(batch.frozen, null); await batch.send(true);
  assert.deepEqual(calls.filter(call => call[0] === 'submit')[0][1].media_ids, ['media-1', 'media-3']);
});
test('switching student or session during file read stops all network calls', async () => {
  for (const invalidate of [({ batch }) => batch.dispose(), ({ api }) => api.generation++]) {
    const context = harness(); let finish;
    const source = image('one'); source.read = () => new Promise(resolve => finish = resolve);
    context.batch.add([source]); const pending = context.batch.send(true);
    invalidate(context); finish(new ArrayBuffer(3));
    await assert.rejects(pending, { code: 'stale' }); assert.equal(context.calls.length, 0);
  }
});
test('deactivation during upload prevents completion and submission', async () => {
  const { api, calls, batch } = harness();
  api.upload = async () => { batch.dispose(); };
  batch.add([image('one')]); await assert.rejects(batch.send(true), { code: 'stale' });
  assert.equal(calls.some(call => call[0] === 'submit' || call[0] === 'complete'), false);
});
test('rejects oversized, invalid and excessive images before changing the batch', () => {
  const { batch } = harness();
  for (const images of [[{ ...image('one'), size: 10485761 }], [{ ...image('one'), type: 'application/pdf' }], Array.from({ length: 11 }, (_, i) => image(String(i)))]) {
    assert.throws(() => batch.add(images)); assert.equal(batch.pages.length, 0);
  }
  batch.add(Array.from({ length: 10 }, (_, i) => image('ok-' + i)));
  assert.equal(batch.pages.length, 10);
});


test('editing an uploaded page after a later failure releases its preview and uploads the replacement', async () => {
  const { api, calls, batch } = harness(); let released = 0, fail = true;
  const original = api.completeMedia;
  api.completeMedia = async id => { if (id === 'media-2' && fail) { fail = false; throw new Error('offline'); } await original(id); };
  batch.add([{ ...image('one'), release() { released++; } }, image('two')]);
  await assert.rejects(batch.send(true), /offline/);
  assert.equal(batch.pages[0].mediaId, 'media-1');
  batch.replace(0, image('edited'));
  assert.equal(released, 1);
  assert.equal(batch.pages[0].mediaId, undefined);
  assert.equal(batch.pages[0].phase, 'waiting');
  await batch.send(true);
  assert.deepEqual(calls.filter(call => call[0] === 'submit')[0][1].media_ids, ['media-3', 'media-4']);
  assert.equal(calls.filter(call => call[0] === 'upload').length, 4);
  assert.throws(() => batch.replace(0, image('late')), /提交重试/);
  batch.dispose(); assert.equal(released, 1);
});

test('subject is preserved with the exact submission body when a receipt is lost', async () => {
  const { api, calls, batch } = harness();
  batch.add([image('english-one'), image('english-two')]);
  const submit = api.submit; let first = true;
  api.submit = async (...args) => { const result = await submit(...args); if (first) { first = false; throw new Error('lost receipt'); } return result; };
  await assert.rejects(batch.send(true, () => {}, 'english'), /lost receipt/);
  await batch.send(true, () => {}, 'chinese');
  const attempts = calls.filter(call => call[0] === 'submit');
  assert.equal(attempts[0][1].subject, 'english');
  assert.deepEqual(attempts[0], attempts[1]);
});

test('mixed-image homework defaults to automatic subject detection', async () => {
  const { batch, calls } = harness();
  batch.add([image('math'), image('english'), image('chinese')]);
  await batch.send(true);
  assert.equal(calls.find(call => call[0] === 'submit')[1].subject, 'auto');
});

test('dictation mode and reference stay frozen together through a lost receipt', async () => {
  const { api, calls, batch } = harness();
  batch.add([image('dictation')]);
  const submit = api.submit; let lost = true;
  api.submit = async (...args) => { const saved = await submit(...args); if (lost) { lost = false; throw new Error('offline'); } return saved; };
  await assert.rejects(batch.send(true, () => {}, 'auto', 'dictation', '第1张：\n1. whether\n2. take care of'), /offline/);
  await batch.send(true, () => {}, 'auto', 'exercise', 'changed');
  const attempts = calls.filter(call => call[0] === 'submit');
  assert.equal(attempts[0][1].activity_type, 'dictation');
  assert.equal(attempts[0][1].reference_text, '第1张：\n1. whether\n2. take care of');
  assert.deepEqual(attempts[0], attempts[1]);
});
