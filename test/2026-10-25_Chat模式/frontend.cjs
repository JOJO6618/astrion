const assert = require('node:assert/strict');
const vue = require('../../node_modules/vue');
const { compile, deferred } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const cases = [];
const test = (name, fn) => cases.push([name, fn]);
const tick = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
function fixture() {
  const requests = [], cleanup = [], timers = new Map(), local = new Map(), urls = [];
  let serial = 0;
  const window = { setTimeout(fn) { timers.set(++serial, fn); return serial; },
    clearTimeout(id) { timers.delete(id); }, addEventListener() {}, removeEventListener() {},
    history: { replaceState(_, __, url) { urls.push(url); } } };
  const request = (path, body) => { const value = deferred(); requests.push({ path, body, ...value }); return value.promise; };
  class ChatApiError extends Error { constructor(message, status) { super(message); this.status = status; } }
  const api = { chatApi: request, command: request, loadView: id => request(`/load/${id}`),
    operationId: () => `operation-${++serial}`, ChatApiError };
  const globals = { window, localStorage: { getItem: key => local.get(key), setItem: (key, value) => local.set(key, value) } };
  const hooks = { ...vue, onBeforeUnmount: fn => cleanup.push(fn) };
  const types = compile('static/src/chat/types.ts', {}, globals);
  const module = compile('static/src/chat/useChat.ts', { vue: hooks, './api': api, './types': types }, globals);
  const draftModule = compile('static/src/chat/useChatDraft.ts', { vue: hooks, './api': api }, globals);
  return { requests, timers, local, urls, cleanup, chat: module.useChat(),
    draft: () => draftModule.useChatDraft(() => {}), ChatApiError };
}
function view(id, content = id) {
  return { document: { id, title: id, messages: [{ message_id: `${id}-user`, role: 'user', content,
    metadata: { chat: { parent_id: null, turn_id: null } } }],
    metadata: { network_enabled: true, chat: { latest_leaf_id: `${id}-user`, turns: [], revision: 0 } } },
    display: { messages: [], task: null, state: {}, next_event_idx: 0 } };
}

