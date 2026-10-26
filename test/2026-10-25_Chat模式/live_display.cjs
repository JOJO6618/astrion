const assert = require('node:assert/strict');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const vue = require('../../node_modules/vue');
const { compile } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const { renderMessages } = require('./chat_area_fixture.cjs');
const phases = JSON.parse(execFileSync('python3', ['-B', path.join(__dirname, 'projection_fixture.py')], { encoding: 'utf8' }));
const types = compile('static/src/chat/types.ts');
const { toDisplayMessages } = compile('static/src/chat/display.ts', { './types': types });
const visibility = compile('static/src/utils/messageVisibility.ts');
const { resolveAvatarStatus } = compile('static/src/utils/avatarStatus.ts', {
  '@/locales': { t: key => key, currentLocale: vue.ref('zh-CN') },
  './avatarFace': { toolFaceKey: name => name }, './messageVisibility': visibility
});
const user = { message_id: 'user', role: 'user', content: 'Hello from Chat', metadata: { starts_work: true, message_source: 'user', chat: { parent_id: null } } };
const first = { message_id: 'response', role: 'assistant', content: 'Visible reply', reasoning_content: 'Thinking received',
  tool_calls: [{ id: 'call', function: { name: 'web_search', arguments: '{}' } }], metadata: { chat: { parent_id: 'user' } } };
const tool = { message_id: 'result', role: 'tool', content: '{"success":true}', tool_call_id: 'call', metadata: { chat: { parent_id: 'response' } } };
const final = { message_id: 'response_final', role: 'assistant', content: 'Final answer', metadata: { chat: { parent_id: 'result' } } };
function view(phase) {
  const stored = ['next_request', 'finished'].includes(phase) ? [user, first, tool] : [user];
  if (phase === 'finished') stored.push(final);
  return { document: { id: 'conv_fixture', title: 'Chat', messages: stored,
    metadata: { network_enabled: true, chat: { latest_leaf_id: stored[stored.length - 1].message_id, turns: [], revision: 1 } } },
    display: { ...phases[phase], task: { task_id: 'task_fixture', status: phase === 'finished' ? 'succeeded' : 'running' }, next_event_idx: 10 } };
}
let current = view('waiting');
const timers = new Map();
let serial = 0;
const window = { setTimeout(fn) { timers.set(++serial, fn); return serial; }, clearTimeout(id) { timers.delete(id); },
  history: { replaceState() {} } };
const api = { operationId: () => 'operation', loadView: async () => current,
  command: async () => ({ conversation_id: 'conv_fixture', task_id: 'task_fixture' }),
  chatApi: async route => route.startsWith('/tasks/')
    ? { status: current.display.task.status, view: current } : [] };
const { useChat } = compile('static/src/chat/useChat.ts', {
  vue: { ...vue, onBeforeUnmount() {} }, './types': types, './api': api
}, { window });
const chat = useChat();
async function advance(phase) {
  current = view(phase);
  const pending = [...timers];
  assert.equal(pending.length, 1, 'One shared task polling timer is active');
  timers.delete(pending[0][0]); pending[0][1]();
  for (let i = 0; i < 12; i++) await Promise.resolve();
}
function display() { return toDisplayMessages(chat.messages.value, chat.display.value.messages); }
function avatar() {
  return resolveAvatarStatus({ messages: display(), taskInProgress: chat.running.value,
    streamingMessage: chat.display.value.state.streaming,
    apiRequestPending: chat.display.value.state.api_request_pending, toolIntentEnabled: true });
}
(async () => {
  await chat.send(user.content, []);
  let messages = display();
  assert.equal(messages.length, 2, 'Accepted send immediately keeps the server reply placeholder');
  assert.equal(messages[1].awaitingFirstContent, true);
  let html = await renderMessages(messages);
  assert.ok(html.includes('Hello from Chat'));
  assert.ok(html.includes('Astrion'), 'Real shared template displays the original reply header');
  assert.ok(html.includes('assistant-generating-placeholder'));
  assert.equal(avatar().text, 'appCore.waitingApiResponse');
  assert.equal(avatar().mode, 'work', 'Thinking preference does not imply actual thinking');
  await advance('thinking');
  assert.equal(avatar().text, 'appCore.thinking');
  assert.equal(avatar().mode, 'think');
  html = await renderMessages(display());
  assert.ok(html.includes('Thinking received'));
  await advance('text');
  html = await renderMessages(display());
  assert.ok(html.includes('Visible reply'), 'Text chunks reach the real shared MarkdownRenderer slot');
  assert.equal(avatar().text, '');
  assert.equal(avatar().mode, 'work');
  await advance('preparing');
  assert.ok(display().some(m => m.actions.some(a => a.type === 'tool')));
  assert.equal(avatar().mode, 'tool'); assert.equal(avatar().text, '');
  await advance('tool');
  assert.equal(avatar().mode, 'tool'); assert.equal(avatar().text, 'Search intent');
  await advance('next_request');
  assert.equal(avatar().text, 'appCore.waitingApiResponse');
  assert.equal(display().flatMap(m => m.actions).filter(a => a.type === 'tool').length, 1);
  await advance('finished');
  messages = display();
  assert.equal(chat.running.value, false);
  assert.equal(messages.length, 3, 'Completion replaces runtime view without duplicate replies');
  html = await renderMessages(messages);
  assert.ok(html.includes('Final answer'));
  assert.equal(avatar().mode, 'idle'); assert.equal(avatar().text, '');
  assert.equal(timers.size, 0, 'Terminal snapshot retires polling');
  chat.document.value.messages.push({ ...user, message_id: 'old-user', content: 'Old branch' });
  chat.browse('old-user');
  assert.equal(toDisplayMessages(chat.messages.value, []).length, 1, 'Old branch browsing stays isolated');
  console.log('Live display passed: actual backend projection → accepted send/polling → real ChatArea template; waiting header, thinking, text, tools, final snapshot, original avatar states.');
})().catch(error => { console.error(error); process.exitCode = 1; });
