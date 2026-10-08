const assert = require('node:assert/strict');
const { compile, deferred, noop, locale, pinia } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const base = 'static/src/app/methods/';
const flush = async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); };

function fixture() {
  const requests = [], timers = new Map(), toasts = [], tabs = [];
  let nextTimer = 0, sent = 0;
  const setTimeout = (callback, delay) => {
    const id = ++nextTimer; timers.set(id, { callback, delay }); return id;
  };
  const clearTimeout = (id) => timers.delete(id);
  const globals = {
    setTimeout, clearTimeout,
    window: { setTimeout, localStorage: { getItem: () => null, setItem: noop } },
    fetch: (url, options) => {
      const request = deferred(); requests.push({ url, options, ...request }); return request.promise;
    }
  };
  const session = compile(base + 'conversation/session.ts');
  const store = compile('static/src/stores/conversation.ts', { pinia }, globals).useConversationStore();
  const owner = compile(base + 'auxiliaryOwnership.ts', {
    '../../stores/conversation': { useConversationStore: () => store },
    './conversation/session': session
  });
  const imports = {
    '@/locales': locale, '../common': { debugLog: noop, goalModeDebugLog: noop },
    '../conversation/session': session, '../auxiliaryOwnership': owner,
    '../../../stores/conversation': { useConversationStore: () => store },
    '../../../stores/conversationTabs': { useConversationTabsStore: () => ({ updateTitle: (...args) => tabs.push(args) }) },
    '../../../stores/task': {}, '../../../stores/resource': {}, './ownership': {}
  };
  const host = {
    ...compile(base + 'ui/workspace.ts', imports, globals).workspaceMethods,
    ...compile(base + 'message/controls.ts', imports, globals).messageControlMethods,
    ...compile(base + 'taskPolling/title.ts', imports, globals).titleMethods,
    ...compile(base + 'taskPolling/sync.ts', imports, globals).syncMethods,
    currentConversationId: 'A', currentHostWorkspaceId: 'ws', versioningHostMode: true,
    hostWorkspaces: [{ workspace_id: 'ws', running_task_count: 0 }],
    inputMessage: 'hello', composerBusy: false, mainChatIdle: true,
    $forceUpdate: noop, uiPushToast: (value) => toasts.push(value),
    sendMessage: () => { sent++; },
    isPlaceholderConversationTitle: (title) => !title || ['New Chat', '\u65b0\u5bf9\u8bdd'].includes(title)
  };
  for (const key of ['runningWorkspaceTasks', 'acknowledgedCompletedTaskIds', 'currentConversationTitle']) {
    Object.defineProperty(host, key, { get: () => store[key], set: (value) => { store[key] = value; } });
  }
  Object.defineProperty(host, 'currentWorkspaceHasRunningTask', { get: () =>
    host.runningWorkspaceTasks.some(task => task.workspace_id === host.currentHostWorkspaceId &&
      task.conversation_id === host.currentConversationId &&
      ['pending', 'running', 'cancel_requested'].includes(task.status))
  });
  const navigate = (id, ws = 'ws') => {
    session.beginConversationSession(host, id, ws);
    host.currentConversationId = id; host.currentHostWorkspaceId = ws;
    store.currentConversationId = id;
  };
  navigate('A');
  return { host, store, session, requests, timers, toasts, tabs, navigate,
    get sent() { return sent; },
    tick(delay) {
      const entry = [...timers].find(([, value]) => value.delay === delay);
      assert.ok(entry, `Missing timer ${delay}`);
      timers.delete(entry[0]); entry[1].callback();
    }
  };
}
const task = (status = 'running', extra = {}) => ({
  task_id: 'T', conversation_id: 'B', workspace_id: 'ws', status, ...extra
});
const respond = (request, tasks) => request.resolve({ ok: true, json: async () => ({ success: true, data: tasks }) });
const seed = async (f, tasks) => {
  const pending = f.host.refreshRunningWorkspaceTasks(); respond(f.requests.at(-1), tasks); await pending;
};
const tests = [];
const test = (name, run) => tests.push([name, run]);

