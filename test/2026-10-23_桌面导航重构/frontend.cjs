const assert = require('node:assert/strict');
const { fixture, answer, commandRequest, tab, flush, noop } = require('../2026-11-14_桌面多视图/frontend/fixture.cjs');
const tests = [];
const test = (name, run) => tests.push([name, run]);
test('older revisions and same-revision old responses cannot replace current tabs', () => {
  const f = fixture();
  f.store.applyServerSnapshot(f.state(8, [tab('B', 'B')], 'B'), 12);
  f.store.applyServerSnapshot(f.state(7, [tab('A', 'A')], 'A'), 20);
  f.store.applyServerSnapshot(f.state(8, [tab('B', 'stale-title')], 'B'), 11);
  assert.equal(f.store.activeKey, 'B'); assert.equal(f.store.tabs[0].conversationId, 'B');
});

test('hydrate failure is retryable and never enables an empty-state overwrite', async () => {
  const f = fixture();
  const failed = f.store.hydrate(); failed.catch(noop);
  await flush(); answer(f.requests[0], { success: false, error: 'not ready' }, 503);
  await assert.rejects(failed); assert.equal(f.store.hydrated, false);
  const retry = f.store.hydrate(); await flush();
  answer(f.requests[1], { success: true, ...f.state(1, [tab('A')], 'A') });
  await retry; assert.equal(f.store.hydrated, true); assert.equal(f.store.tabs.length, 1);
});

test('new tab uses one command identity and only the returned server key', async () => {
  const f = fixture();
  const created = f.store.addNewTab({ workspaceId: 'ws1' });
  const request = await commandRequest(f, 'create');
  const payload = JSON.parse(request.options.body);
  assert.ok(payload.payload.key.startsWith('tab_'));
  assert.equal(f.store.tabs.length, 0);
  answer(request, { success: true, result: { key: payload.payload.key },
    state: f.state(1, [tab(payload.payload.key)]) });
  await created; assert.equal(f.store.tabs[0].key, payload.payload.key);
});

test('network uncertainty retries the identical command instead of creating twice', async () => {
  const f = fixture(); const transport = new f.transport.NavigationTransport();
  const pending = transport.command({ command_id: 'same', action: 'create', payload: { key: 'tab_A' } });
  await flush(); const first = f.requests[0]; first.reject(new TypeError('connection lost'));
  await flush(); assert.equal(f.requests[1].options.body, first.options.body);
  answer(f.requests[1], { success: true, state: {} }); await pending;
});

test('business rejection is returned and is not silently retried', async () => {
  const f = fixture(); const transport = new f.transport.NavigationTransport();
  const pending = transport.command({ command_id: 'same', action: 'activate', payload: { key: 'closed' } });
  await flush(); answer(f.requests[0], { success: false, error: 'closed' }, 409);
  await assert.rejects(pending, (error) => error.status === 409);
  assert.equal(f.requests.length, 1);
});

test('polls do not overlap and stopping invalidates late responses and timers', async () => {
  const f = fixture(); const transport = new f.transport.NavigationTransport();
  const applied = [];
  transport.start((value) => applied.push(value), noop);
  assert.equal(f.requests.length, 1);
  // Only the request deadline exists; no interval can spawn a second request.
  assert.equal(f.timers.size, 1);
  transport.stop(); answer(f.requests[0], { success: true }); await flush();
  assert.equal(applied.length, 0); assert.equal(f.timers.size, 0);
});

test('command transport serializes writes without waiting for content loading', async () => {
  const f = fixture(); const transport = new f.transport.NavigationTransport();
  const first = transport.command({ command_id: 'one', action: 'create', payload: {} });
  const second = transport.command({ command_id: 'two', action: 'close', payload: {} });
  await flush(); assert.equal(f.requests.length, 1);
  answer(f.requests[0], { success: true }); await first; await flush();
  assert.equal(f.requests.length, 2);
  answer(f.requests[1], { success: true }); await second;
});

test('binding uses the captured source key even after another tab becomes active', async () => {
  const f = fixture();
  f.store.applyServerSnapshot(f.state(1, [tab('source'), tab('other')], 'other'));
  const pending = f.store.bindConversation('source', 'ws1', 'created', 'new');
  const request = await commandRequest(f, 'bind');
  assert.equal(JSON.parse(request.options.body).payload.key, 'source');
  answer(request, { success: true, result: { bound: true },
    state: f.state(2, [tab('source', 'created'), tab('other')], 'other') });
  await pending; assert.equal(f.store.activeKey, 'other'); assert.equal(f.store.tabs[0].key, 'source');
});


(async () => {
  for (const [name, run] of tests) { await run(); console.log('PASS ' + name); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
