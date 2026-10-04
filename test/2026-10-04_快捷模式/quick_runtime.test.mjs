import { test } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
const built = await build({ entryPoints: ['static/src/quick/runtime.ts'], bundle: true, write: false, format: 'esm', platform: 'node',
  plugins: [{ name: 'test-locales', setup(builder) {
    builder.onResolve({ filter: /^@\/locales$/ }, () => ({ path: 'locales', namespace: 'test' }));
    builder.onLoad({ filter: /.*/, namespace: 'test' }, () => ({ contents: 'export const t = key => key;' }));
  } }] });
const { useQuickRuntime } = await import(`data:text/javascript;base64,${Buffer.from(built.outputFiles[0].text).toString('base64')}`);
const wait = () => new Promise(resolve => setImmediate(resolve));
function setup(handler) {
  const calls = [], saved = new Map();
  globalThis.localStorage = { getItem: key => saved.get(key) || null, setItem: (key, value) => saved.set(key, value) };
  globalThis.window = { astrionQuick: { request: async (route, method = 'GET', body, workspace) => {
    calls.push({ route, method, body, workspace });
    if (route === '/api/runtime/sessions' && method === 'POST') return { conversation_id: 'c1' };
    if (route === '/api/tasks' && method === 'POST') return { data: { task_id: 't1' } };
    if (route.includes('/pending')) return { items: [] };
    if (route.startsWith('/api/runtime/sessions?')) return { conversations: [{ id: 'c1', title: 'test', quick_entry: true }] };
    return await handler(route, method, body);
  }, configure: async patch => patch } };
  const state = useQuickRuntime(); state.workspace.value = 'w1'; state.model.value = 'vision1';
  return { state, calls };
}

test('new messages explicitly create execute/unrestricted/sandbox sessions and store screenshots as data_url media', async () => {
  const { state, calls } = setup(async () => ({ data: { status: 'succeeded', events: [{ type: 'text_start', data: {} }, { type: 'text_chunk', data: { content: 'Actual reply' } }], next_offset: 2 } }));
  state.draft.value = 'Look'; state.images.value = ['data:image/png;base64,AA=='];
  await state.send(); await wait();
  const create = calls.find(call => call.route === '/api/runtime/sessions');
  assert.deepEqual(create.body, { work_mode: 'execute', permission_mode: 'unrestricted', execution_mode: 'sandbox', model_key: 'vision1', quick_entry: true });
  const send = calls.find(call => call.route === '/api/tasks');
  assert.deepEqual(send.body.images, [{ data_url: 'data:image/png;base64,AA==', kind: 'image' }]);
  assert.equal(state.reply.value, 'Actual reply');
  assert.equal(state.running.value, false);
  assert.equal(state.streaming.value, false);
  assert.equal(state.sessions.value[0].conversation_id, 'c1');
  state.dispose();
});

test('sending during an active task is guidance, never an early-input task', async () => {
  const { state, calls } = setup(async () => ({ success: true }));
  state.running.value = true; state.task.value = 't1'; state.draft.value = 'Follow this';
  state.images.value = ['data:image/png;base64,AA=='];
  await state.send();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].route, '/api/tasks/t1/runtime_guidance');
  assert.deepEqual(calls[0].body.images, [{ data_url: 'data:image/png;base64,AA==', kind: 'image' }]);
  assert.equal(state.running.value, true);
  assert.equal(state.draft.value, '');
  state.dispose();
});

test('approval decisions and questions use the actual backend payload', async () => {
  const { state, calls } = setup(async () => ({ success: true }));
  state.conversation.value = 'c1';
  await state.answer({ kind: 'tool-approvals', approval_id: 'a1' }, true);
  await state.answer({ kind: 'user-questions', question_id: 'q1' }, true, 'My answer');
  assert.deepEqual(calls.find(call => call.route.endsWith('/decision')).body, { decision: 'approved' });
  assert.deepEqual(calls.find(call => call.route.endsWith('/answer')).body, { text: 'My answer' });
  state.dispose();
});

 test('reconcile discovers a task started while the window was hidden', async (context) => {
  const { state } = setup(async route => route.includes('running-status')
    ? { data: { main_task_id: 't2' } }
    : { data: { status: 'succeeded', events: [{ type: 'text_chunk', data: { content: 'Resumed reply' } }], next_offset: 1 } });
  context.after(() => state.dispose());
  state.conversation.value = 'c1';
  await state.reconcile(); await wait();
  assert.equal(state.reply.value, 'Resumed reply');
  assert.equal(state.running.value, false);
});

test('an event gap restores the full final reply from conversation history', async (context) => {
  const { state } = setup(async route => route.endsWith('/history')
    ? { conversation: { metadata: { quick_entry: true }, messages: [{ role: 'assistant', content: 'Complete final reply' }] } }
    : route.includes('running-status') ? { data: { main_task_id: '' } }
    : { data: { status: 'succeeded', window_start: 8, next_offset: 9, events: [{ type: 'text_chunk', data: { content: 'reply tail' } }] } });
  context.after(() => state.dispose());
  state.draft.value = 'Start';
  await state.send(); await wait();
  assert.equal(state.reply.value, 'Complete final reply');
  assert.equal(state.streaming.value, false);
});
