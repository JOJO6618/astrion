// 2026-10-10 侧边栏点击统一与发送守卫回归
// 运行：node test/2026-10-10_侧边栏点击统一与发送守卫/regressions.cjs
//
// 覆盖：
// 1. 静态断言：侧边栏/App.vue 点击统一到 select-workspace-conversation，loadConversation 无残留；
// 2. 发送守卫：对话切换加载窗口（session≠currentConversationId 且 historyLoading）内拒绝发送，
//    不再落入「暂不可堆积/暂无任务」路径；视图一致与运行中入队场景不受影响；
// 3. runtimeQueue 防御：session 与视图不一致时静默放弃入队，原提示逻辑保留；
// 4. 统一入口 handleSelectWorkspaceConversation：同对话短路、force 重载、完整收尾链、
//    跨工作区先切换、失败/异常 toast。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { compile, deferred, noop, locale } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const base = 'static/src/app/methods/';
const root = path.resolve(__dirname, '../..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');
const flush = async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); };

const tests = [];
const test = (name, run) => tests.push([name, run]);

/* ────────────────────────── Part 1: 源码静态断言 ────────────────────────── */

test('静态：入口统一且 loadConversation 无残留', () => {
  const sidebar = read('static/src/components/sidebar/ConversationSidebar.vue');
  assert.match(sidebar, /select-workspace-conversation/);
  assert.doesNotMatch(sidebar, /emit\('select'/);
  assert.doesNotMatch(sidebar, /@select="/);
  assert.doesNotMatch(sidebar, /\(event: 'select'/);
  assert.match(sidebar, /handleWorkspaceConversationClick\(conv\.id, String\(currentWorkspaceId \|\| ''\)\)/);

  const app = read('static/src/App.vue');
  assert.doesNotMatch(app, /@select="loadConversation"/);
  assert.ok(
    (app.match(/@select-workspace-conversation/g) || []).length >= 2,
    'App.vue 应有桌面版与移动版两处 select-workspace-conversation 绑定'
  );

  const load = read(base + 'conversation/load.ts');
  assert.doesNotMatch(load, /async loadConversation\(/);
  assert.match(load, /loadMoreConversations/);

  const controls = read(base + 'message/controls.ts');
  assert.match(controls, /conversationOpeningTitle/);

  const runtimeQueue = read(base + 'message/runtimeQueue.ts');
  assert.match(runtimeQueue, /String\(session\.conversationId\) === String\(conversationId\)/);

  const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return walk(full);
    if (!/\.(ts|vue)$/.test(entry.name)) return [];
    return [{ file: path.relative(root, full), text: fs.readFileSync(full, 'utf8') }];
  });
  for (const { file, text } of walk(path.join(root, 'static/src'))) {
    assert.doesNotMatch(text, /\.loadConversation\(/, `发现残留 loadConversation 调用: ${file}`);
  }
});

/* ────────────────────────── Part 2: 发送守卫行为 ────────────────────────── */

function controlsFixture(overrides = {}) {
  const session = compile(base + 'conversation/session.ts');
  const toasts = [];
  let sent = 0;
  let queued = 0;
  const taskStore = { clearTask: noop, currentTaskId: null };
  const host = {
    ...compile(base + 'message/controls.ts', {
      '../common': { goalModeDebugLog: noop },
      '@/locales': locale,
      '../../../stores/task': { useTaskStore: () => taskStore },
      './ownership': { ensureMessageSession: (h) => session.currentConversationSession(h) },
      '../conversation/session': session
    }).messageControlMethods,
    currentConversationId: 'B',
    currentHostWorkspaceId: 'ws',
    historyLoading: true,
    inputMessage: 'hello',
    composerBusy: false,
    mainChatIdle: true,
    selectedFiles: [],
    selectedImages: [],
    selectedVideos: [],
    pendingUserQuestions: [],
    uiPushToast: (value) => toasts.push(value),
    sendMessage: () => { sent++; },
    enqueueRuntimeQueuedMessage: async () => { queued++; return true; },
    inputClearMessage: noop,
    inputClearSelectedFiles: noop,
    inputSetLineCount: noop,
    inputSetMultiline: noop,
    autoResizeInput: noop,
    ...overrides
  };
  session.beginConversationSession(host, 'A', 'ws');
  return {
    host,
    toasts,
    sent: () => sent,
    queued: () => queued
  };
}

test('守卫：切换加载窗口内发送被拦截，不再误弹暂不可堆积', async () => {
  const f = controlsFixture();
  // 旧 bug 条件：视图仍显示旧对话（运行残留 composerBusy），session 已指向新对话
  f.host.composerBusy = true;
  f.host.mainChatIdle = false;
  await f.host.handleSendOrStop();
  assert.equal(f.sent(), 0);
  assert.equal(f.queued(), 0);
  assert.equal(f.toasts.length, 1);
  assert.equal(f.toasts[0].title, 'appMessages.conversationOpeningTitle');
  assert.equal(f.toasts[0].type, 'info');
  assert.ok(!f.toasts.some((item) => item.title === 'appMessages.cannotQueueTitle'));
  assert.equal(f.host.inputMessage, 'hello');
});

test('守卫：首次打开对话（无当前对话）窗口内发送同样被拦截', async () => {
  const f = controlsFixture();
  f.host.currentConversationId = null;
  await f.host.handleSendOrStop();
  assert.equal(f.sent(), 0);
  assert.equal(f.toasts.length, 1);
  assert.equal(f.toasts[0].title, 'appMessages.conversationOpeningTitle');
});

test('守卫：视图与 session 一致时正常发送', async () => {
  const f = controlsFixture();
  f.host.currentConversationId = 'A';
  f.host.historyLoading = false;
  await f.host.handleSendOrStop();
  assert.equal(f.sent(), 1);
  assert.equal(f.toasts.length, 0);
});

test('守卫：运行中对话内发送正常进入运行队列', async () => {
  const f = controlsFixture();
  f.host.currentConversationId = 'A';
  f.host.historyLoading = false;
  f.host.composerBusy = true;
  f.host.mainChatIdle = false;
  await f.host.handleSendOrStop();
  assert.equal(f.queued(), 1);
  assert.equal(f.sent(), 0);
  assert.equal(f.toasts.length, 0);
});

/* ────────────────────── Part 3: runtimeQueue 入队防御 ────────────────────── */

function runtimeQueueFixture(currentConversationId, taskStore) {
  const session = compile(base + 'conversation/session.ts');
  const toasts = [];
  const requests = [];
  const fetchMock = (url, options) => {
    const request = deferred();
    requests.push({ url, options, ...request });
    return request.promise;
  };
  const host = {
    ...compile(base + 'message/runtimeQueue.ts', {
      '../common': { debugLog: noop },
      '@/locales': locale,
      '../conversation/session': session,
      '../../../stores/task': { useTaskStore: () => taskStore }
    }, { fetch: fetchMock }).runtimeQueueMethods,
    currentConversationId,
    uiPushToast: (value) => toasts.push(value),
    applyRuntimeQueuedMessages: () => [],
    setRuntimeQueueSyncLock: noop
  };
  return { host, session, toasts, requests };
}

test('runtimeQueue：session 与视图不一致时静默放弃入队', async () => {
  const f = runtimeQueueFixture('B', { currentTaskId: 'T1', pollGeneration: 1 });
  f.session.beginConversationSession(f.host, 'A', 'ws');
  const result = await f.host.enqueueRuntimeQueuedMessage('hi');
  assert.equal(result, false);
  assert.equal(f.requests.length, 0);
  assert.equal(f.toasts.length, 0);
});

test('runtimeQueue：任务索引为空时保留提示（原行为兜底）', async () => {
  const f = runtimeQueueFixture('B', { currentTaskId: null, pollGeneration: 1 });
  f.session.beginConversationSession(f.host, 'B', 'ws');
  const result = await f.host.enqueueRuntimeQueuedMessage('hi');
  assert.equal(result, false);
  assert.equal(f.requests.length, 0);
  assert.equal(f.toasts.length, 1);
  assert.equal(f.toasts[0].title, 'appMessages.cannotQueueTitle');
});

test('runtimeQueue：一致且有任务时正常发起入队请求', async () => {
  const f = runtimeQueueFixture('B', { currentTaskId: 'T1', pollGeneration: 3 });
  f.session.beginConversationSession(f.host, 'B', 'ws');
  const pending = f.host.enqueueRuntimeQueuedMessage('hi', []);
  await flush();
  assert.equal(f.requests.length, 1);
  assert.ok(String(f.requests[0].url).includes('/api/tasks/T1/runtime_queue'));
  f.requests[0].resolve({ ok: true, json: async () => ({ success: true, data: { messages: [] } }) });
  assert.equal(await pending, true);
});

/* ────────────────────── Part 4: 统一入口收尾链 ────────────────────── */

function hostWorkspaceFixture() {
  const session = compile(base + 'conversation/session.ts');
  const calls = { enter: [], switch: [], ack: [], order: [] };
  const toasts = [];
  const openedTabs = [];
  const store = { workspaceGroups: [], conversationsCache: {} };
  const tabs = {
    enabled: true,
    tabs: [],
    setActive: noop,
    persistNow: noop,
    openConversationTab: (payload) => { openedTabs.push(payload); calls.order.push('openTab'); }
  };
  const host = {
    ...compile(base + 'ui/hostWorkspace.ts', {
      '@/locales': locale,
      '../auxiliaryOwnership': { beginAuxiliaryRequest: () => () => true },
      '../../../stores/conversation': { useConversationStore: () => store },
      '../../../stores/conversationTabs': { useConversationTabsStore: () => tabs },
      '../conversation/session': session
    }).hostWorkspaceMethods,
    versioningHostMode: true,
    dockerProjectMode: false,
    currentHostWorkspaceId: 'ws1',
    hostWorkspaces: [
      { workspace_id: 'ws1', label: 'W1' },
      { workspace_id: 'ws2', label: 'W2' }
    ],
    currentConversationId: 'A',
    historyLoading: false,
    titleReady: false,
    suppressTitleTyping: false,
    conversations: [],
    conversationsOffset: 0,
    uiPushToast: (value) => toasts.push(value),
    persistComposerDraftNow: () => Promise.resolve(),
    flushReasoningEffortSave: noop,
    startTitleTyping: noop,
    restoreComposerDraftState: async () => { calls.order.push('draft-restore'); },
    subAgentFetch: () => calls.order.push('subAgent'),
    fetchTodoList: () => calls.order.push('todo'),
    refreshRunningWorkspaceTasks: async () => { calls.order.push('refreshTasks'); },
    getVisibleWorkspaceTaskForConversation: () => ({ task_id: 'T1', status: 'succeeded' }),
    acknowledgeCompletedWorkspaceTask: (id) => { calls.ack.push(id); },
    fetchWorkMode: () => calls.order.push('workMode'),
    fetchPermissionMode: noop,
    fetchExecutionMode: noop,
    fetchNetworkPermission: noop,
    fetchVersioningStatus: async (id) => { calls.order.push(`versioning:${id}`); },
    refreshProjectGitSummary: noop,
    fetchConversationTokenStatistics: noop,
    updateCurrentContextTokens: noop,
    enterConversation: async (id, options) => {
      calls.enter.push({ id, options });
      calls.order.push('enter');
      host.currentConversationId = id;
      host.historyLoading = false;
      return { success: true, title: `T:${id}` };
    },
    handleHostWorkspaceSwitch: async (workspaceId, options) => {
      calls.switch.push({ workspaceId, options });
      calls.order.push('switch');
      host.currentHostWorkspaceId = workspaceId;
    },
    beginConversationView: (conversationId, workspaceId) =>
      session.beginConversationSession(
        host,
        conversationId,
        workspaceId || host.currentHostWorkspaceId || ''
      )
  };
  return { host, session, calls, toasts, openedTabs };
}

test('入口：点击当前对话短路跳过', async () => {
  const f = hostWorkspaceFixture();
  const result = await f.host.handleSelectWorkspaceConversation({ conversationId: 'A', workspaceId: 'ws1' });
  assert.equal(result.success, true);
  assert.equal(result.skipped, true);
  assert.equal(f.calls.enter.length, 0);
  assert.equal(f.host.titleReady, true);
  assert.equal(f.host.suppressTitleTyping, false);
});

test('入口：force 时同一对话也重新加载', async () => {
  const f = hostWorkspaceFixture();
  const result = await f.host.handleSelectWorkspaceConversation(
    { conversationId: 'A', workspaceId: 'ws1' },
    { force: true }
  );
  assert.equal(f.calls.enter.length, 1);
  assert.equal(f.calls.enter[0].id, 'A');
  assert.equal(result.success, true);
});

test('入口：加载成功后执行完整收尾链', async () => {
  const f = hostWorkspaceFixture();
  const result = await f.host.handleSelectWorkspaceConversation({ conversationId: 'B', workspaceId: 'ws1' });
  assert.equal(result.success, true);
  assert.equal(result.title, 'T:B');
  assert.equal(f.calls.enter.length, 1);
  assert.equal(f.calls.enter[0].id, 'B');
  assert.equal(f.calls.enter[0].options.source, 'workspace');
  assert.equal(f.calls.enter[0].options.urlMode, 'push');
  assert.equal(f.calls.enter[0].options.workspaceId, 'ws1');
  assert.ok(f.calls.enter[0].options.session, 'enterConversation 应携带统一 session');
  for (const step of ['draft-restore', 'subAgent', 'todo', 'refreshTasks', 'workMode', 'versioning:B']) {
    assert.ok(f.calls.order.includes(step), `缺少收尾步骤 ${step}`);
  }
  assert.deepEqual(f.calls.ack, ['T1']);
  assert.equal(f.host.currentConversationTitle, 'T:B');
  assert.equal(f.host.titleReady, true);
  assert.equal(f.openedTabs.length, 1);
  assert.equal(f.openedTabs[0].conversationId, 'B');
  assert.equal(f.openedTabs[0].workspaceId, 'ws1');
  assert.equal(f.toasts.length, 0);
});

test('入口：跨工作区先切工作区再加载', async () => {
  const f = hostWorkspaceFixture();
  const result = await f.host.handleSelectWorkspaceConversation({ conversationId: 'C', workspaceId: 'ws2' });
  assert.equal(result.success, true);
  assert.equal(f.calls.switch.length, 1);
  assert.equal(f.calls.switch[0].workspaceId, 'ws2');
  assert.equal(f.calls.switch[0].options.preserveConversationView, true);
  assert.ok(f.calls.switch[0].options.session, '切换工作区应携带预创建的 session');
  assert.ok(f.calls.order.indexOf('switch') < f.calls.order.indexOf('enter'));
  assert.equal(f.calls.enter[0].id, 'C');
  assert.equal(f.calls.enter[0].options.workspaceId, 'ws2');
});

test('入口：加载失败与异常均以 toast 上报', async () => {
  const failed = hostWorkspaceFixture();
  failed.host.enterConversation = async () => ({ success: false, message: 'boom' });
  const failedResult = await failed.host.handleSelectWorkspaceConversation({ conversationId: 'D', workspaceId: 'ws1' });
  assert.equal(failedResult.success, false);
  assert.equal(failed.toasts.at(-1).title, 'appMessages.loadConversationFailedTitle');
  assert.equal(failed.host.titleReady, true);

  const threw = hostWorkspaceFixture();
  threw.host.enterConversation = async () => { throw new Error('network down'); };
  const threwResult = await threw.host.handleSelectWorkspaceConversation({ conversationId: 'D', workspaceId: 'ws1' });
  assert.equal(threwResult.success, false);
  assert.equal(threw.toasts.at(-1).title, 'appMessages.loadConversationErrorTitle');
  assert.equal(threw.host.titleReady, true);
});

(async () => {
  for (const [name, run] of tests) {
    await run();
    console.log(`PASS ${name}`);
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
