const assert = require('node:assert/strict');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const vue = require('../../node_modules/vue');
const { compile, deferred } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const { renderMessages } = require('../2026-10-25_Chat模式/chat_area_fixture.cjs');
const phases = JSON.parse(execFileSync('/opt/homebrew/bin/python3.11', ['-B',
  path.join(__dirname, '../2026-10-25_Chat模式/projection_fixture.py')], { encoding: 'utf8' }));
const types = compile('static/src/chat/types.ts');
const { toDisplayMessages } = compile('static/src/chat/display.ts', { './types': types });
const cases = [];
const test = (name, fn) => cases.push([name, fn]);
const tick = async () => { for (let i = 0; i < 16; i++) await Promise.resolve(); };
const user = { message_id: 'user', role: 'user', content: '不切换到 Agent，继续在 Chat 中处理当前任务',
  metadata: { starts_work: true, chat: { parent_id: null } } };
function view(id = 'conv_fixture', phase = 'idle', status) {
  const messages = [structuredClone(user)];
  if (phase === 'finished') messages.push({ message_id: 'response', role: 'assistant', content: 'Visible reply',
    metadata: { chat: { parent_id: 'user' } } });
  const active = phase !== 'idle';
  return { document: { id, title: id, messages, metadata: { network_enabled: true,
    chat: { latest_leaf_id: messages.at(-1).message_id, active_turn_id: active ? 'turn' : null,
      turns: [], revision: 1 } } },
    display: { ...(phases[phase] || { messages: [], state: {} }),
      task: active ? { task_id: 'task_fixture', status: status || (phase === 'finished' ? 'succeeded' : 'running') } : null,
      next_event_idx: phase === 'text' ? 29 : phase === 'finished' ? 35 : 17 } };
}
function fixture() {
  const requests = [], timers = new Map(), errors = [];
  let serial = 0;
  const window = { setTimeout(fn) { timers.set(++serial, fn); return serial; },
    clearTimeout(id) { timers.delete(id); }, addEventListener() {}, removeEventListener() {},
    history: { replaceState() {} } };
  const request = (route, body) => {
    const pending = deferred(); requests.push({ route, body, ...pending }); return pending.promise;
  };
  const api = { operationId: () => `op-${++serial}`, command: request, chatApi: request,
    loadView: id => request(`/load/${id}`), releasePresentation: async () => {} };
  const { useChat } = compile('static/src/chat/useChat.ts', {
    vue: { ...vue, onBeforeUnmount() {} }, './api': api, './types': types
  }, { window });
  const chat = useChat(data => errors.push(data));
  const display = () => toDisplayMessages(chat.messages.value, chat.display.value?.messages || []);
  async function load(snapshot) {
    const pending = chat.load(snapshot.document.id);
    requests.at(-1).resolve(snapshot); await pending;
  }
  async function poll(snapshot) {
    assert.equal(timers.size, 1, 'The resumed task must own exactly one polling timer');
    const [id, run] = [...timers][0]; timers.delete(id); run();
    const request = requests.at(-1);
    request.resolve({ status: snapshot.display.task.status, view: snapshot, events: [] });
    await tick(); return request;
  }
  return { chat, timers, requests, errors, display, load, poll };
}

test('decline snapshot resumes polling and renders AI header and live text without another reload', async () => {
  const f = fixture();
  await f.load(view());
  assert.equal(f.timers.size, 0);
  // The handoff changed event calls reload after the server accepted its user
  // continuation. At this instant the worker may not have emitted a header.
  const continuing = f.chat.reload();
  f.requests.at(-1).resolve(view('conv_fixture', 'accepted', 'pending'));
  await continuing;
  assert.equal(f.chat.running.value, true);
  assert.equal(f.timers.size, 1, 'Declining Agent must subscribe to the new Chat task');
  const waitRequest = await f.poll(view('conv_fixture', 'waiting'));
  assert.equal(waitRequest.route, '/tasks/task_fixture?from=17');
  let html = await renderMessages(f.display());
  assert.ok(html.includes('Astrion'), 'The real shared ChatArea renders the AI reply header');
  assert.ok(html.includes('assistant-generating-placeholder'));
  await f.poll(view('conv_fixture', 'text'));
  html = await renderMessages(f.display());
  assert.ok(html.includes('Visible reply'), 'Text becomes visible while the task is still running');
  assert.equal(f.chat.running.value, true);
  const finalRequest = await f.poll(view('conv_fixture', 'finished'));
  assert.equal(finalRequest.route, '/tasks/task_fixture?from=29');
  assert.equal(f.chat.running.value, false);
  assert.equal(f.timers.size, 0);
  assert.equal(f.requests.filter(r => r.route.startsWith('/load/')).length, 2,
    'One initial load and one decline refresh; no manual refresh is required');
});

test('running refresh replaces its existing timer rather than creating duplicate subscriptions', async () => {
  const f = fixture(); await f.load(view('conv_fixture', 'waiting'));
  const oldTimer = [...f.timers.keys()][0];
  const refresh = f.chat.reload(); f.requests.at(-1).resolve(view('conv_fixture', 'text')); await refresh;
  assert.equal(f.timers.size, 1);
  assert.ok(!f.timers.has(oldTimer));
  assert.ok((await renderMessages(f.display())).includes('Visible reply'));
});

test('a completed continuation refresh does not start a new polling loop', async () => {
  const f = fixture(); await f.load(view());
  const refresh = f.chat.reload(); f.requests.at(-1).resolve(view('conv_fixture', 'finished')); await refresh;
  assert.equal(f.timers.size, 0);
  assert.ok((await renderMessages(f.display())).includes('Visible reply'));
});

test('a late initial load cannot cancel another conversation polling timer', async () => {
  const f = fixture();
  const old = f.chat.load('conv_old'); const oldRequest = f.requests.at(-1);
  await f.load(view('conv_new', 'waiting'));
  const timer = [...f.timers.keys()][0];
  oldRequest.resolve(view('conv_old')); await old;
  assert.equal(f.chat.cid.value, 'conv_new');
  assert.ok(f.timers.has(timer), 'A retired navigation must not clear the active task timer');
});

test('a late decline refresh cannot replace or stop a different conversation task', async () => {
  const f = fixture(); await f.load(view());
  const old = f.chat.reload(); const oldRequest = f.requests.at(-1);
  await f.load(view('conv_new', 'waiting'));
  const timer = [...f.timers.keys()][0];
  oldRequest.resolve(view('conv_fixture', 'accepted', 'pending')); await old;
  assert.equal(f.chat.cid.value, 'conv_new'); assert.ok(f.timers.has(timer));
});

(async () => {
  let failures = 0;
  for (const [name, fn] of cases) {
    try { await fn(); console.log(`PASS ${name}`); }
    catch (error) { failures++; console.error(`FAIL ${name}\n${error.stack}`); }
  }
  if (failures) process.exitCode = 1;
  else console.log(`${cases.length} Chat handoff continuation display checks passed`);
})().catch(error => { console.error(error); process.exitCode = 1; });