test('navigation during refresh commits completion and does not strand a running task', async () => {
  const f = fixture(); await seed(f, [task()]);
  f.tick(1500); f.navigate('B'); respond(f.requests.at(-1), [task('succeeded')]); await flush();
  assert.equal(f.host.runningWorkspaceTasks.length, 0);
  assert.equal(f.host.currentWorkspaceHasRunningTask, false);
  assert.equal(f.host.hostWorkspaces[0].running_task_count, 0);
  assert.equal(f.timers.size, 0);
  await f.host.handleSendOrStop(); assert.equal(f.sent, 1); assert.equal(f.toasts.length, 0);
});
test('navigation while another task stays active keeps the refresh chain alive', async () => {
  const f = fixture(); await seed(f, [task()]); f.tick(1500); f.navigate('C', 'other');
  respond(f.requests.at(-1), [task()]); await flush();
  f.tick(1500); respond(f.requests.at(-1), [task('succeeded')]); await flush();
  assert.equal(f.host.runningWorkspaceTasks[0].status, 'succeeded');
  assert.equal(f.timers.size, 0);
});
test('older requests cannot resurrect a task after a newer completion snapshot', async () => {
  const f = fixture(); const old = f.host.refreshRunningWorkspaceTasks(); f.navigate('B');
  const latest = f.host.refreshRunningWorkspaceTasks();
  assert.equal(f.requests[0].options.signal.aborted, true);
  respond(f.requests[1], [task('succeeded')]); await latest;
  respond(f.requests[0], [task()]); assert.equal(await old, false);
  assert.equal(f.host.runningWorkspaceTasks.length, 0); assert.equal(f.timers.size, 0);
});
test('transport failure preserves cached tasks and schedules retry', async () => {
  const f = fixture(); await seed(f, [task()]); f.tick(1500);
  f.requests.at(-1).reject(new Error('offline')); await flush();
  assert.equal(f.host.runningWorkspaceTasks[0].status, 'running');
  f.tick(3000); respond(f.requests.at(-1), []); await flush(); assert.equal(f.timers.size, 0);
});
test('request timeout retries and unmount rejects late responses without rearming', async () => {
  const f = fixture(); const pending = f.host.refreshRunningWorkspaceTasks();
  f.tick(12000); assert.equal(f.requests[0].options.signal.aborted, true);
  f.requests[0].reject(new Error('aborted')); await pending;
  assert.ok([...f.timers.values()].some(timer => timer.delay === 3000));
  f.tick(3000); f.host.stopWorkspaceTasksRefresh();
  respond(f.requests.at(-1), [task()]); await flush();
  assert.equal(f.host.runningWorkspaceTasks.length, 0); assert.equal(f.timers.size, 0);
  assert.equal(await f.host.refreshRunningWorkspaceTasks(), false);
});
test('idle send verifies a stale running cache and proceeds after completion', async () => {
  const f = fixture(); f.navigate('B'); f.host.runningWorkspaceTasks = [task()];
  const send = f.host.handleSendOrStop(); assert.equal(f.sent, 0);
  respond(f.requests[0], [task('succeeded')]); await send;
  assert.equal(f.sent, 1); assert.equal(f.toasts.length, 0);
});
test('send still rejects a genuinely running task and preserves input', async () => {
  const f = fixture(); f.navigate('B'); f.host.runningWorkspaceTasks = [task()];
  const send = f.host.handleSendOrStop(); respond(f.requests[0], [task()]); await send;
  assert.equal(f.sent, 0); assert.equal(f.toasts[0].title, 'appMessages.conversationRunningTitle');
  assert.equal(f.host.inputMessage, 'hello');
});
test('send verification cannot send from a new view or after another submission starts', async () => {
  for (const change of ['navigation', 'submission']) {
    const f = fixture(); f.navigate('B'); f.host.runningWorkspaceTasks = [task()];
    const send = f.host.handleSendOrStop();
    if (change === 'navigation') f.navigate('C');
    else f.session.beginConversationSubmission(f.session.currentConversationSession(f.host));
    respond(f.requests[0], []); await send;
    assert.equal(f.sent, 0); assert.equal(f.toasts.length, 0); assert.equal(f.host.inputMessage, 'hello');
  }
});
test('failed verification retains the cache and does not send blindly', async () => {
  const f = fixture(); f.navigate('B'); f.host.runningWorkspaceTasks = [task()];
  const send = f.host.handleSendOrStop(); f.requests[0].reject(new Error('offline')); await send;
  assert.equal(f.sent, 0); assert.equal(f.host.runningWorkspaceTasks[0].status, 'running');
  assert.equal(f.toasts[0].message, 'appUi.fetchRunningTasksFailed');
});
test('title events update both type caches, workspace groups, search and tabs in place', () => {
  const f = fixture(); const item = () => ({ id: 'A', title: 'New Chat' });
  f.store.conversations = [item()]; f.store.conversationsCache.normal.list = f.store.conversations;
  f.store.conversationsCache.multi_agent.list = [item()]; f.store.searchResults = [item()];
  f.store.searchGroups = [{ results: [item()] }]; f.store.ensureWorkspaceGroup('ws');
  const group = f.store.workspaceGroups[0]; group.conversations.push(item());
  group.conversationsByType.multi_agent.push(item());
  f.store.runningWorkspaceTasks = [task('running', { conversation_id: 'A', conversation_title: 'New Chat' })];
  const lists = [f.store.conversations, f.store.conversationsCache.multi_agent.list,
    f.store.searchResults, f.store.searchGroups[0].results, group.conversations,
    group.conversationsByType.multi_agent];
  f.host.handleConversationChanged({ conversation_id: 'A', title: 'Generated title' }, 0);
  for (const list of lists) assert.equal(list[0].title, 'Generated title');
  assert.equal(group.conversations, group.conversationsByType.normal);
  assert.equal(f.host.currentConversationTitle, 'Generated title');
  assert.equal(f.store.runningWorkspaceTasks[0].conversation_title, 'Generated title');
  assert.deepEqual(f.tabs.at(-1), ['A', 'Generated title']);
});
test('task index refreshes titles generated after background completion', async () => {
  const f = fixture(); f.store.ensureWorkspaceGroup('ws');
  const group = f.store.workspaceGroups[0]; group.conversations.push({ id: 'B', title: 'New Chat' });
  await seed(f, [task('succeeded', { conversation_title: 'New Chat', updated_at: Date.now() / 1000 })]);
  f.tick(1500); respond(f.requests.at(-1), [task('succeeded', {
    conversation_title: 'Late title', updated_at: Date.now() / 1000
  })]); await flush();
  assert.equal(group.conversations[0].title, 'Late title'); assert.equal(f.timers.size, 0);
});
test('title metadata requests specify conversation and workspace and reject navigation ABA', async () => {
  const f = fixture(); const old = f.host.fetchGeneratedConversationTitle('A');
  assert.equal(f.requests[0].url, '/api/conversations/A/metadata?workspace_id=ws');
  f.navigate('B'); f.navigate('A');
  respond(f.requests[0], { id: 'A', title: 'old', title_locked: true });
  assert.equal(await old, false); assert.equal(f.host.currentConversationTitle, '');
  const latest = f.host.fetchGeneratedConversationTitle('A');
  f.requests[1].resolve({ok:true,json:async()=>({success:true,data:{id:'A',title:'Current title',title_locked:true}})});
  assert.equal(await latest, true); assert.equal(f.host.currentConversationTitle, 'Current title');
});
test('title refresh does not schedule a successor after navigation during its request', async () => {
  const f = fixture(); f.host.scheduleGeneratedTitleRefresh('test'); f.navigate('B');
  f.requests[0].resolve({ok:true,json:async()=>({success:true,data:{id:'A',title:'old',title_locked:true}})});
  await flush(); assert.equal(f.timers.size, 0); assert.equal(f.host.currentConversationTitle, '');
});

(async () => {
  for (const [name, run] of tests) { await run(); console.log(`PASS ${name}`); }
})().catch(error => { console.error(error); process.exitCode = 1; });
