import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ApiError, FamilyApi, operationId } from '../src/api.ts';

const guardian = { audience: 'guardian', tenant_id: 'family', student_id: null, role: 'owner' };
const child = { audience: 'child', tenant_id: 'family', student_id: 'student', role: null };
const token = access_token => ({ access_token, token_type: 'bearer' });
const ok = data => ({ status: 200, body: { data } });
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
function harness(responses) {
  const calls = [];
  const api = new FamilyApi(async (path, options) => {
    calls.push({ path, ...options });
    assert.ok(responses.length, 'Unexpected request');
    const next = responses.shift();
    return typeof next === 'function' ? next() : next;
  });
  return { api, calls };
}

test('browser cookie sessions adopt identity without exposing a bearer token', async () => {
  const { api, calls } = harness([ok({ token_type: 'cookie' }), ok(guardian), ok({ items: [] })]);
  assert.deepEqual(await api.login('liyuhan', 'password'), guardian);
  assert.deepEqual(calls[0].body, { username: 'liyuhan', password: 'password' });
  await api.students();
  assert.ok(calls.every(call => call.headers.Authorization === undefined));
});

test('cookie logout does not send an empty bearer header that would bypass the cookie', async () => {
  const { api, calls } = harness([ok({ token_type: 'cookie' }), ok(guardian), ok({ revoked: true })]);
  await api.login('parent@example.test', 'password');
  await api.logout();
  assert.equal(calls.at(-1).headers.Authorization, undefined);
});

test('reload restores and validates browser identity, while an absent session stays signed out', async () => {
  const { api, calls } = harness([ok(child)]);
  assert.deepEqual(await api.resume(), child);
  assert.equal(calls[0].path, '/api/v1/auth/me');
  let expired = 0;
  const absent = new FamilyApi(async () => ({ status: 401, body: {} }), () => expired++);
  assert.equal(await absent.resume(), null);
  assert.equal(expired, 0);
  const invalid = harness([ok({ ...child, student_id: null })]);
  await assert.rejects(invalid.api.resume(), { status: 502 });
});

test('starting learning replaces guardian credentials with a verified child session', async () => {
  const { api, calls } = harness([ok(token('parent')), ok(guardian), ok(token('learner')), ok(child), ok({})]);
  await api.login('parent@example.test', 'password');
  assert.deepEqual(await api.startLearning('student'), child);
  await api.student('student');
  assert.equal(calls[2].path, '/api/v1/auth/start-learning');
  assert.deepEqual(calls[2].body, { student_id: 'student' });
  assert.equal(calls[2].headers.Authorization, 'Bearer parent');
  assert.equal(calls.at(-1).headers.Authorization, 'Bearer learner');
});

test('incorrect parent password does not invalidate the active child cookie session', async () => {
  let expired = 0;
  const api = new FamilyApi(async () => ({ status: 401, body: {} }), () => expired++);
  await assert.rejects(api.login('parent@example.test', 'wrong'), { status: 401 });
  assert.equal(expired, 0);
});

test('login rejection explains family credentials while authenticated reads still report expiry', async () => {
  const rejected = { status: 401, body: { error: { code: 'authentication_required', message: 'untrusted-server-value' } } };
  const { api } = harness([rejected, rejected]);
  await assert.rejects(api.login('parent@example.test', 'wrong'), error => error.status === 401 &&
    error.message.includes('家庭账号') && !error.message.includes('已失效') && !error.message.includes('untrusted-server-value'));
  await assert.rejects(api.students(), error => error.status === 401 && error.message.includes('已失效'));
});

test('registration adopts guardian identity and keeps credentials out of subsequent requests', async () => {
  const { api, calls } = harness([ok(token('private-token')), ok(guardian), ok({ items: [] })]);
  assert.deepEqual(await api.register('parent@example.test', 'long-passphrase'), guardian);
  await api.tenants();
  assert.deepEqual(calls[0].body, { username: 'parent@example.test', password: 'long-passphrase', privacy_version: '2026-10-25', accepted_privacy: true });
  assert.equal(calls[0].headers.Authorization, undefined);
  assert.equal(calls[1].headers.Authorization, 'Bearer private-token');
  assert.equal(calls[2].body, undefined);
  assert.equal(JSON.stringify(api), '{}');
});