test('late Chat load cannot replace a newer conversation', async () => {
  const f = fixture();
  const first = f.chat.load('A'), second = f.chat.load('B');
  f.requests[1].resolve(view('B')); await second;
  f.requests[0].resolve(view('A')); await first;
  assert.equal(f.chat.cid.value, 'B');
});
test('desktop ownership is checked before committing a response', async () => {
  const f = fixture(); let owner = true;
  const loading = f.chat.load('A', () => owner);
  owner = false; f.requests[0].resolve(view('A')); await loading;
  assert.equal(f.chat.document.value, null);
});
test('slow search cannot overwrite a newer query', async () => {
  const f = fixture(); const old = f.chat.list('old'), latest = f.chat.list('new');
  f.requests[1].resolve([{ id: 'B' }]); await latest;
  f.requests[0].resolve([{ id: 'A' }]); await old;
  assert.equal(f.chat.history.value[0].id, 'B');
});
test('accepted background send cannot take over a different page', async () => {
  const f = fixture(); const sending = f.chat.send('hello', []);
  await f.chat.load('');
  f.requests[0].resolve({ conversation_id: 'conv_A' });
  const accepted = await sending;
  assert.equal(accepted.ownsPage, false);
  assert.equal(f.chat.cid.value, ''); assert.deepEqual(f.urls, []);
});
test('a late network setting response cannot change another Chat', async () => {
  const f = fixture(); const initial = f.chat.load('A'); f.requests[0].resolve(view('A')); await initial;
  const switching = f.chat.toggleNetwork(); const loading = f.chat.load('B');
  f.requests[2].resolve(view('B')); await loading; f.requests[1].resolve({}); await switching;
  assert.equal(f.chat.network.value, true); assert.equal(f.chat.cid.value, 'B');
});
test('refresh uses latest branch rather than temporary browsing', async () => {
  const f = fixture(); const current = view('A');
  current.document.messages.push({ message_id: 'other', role: 'assistant', content: 'old', metadata: { chat: { parent_id: 'A-user' } } });
  const loading = f.chat.load('A'); f.requests[0].resolve(current); await loading;
  f.chat.browse('other'); assert.equal(f.chat.viewingLeaf.value, 'other');
  const refresh = f.chat.load('A'); f.requests[1].resolve(current); await refresh;
  assert.equal(f.chat.viewingLeaf.value, null); assert.equal(f.chat.path.value.length, 1);
});
test('stable response identity joins live text and thinking without duplication', async () => {
  const f = fixture(); const current = view('A');
  current.display.task = { task_id: 'task', status: 'running' };
  current.display.messages = [{ actions: [
    { type: 'thinking', responseMessageId: 'response', content: 'thought' },
    { type: 'text', responseMessageId: 'response', content: 'answer' }] }];
  const loading = f.chat.load('A'); f.requests[0].resolve(current); await loading;
  assert.equal(f.chat.messages.value.length, 2);
  assert.equal(f.chat.messages.value[1].content, 'answer');
  assert.equal(f.chat.messages.value[1].reasoning_content, 'thought');
  const final = structuredClone(current);
  final.document.messages.push({ message_id: 'response', role: 'assistant', content: 'answer', metadata: { chat: { parent_id: 'A-user' } } });
  final.document.metadata.chat.latest_leaf_id = 'response'; final.display.task.status = 'succeeded';
  const refresh = f.chat.reload(); f.requests[1].resolve(final); await refresh;
  assert.equal(f.chat.messages.value.length, 2);
});
test('old draft completion cannot unlock a newer editor', async () => {
  const f = fixture(); const draft = f.draft();
  assert.equal(draft.ready.value, false);
  const first = draft.open('user', 'A'), second = draft.open('user', 'B');
  f.requests.find(r => r.path === '/drafts/A').resolve({ text: 'A', revision: 0 });
  await first;
  assert.equal(draft.ready.value, false);
  f.requests.find(r => r.path === '/drafts/B').resolve({ text: 'B', revision: 0 });
  await second;
  assert.equal(draft.ready.value, true);
  assert.equal(draft.text.value, 'B');
});
test('draft read failure unlocks local work after initialization', async () => {
  const f = fixture(); const draft = f.draft();
  f.local.set('astrion:chat:draft:user:A', JSON.stringify({ text: 'local work', revision: 2 }));
  const opening = draft.open('user', 'A');
  assert.equal(draft.ready.value, false);
  f.requests[0].reject(Error('offline')); await opening;
  assert.equal(draft.ready.value, true);
  assert.equal(draft.text.value, 'local work');
});
test('late draft reads cannot cross conversation identities', async () => {
  const f = fixture(); const draft = f.draft();
  const first = draft.open('user', 'A'), second = draft.open('user', 'B');
  // Switching saves A only after its initial read; restoring an empty draft does not write.
  const getB = f.requests.find(r => r.path === '/drafts/B');
  getB.resolve({ text: 'B draft', revision: 0 }); await second;
  f.requests.find(r => r.path === '/drafts/A').resolve({ text: 'A draft', revision: 0 }); await first;
  assert.equal(draft.text.value, 'B draft');
  assert.equal(draft.identity.value, 2);
});
test('per-conversation draft writers keep their own revision after navigation', async () => {
  const f = fixture(); const draft = f.draft();
  const opening = draft.open('user', 'A'); f.requests[0].resolve({ revision: 2 }); await opening;
  draft.text.value = 'saved A'; const saving = draft.save();
  const writer = f.requests.find(r => r.body?.text === 'saved A');
  const next = draft.open('user', 'B');
  f.requests.find(r => r.path === '/drafts/B').resolve({ revision: 8, text: 'B' }); await next;
  writer.resolve({ revision: 3 }); await saving; await tick();
  assert.equal(draft.text.value, 'B');
  const saved = JSON.parse(f.local.get('astrion:chat:draft:user:A'));
  assert.equal(saved.revision, 3); assert.equal(saved.text, 'saved A');
});
(async () => {
  for (const [name, fn] of cases) {
    await fn(); console.log('PASS', name);
  }
  console.log(`${cases.length} Chat frontend state checks passed`);
})().catch(error => { console.error(error); process.exitCode = 1; });
