import { test } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';

const bundled = await build({ entryPoints: ['static/src/quick/runtime.ts'], bundle: true, write: false, format: 'esm', platform: 'node',
  plugins: [{ name: 'locales', setup(builder) {
    builder.onResolve({ filter: /^@\/locales$/ }, () => ({ path: 'locales', namespace: 'mock' }));
    builder.onLoad({ filter: /.*/, namespace: 'mock' }, () => ({ contents: 'export const t = key => key;' }));
  } }] });
const { useQuickRuntime } = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString('base64')}`);
const flush = () => new Promise(resolve => setImmediate(resolve));
function fixture(context, result) {
  const storage = new Map();
  globalThis.localStorage = { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value) };
  globalThis.window = { astrionQuick: { request: async (route, method) => {
    if (route === '/api/tasks' && method === 'POST') return { data: { task_id: 't1' } };
    if (route === '/api/tasks/t1?from=0') return { data: result };
    return { items: [], conversations: [] };
  } } };
  const state = useQuickRuntime();
  state.workspace.value = 'w1'; state.model.value = 'vision'; state.conversation.value = 'c1'; state.draft.value = 'Task';
  context.after(() => { state.dispose(); delete globalThis.window; delete globalThis.localStorage; });
  return state;
}

test('cancelled tasks do not turn their terminal reason into frontend output', async context => {
  const state = fixture(context, { status: 'cancelled', error: '任务已停止', next_offset: 1,
    events: [{ type: 'task_stopped', data: {} }] });
  await state.send(); await flush();
  assert.equal(state.error.value, '');
  assert.equal(state.reply.value, '');
  assert.equal(state.notice, undefined);
  assert.equal(state.running.value, false);
});
test('API failure retains partial model output and exposes one terminal error', async context => {
  const message = 'API 429: Rate limit exceeded';
  const state = fixture(context, { status: 'failed', error: message, next_offset: 3, events: [
    { type: 'text_chunk', data: { content: 'Partial output' } },
    { type: 'error', data: { message } }
  ] });
  await state.send(); await flush();
  assert.equal(state.reply.value, 'Partial output');
  assert.equal(state.error.value, message);
  assert.equal(state.streaming.value, false);
  assert.equal(state.collapsed.value, false);
});
test('terminal API errors are displayed even without an error event', async context => {
  const state = fixture(context, { status: 'error', error: 'API 500: unavailable', next_offset: 0, events: [] });
  state.collapsed.value = true;
  await state.send(); await flush();
  assert.equal(state.error.value, 'API 500: unavailable');
  assert.equal(state.collapsed.value, false);
});
test('successful guidance submission creates no frontend status notice', async context => {
  const state = fixture(context, {});
  state.task.value = 't1'; state.running.value = true;
  await state.send();
  assert.equal(state.draft.value, '');
  assert.equal(state.notice, undefined);
  assert.equal(state.error.value, '');
});
