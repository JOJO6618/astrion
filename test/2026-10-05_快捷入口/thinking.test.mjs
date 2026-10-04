import { test } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
const built = await build({ entryPoints: ['static/src/quick/runtime.ts'], bundle: true, write: false, format: 'esm', platform: 'node',
  plugins: [{ name: 'locales', setup(builder) {
    builder.onResolve({ filter: /^@\/locales$/ }, () => ({ path: 'locales', namespace: 'mock' }));
    builder.onLoad({ filter: /.*/, namespace: 'mock' }, () => ({ contents: 'export const t = key => key;' }));
  } }] });
const { useQuickRuntime } = await import(`data:text/javascript;base64,${Buffer.from(built.outputFiles[0].text).toString('base64')}`);
test('thinking is a status only and clears on task completion', async context => {
  let finish = false;
  globalThis.window = { astrionQuick: { request: async (route, method) => {
    if (route === '/api/tasks' && method === 'POST') return { data: { task_id: 't1' } };
    if (route === '/api/tasks/t1?from=0') return { data: { status: 'running', next_offset: 2, events: [
      { type: 'thinking_start', data: {} }, { type: 'thinking_chunk', data: { content: 'Private reasoning content' } }
    ] } };
    if (route === '/api/tasks/t1?from=2') { finish = true; return { data: { status: 'succeeded', next_offset: 3, events: [] } }; }
    return { items: [], conversations: [] };
  } } };
  const state = useQuickRuntime(); context.after(() => state.dispose());
  state.workspace.value = 'w1'; state.model.value = 'm1'; state.conversation.value = 'c1'; state.draft.value = 'Task';
  await state.send(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(state.thinking.value, true);
  assert.equal(state.reply.value, '');
  assert.deepEqual(state.tools.value, []);
  await new Promise(resolve => setTimeout(resolve, 450));
  assert.equal(finish, true);
  assert.equal(state.thinking.value, false);
});
