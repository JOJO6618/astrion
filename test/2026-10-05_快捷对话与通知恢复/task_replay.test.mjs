import { test } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';

const mocks = {
  common: 'export const debugLog = () => {}; export const goalModeDebugLog = () => {};',
  shared: 'export const debugNotifyLog = () => {}; export const keyNotifyLog = () => {}; export const jsonDebug = () => {}; export const restoreDebugLog = () => {};',
  task: 'export const useTaskStore = () => globalThis.__replayTask;',
  quickDock: 'export const useQuickDockStore = () => ({ setEditedFiles: (items, live) => globalThis.__replayUpdates.push({ items, live }) });',
  preview: 'export const usePreviewStore = () => ({ setTargets: (items, live) => globalThis.__replayUpdates.push({ items, live }) });',
  chat: 'export const useChatStore = () => ({});',
  locales: 'export const t = key => key;'
};
const built = await build({
  stdin: { contents: `
    export { createPinia, setActivePinia } from 'pinia';
    export { useUiStore } from './static/src/stores/ui.ts';
    export { lifecycleMethods } from './static/src/app/methods/taskPolling/lifecycle.ts';
    export * from './static/src/utils/taskReplay.ts';
  `, resolveDir: process.cwd() },
  bundle: true, write: false, format: 'esm', platform: 'node',
  plugins: [{ name: 'replay-dependencies', setup(builder) {
    builder.onResolve({ filter: /^@\/locales$|^\.\.\/common$|^\.\/shared$|^\.\.\/\.\.\/\.\.\/stores\// }, args => {
      const key = args.path === '@/locales' ? 'locales' : args.path.split('/').at(-1);
      return { path: key, namespace: 'mock' };
    });
    builder.onLoad({ filter: /.*/, namespace: 'mock' }, args => ({ contents: mocks[args.path] }));
  } }]
});
const api = await import(`data:text/javascript;base64,${Buffer.from(built.outputFiles[0].text).toString('base64')}`);

function setup() {
  api.setActivePinia(api.createPinia());
  globalThis.__replayTask = { currentTaskId: 't1' };
  globalThis.__replayUpdates = [];
  const ui = api.useUiStore();
  const context = {
    ...api.lifecycleMethods,
    currentConversationId: 'c1',
    _taskReplayBoundary: { taskId: 't1', conversationId: 'c1', lastEventIndex: 2000 },
    handleTaskError() { ui.pushToast({ message: 'tool failure', duration: null }); },
    fileSetTodoList(items, live) { globalThis.__replayUpdates.push({ items, live }); }
  };
  return { context, ui };
}
const event = (idx, type, extra = {}) => ({ idx, type, data: { task_id: 't1', conversation_id: 'c1', ...extra } });

test('historical errors do not notify; a live error in the same batch does', () => {
  const { context, ui } = setup();
  context.handleTaskEvent(event(0, 'error'));
  context.handleTaskEvent(event(2000, 'error'));
  assert.equal(ui.toastQueue.length, 0);
  context.handleTaskEvent(event(2001, 'error'));
  assert.equal(ui.toastQueue.length, 1);
  assert.equal(context._rebuildingFromScratch, false);
});

test('slow historical replay still suppresses notifications after two seconds', async () => {
  const { context, ui } = setup();
  context.handleTaskEvent(event(0, 'error'));
  await new Promise(resolve => setTimeout(resolve, 2100));
  context.handleTaskEvent(event(1999, 'error'));
  assert.equal(ui.toastQueue.length, 0);
});

test('historical files, preview and todos restore snapshots without live animations', () => {
  const { context } = setup();
  for (const type of ['edited_files_updated', 'preview_targets_updated', 'todo_updated']) {
    context.handleTaskEvent(event(10 + globalThis.__replayUpdates.length, type, { todo_list: {}, edited_files: [], preview_targets: [] }));
  }
  assert.deepEqual(globalThis.__replayUpdates.map(item => item.live), [false, false, false]);
  context.handleTaskEvent(event(2001, 'edited_files_updated', { edited_files: [] }));
  assert.equal(globalThis.__replayUpdates.at(-1).live, true);
});

test('a stale replay boundary cannot suppress another task or conversation', () => {
  const { context, ui } = setup();
  globalThis.__replayTask.currentTaskId = 't2';
  context.handleTaskEvent({ idx: 1, type: 'error', data: { task_id: 't2', conversation_id: 'c1' } });
  assert.equal(ui.toastQueue.length, 1);
  context.currentConversationId = 'c2';
  context.handleTaskEvent({ idx: 2, type: 'error', data: { task_id: 't2', conversation_id: 'c2' } });
  assert.equal(ui.toastQueue.length, 2);
});

test('notification suppression is restored if an event handler throws', () => {
  assert.throws(() => api.withoutReplayNotifications(() => { throw new Error('handler failed'); }));
  assert.equal(api.replayNotificationsSuppressed(), false);
});