test('child pairing checks audience and student binding before accepting a session', async () => {
  for (const identity of [guardian, { ...child, student_id: null }]) {
    const { api, calls } = harness([ok(token('child-token')), ok(identity), ok({ items: [] })]);
    await assert.rejects(api.pair('pair-code', '学习平板'), error => error instanceof ApiError && error.status === 403);
    await api.tenants();
    assert.equal(calls[2].headers.Authorization, undefined);
  }
  const { api, calls } = harness([ok(token('child-token')), ok(child)]);
  assert.deepEqual(await api.pair('pair-code', '学习平板'), child);
  assert.deepEqual(calls[0].body, { code: 'pair-code', device_name: '学习平板' });
});

test('invalid or incomplete token responses are rejected', async () => {
  for (const invalid of [null, {}, token(''), { access_token: 'token', token_type: 'basic' }]) {
    const { api, calls } = harness([ok(invalid)]);
    await assert.rejects(api.login('parent@example.test', 'password'), { status: 502 });
    assert.equal(calls.length, 1);
  }
});

test('tenant token adoption failure invalidates the visible session once', async () => {
  let expired = 0;
  const queue = [ok(token('first')), ok(guardian), ok(token('second')), { status: 503, body: null }, ok({ items: [] })];
  const calls = [];
  const api = new FamilyApi(async (path, options) => { calls.push({ path, ...options }); return queue.shift(); }, () => expired++);
  await api.login('p', 'password');
  await assert.rejects(api.activateTenant('other-family'), { status: 503 });
  assert.equal(expired, 1);
  await api.tenants();
  assert.equal(calls.at(-1).headers.Authorization, undefined);
});

test('clearing a session discards pending successful reads', async () => {
  const pending = deferred();
  const { api } = harness([() => pending.promise]);
  const read = api.students();
  api.clear(); pending.resolve(ok({ items: [{ id: 'old-student' }] }));
  await assert.rejects(read, { code: 'stale' });
});

test('a superseded login cannot replace the newer session', async () => {
  const pending = deferred();
  const { api, calls } = harness([() => pending.promise, ok(token('new')), ok(guardian), ok({ items: [] })]);
  const old = api.login('old', 'old-password');
  await api.login('new', 'new-password');
  pending.resolve(ok(token('old')));
  await assert.rejects(old, { code: 'stale' });
  await api.students();
  assert.equal(calls.at(-1).headers.Authorization, 'Bearer new');
});

test('late unauthorized responses cannot revoke a newer session', async () => {
  const pending = deferred(); let expired = 0;
  const queue = [() => pending.promise, ok(token('new')), ok(guardian), ok({ items: [] })];
  const api = new FamilyApi(async () => { const value = queue.shift(); return typeof value === 'function' ? value() : value; }, () => expired++);
  const old = api.students();
  await api.login('new', 'password');
  pending.resolve({ status: 401, body: {} });
  await assert.rejects(old, { code: 'stale' });
  assert.equal(expired, 0);
  await api.students();
});

test('current unauthorized response clears credentials and announces expiry once', async () => {
  let expired = 0; const calls = [];
  const queue = [ok(token('secret')), { status: 401, body: {} }, ok(guardian)];
  const api = new FamilyApi(async (_path, options) => { calls.push(options); return queue.shift(); }, () => expired++);
  await assert.rejects(api.login('p', 'password'), { status: 401 });
  assert.equal(expired, 1);
  await api.me();
  assert.equal(calls.at(-1).headers.Authorization, undefined);
});

test('logout forgets credentials immediately and late failure preserves a new login', async () => {
  const pending = deferred();
  const { api, calls } = harness([ok(token('old')), ok(guardian), () => pending.promise, ok(token('new')), ok(guardian), ok(guardian)]);
  await api.login('old', 'password');
  const logout = api.logout();
  assert.equal(calls[2].headers.Authorization, 'Bearer old');
  await api.login('new', 'password');
  assert.equal(calls[3].headers.Authorization, undefined);
  pending.reject(new Error('raw network secret'));
  await assert.rejects(logout, error => error.status === 0 && !error.message.includes('secret'));
  await api.me();
  assert.equal(calls.at(-1).headers.Authorization, 'Bearer new');
});

test('POST profile and draft preserve expected revisions and encode identifiers', async () => {
  const { api, calls } = harness([ok({}), ok({}), ok({})]);
  await api.updateStudent({ id: 'student/id', revision: 3 }, '新昵称', 4);
  await api.draft({ id: 'task/id', draft: { revision: 6 } }, '自己的思路');
  await api.attempt({ id: 'task/id', draft: { revision: 7 } }, 'submitted', 'stable-operation');
  assert.deepEqual(calls.map(({ path, method, body }) => ({ path, method, body })), [
    { path: '/api/v1/students/student%2Fid/profile', method: 'POST', body: { expected_revision: 3, nickname: '新昵称', grade: 4 } },
    { path: '/api/v1/review-tasks/task%2Fid/draft', method: 'POST', body: { expected_draft_revision: 6, student_answer: '自己的思路' } },
    { path: '/api/v1/review-tasks/task%2Fid/attempts', method: 'POST', body: { expected_draft_revision: 7, outcome: 'submitted' } },
  ]);
  assert.equal(calls[2].headers['Idempotency-Key'], 'stable-operation');
});

test('evaluation authorization is separate, versioned and idempotent', async () => {
  const { api, calls } = harness([ok({}), ok({})]);
  await api.serviceConsent('student', true);
  await api.decideEvaluation('student', { revision: 2 }, false, { version: 'policy-2' }, 'operation');
  assert.equal(calls[0].path, '/api/v1/consents');
  assert.deepEqual(calls[1].body, { operation_id: 'operation', expected_revision: 2, granted: false, policy_version: 'policy-2' });
  assert.equal(calls[1].path, '/api/v1/students/student/evaluation-consent');
});

test('pagination encodes opaque cursors without adding query parameters', async () => {
  const { api, calls } = harness([ok({}), ok({}), ok({})]);
  const cursor = 'a&student_id=other/+?';
  await api.students(cursor); await api.tasks('student', cursor); await api.mistakes('student', cursor);
  for (const call of calls) {
    const url = new URL(call.path, 'https://example.test');
    assert.deepEqual([...url.searchParams.keys()], ['limit', 'cursor']);
    assert.equal(url.searchParams.get('cursor'), cursor);
  }
});

test('safe errors exclude raw validation values and untrusted reference IDs', async () => {
  for (const request_id of ['raw-password', 'req_' + 'a'.repeat(32)]) {
    const { api } = harness([{ status: 422, body: { error: { message: 'raw-password', code: 'raw-password' }, request_id } }]);
    await assert.rejects(api.students(), error => error.status === 422 && !String(error).includes('raw-password') &&
      error.requestId === (request_id.startsWith('req_') ? request_id : ''));
  }
});

test('network failures and malformed envelopes become safe local errors', async () => {
  const { api } = harness([() => { throw new Error('private-server-data'); }]);
  await assert.rejects(api.students(), error => error.status === 0 && !error.message.includes('private-server-data'));
  for (const body of [null, 'not-json', {}, { error: {} }]) {
    const { api: invalid } = harness([{ status: 200, body }]);
    await assert.rejects(invalid.students(), { status: 502 });
  }
});

test('operation identifiers have UUID syntax', () => {
  assert.match(operationId(), /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
});

test('submission filters and identifiers cannot inject extra query parameters', async () => {
  const { api, calls } = harness([ok({})]);
  const student = 'student/&status=failed', cursor = 'opaque&subject=other';
  await api.submissions(student, cursor, { date_from: '2026-10-01', date_to: '2026-10-26', subject: 'math', status: 'cancelled' });
  const params = new URL(calls[0].path, 'https://example.test').searchParams;
  assert.deepEqual([...params.keys()], ['limit', 'cursor', 'student_id', 'date_from', 'date_to', 'subject', 'status']);
  assert.equal(params.get('student_id'), student); assert.equal(params.get('cursor'), cursor);
  assert.equal(params.get('status'), 'cancelled'); assert.equal(params.get('subject'), 'math');
  assert.equal(params.get('date_from'), '2026-10-01'); assert.equal(params.get('date_to'), '2026-10-26');
});

test('upload sends original bytes and MIME while submission preserves its idempotency key', async () => {
  const { api, calls } = harness([ok({}), ok({}), ok({}), ok({})]);
  const bytes = new Uint8Array([137, 80, 78, 71]).buffer;
  await api.uploadIntent('student', 'image/png', 4);
  await api.upload('media/id', bytes, 'image/png'); await api.completeMedia('media/id');
  const body = { student_id: 'student', subject: 'math', completed_attempt: true, media_ids: ['media/id'], client_request_id: 'request' };
  await api.submit(body, 'stable-key');
  assert.deepEqual(calls[0].body, { student_id: 'student', content_type: 'image/png', size_bytes: 4 });
  assert.equal(calls[1].path, '/api/v1/media/media%2Fid/upload');
  assert.equal(calls[1].method, 'PUT'); assert.equal(calls[1].body, bytes);
  assert.equal(calls[1].headers['Content-Type'], 'image/png');
  assert.equal(calls[2].path, '/api/v1/media/media%2Fid/complete');
  assert.equal(calls[3].body, body); assert.equal(calls[3].headers['Idempotency-Key'], 'stable-key');
});

test('private media requires binary data and rejects reads from an expired or replaced session', async () => {
  const bytes = new Uint8Array([255, 216, 255]).buffer;
  const { api, calls } = harness([{ status: 200, body: bytes }, { status: 200, body: 'invalid' }]);
  assert.equal(await api.mediaContent('media/id'), bytes);
  assert.equal(calls[0].responseType, 'arraybuffer');
  assert.equal(calls[0].path, '/api/v1/media/media%2Fid/content');
  await assert.rejects(api.mediaContent('media'), { status: 502 });
  const pending = deferred();
  const { api: stale } = harness([() => pending.promise]);
  const read = stale.mediaContent('media'); stale.clear(); pending.resolve({ status: 200, body: bytes });
  await assert.rejects(read, { code: 'stale' });
  let expired = 0;
  const unauthorized = new FamilyApi(async () => ({ status: 401, body: bytes }), () => expired++);
  await assert.rejects(unauthorized.mediaContent('media'), { status: 401 }); assert.equal(expired, 1);
});

test('passkey login adopts child identity and rejects a guardian response', async () => {
  const credential = { id: 'credential', response: { signature: 'signed' } };
  const { api, calls } = harness([ok({ token_type: 'cookie' }), ok(child)]);
  assert.deepEqual(await api.loginWithPasskey(credential), child);
  assert.equal(calls[0].path, '/api/v1/auth/passkeys/authentication/verify');
  assert.deepEqual(calls[0].body, { credential });
  const invalid = harness([ok({ token_type: 'cookie' }), ok(guardian)]);
  await assert.rejects(invalid.api.loginWithPasskey(credential), { status: 403 });
});

test('passkey management keeps credential identifiers in their own path segment', async () => {
  const { api, calls } = harness([ok({ items: [] }), ok({ revoked: true })]);
  await api.passkeys(); await api.revokePasskey('credential/other');
  assert.equal(calls[1].path, '/api/v1/auth/passkeys/credential%2Fother');
  assert.equal(calls[1].method, 'DELETE');
});
