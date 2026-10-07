/* Read-only investigation: run current TS methods with synthetic IO and isolated state. */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const ts = require('../../node_modules/typescript');
const root = path.resolve(__dirname, '../..');
const noop = () => {};
const deferred = () => {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
};
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
const response = (data) => ({ ok: true, json: async () => ({ success: true, data }) });

function load(relative, deps = {}, globals = {}) {
  const source = fs.readFileSync(path.join(root, relative), 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS }
  }).outputText;
  const exports = {};
  const common = { debugLog: noop, traceLog: noop, goalModeDebugLog: noop };
  const shared = { debugNotifyLog: noop, keyNotifyLog: noop, jsonDebug: noop,
    restoreDebugLog: noop, userMDebug: noop };
  const emptyStore = { $patch: noop, setEditedFiles: noop, setRuntime: noop,
    setTargets: noop, setWorkflow: noop, enabled: false };
  const defaults = {
    '../common': common, '../app/methods/common': common, './common': common,
    './shared': shared, '@/locales': { t: (key) => key },
    '../../../stores/conversation': { useConversationStore: () => emptyStore },
    '../../../stores/quickDock': { useQuickDockStore: () => emptyStore },
    '../../../stores/preview': { usePreviewStore: () => emptyStore },
    '../../../stores/workflow': { useWorkflowStore: () => emptyStore },
    '../../../stores/conversationTabs': { useConversationTabsStore: () => emptyStore },
    '../../stores/quickDock': { useQuickDockStore: () => emptyStore },
    '../../stores/preview': { usePreviewStore: () => emptyStore }
  };
  const context = {
    exports, module: { exports },
    require: (name) => {
      if (Object.hasOwn(deps, name)) return deps[name];
      if (Object.hasOwn(defaults, name)) return defaults[name];
      throw new Error(`Unmocked import: ${name}`);
    },
    console: { log: noop, warn: noop, error: noop }, Date, Set, Map, Promise,
    AbortSignal, setTimeout: (cb) => { cb(); return 1; }, clearTimeout: noop,
    window: { setInterval: () => 1, clearInterval: noop, localStorage: { getItem: () => null } },
    clearInterval: noop, history: { pushState: noop, replaceState: noop },
    ...globals
  };
  vm.runInNewContext(compiled, context, { filename: relative });
  return context.module.exports;
}

function bootstrapHost() {
  return {
    messages: [], currentConversationId: null, currentHostWorkspaceId: 'ws',
    stripConversationPrefix: (id) => id.replace(/^conv_/, ''),
    modelSet: noop, fetchTerminalCount: noop, promoteConversationToTop: noop,
    resetAllStates: noop, logMessageState: noop, refreshBlankHeroState: noop,
    applyRuntimeQueuedMessages: noop, handleCompressionState: noop,
    settleHistoryRenderAndScroll: async () => {}, restoreTaskState: async () => {},
    renderHistoryMessages(messages) { this.messages.push(...messages); }
  };
}

async function staleBootstrapWins() {
  const pendingA = deferred();
  const api = load('static/src/app/methods/conversation/bootstrap.ts', {}, {
    fetch: async (url) => url.includes('/A/bootstrap') ? pendingA.promise
      : response(url.includes('/B/bootstrap') ? {
        conversation_id: 'B', meta: { title: 'B' }, messages: [{ role: 'user', content: 'B' }]
      } : {})
  });
  const host = bootstrapHost();
  const a = api.bootstrapMethods.enterConversation.call(host, 'A');
  await api.bootstrapMethods.enterConversation.call(host, 'B');
  assert.equal(host.currentConversationId, 'B');
  pendingA.resolve(response({ conversation_id: 'A', meta: {}, messages: [{ role: 'user', content: 'A' }] }));
  await a;
  assert.equal(host.currentConversationId, 'A');
  return 'B 已完成加载后，A 的迟到 bootstrap 仍把当前对话及消息覆盖为 A。';
}

async function reconcilePreemptsRestore() {
  const settle = deferred();
  let rebuilt = false;
  const store = { currentTaskId: null, isPolling: false, lastEventIndex: 8,
    resumeTask(id, options) {
      this.currentTaskId = id;
      if (options.resetOffset) this.lastEventIndex = 0;
      this.isPolling = true;
    }
  };
  const deps = { '../../../stores/task': { useTaskStore: () => store } };
  const api = load('static/src/app/methods/conversation/bootstrap.ts', {}, {
    fetch: async (url) => response(url.includes('/bootstrap') ? {
      conversation_id: 'A', meta: {}, messages: [
        { role: 'user', content: 'question' },
        { role: 'assistant', actions: [{ type: 'text', content: 'already saved', streaming: false }] }
      ], running: { is_main_running: true },
      task_replay: { task: { task_id: 'T', status: 'running' }, needs_rebuild: true,
        replay_from: 0, events: [{ idx: 0, type: 'ai_message_start' }], decision_inputs: {} }
    } : {})
  });
  const compression = load('static/src/app/methods/taskPolling/compression.ts', deps);
  const probe = load('static/src/app/methods/taskPolling/probe.ts', deps, {
    fetch: async () => response({ is_truly_active: true, is_main_running: true, main_task_id: 'T' })
  });
  const ai = load('static/src/app/methods/taskPolling/aiStream.ts');
  const host = bootstrapHost();
  Object.assign(host, {
    taskInProgress: false, streamingMessage: false, clearProcessedEvents: noop,
    startRunningStateReconcile: noop, handleTaskEvent: noop,
    isConversationIndependentRoute: () => false, isExplicitNewConversationRoute: () => false,
    settleHistoryRenderAndScroll: () => settle.promise,
    restoreTaskState: compression.compressionMethods.restoreTaskState,
    $forceUpdate: noop, $nextTick: noop, monitorResetSpeech: noop, cleanupStaleToolActions: noop,
    chatStartAssistantMessage() { rebuilt = true; this.messages.push({ role: 'assistant', actions: [] }); }
  });
  const entering = api.bootstrapMethods.enterConversation.call(host, 'A');
  await flush();
  assert.equal(host.messages.length, 2);
  await probe.probeMethods.reconcileRunningStateOnce.call(host);
  assert.equal(store.lastEventIndex, 0);
  settle.resolve();
  await entering;
  assert.equal(host.messages[1].actions.length, 1, 'persisted assistant was not cleared for replay');
  ai.aiStreamMethods.handleAiMessageStart.call(host, { task_id: 'T' }, 0);
  assert.equal(rebuilt, true);
  assert.equal(host.messages.length, 3);
  return '布局等待期间对账从 0 接管；正式恢复被 taskInProgress 跳过，保留磁盘 assistant，重放 ai_message_start 又创建 assistant。';
}

function taskModule(fetch) {
  let store;
  const pinia = { defineStore: (_name, config) => {
    store = config.state();
    for (const [key, action] of Object.entries(config.actions)) store[key] = action.bind(store);
    for (const [key, getter] of Object.entries(config.getters)) {
      Object.defineProperty(store, key, { get: () => getter(store) });
    }
    return () => store;
  } };
  load('static/src/stores/task.ts', { pinia }, { fetch });
  return store;
}

async function oldSameTaskPollAccepted() {
  const requests = [];
  const store = taskModule(() => { const d = deferred(); requests.push(d); return d.promise; });
  store.currentTaskId = 'T'; store.taskStatus = 'running'; store.lastEventIndex = 10;
  const received = [];
  const old = store.pollTaskEvents((event) => received.push(event));
  store.stopPolling();
  store.currentTaskId = 'T'; store.taskStatus = 'running'; store.lastEventIndex = 0;
  const fresh = store.pollTaskEvents((event) => received.push(event));
  assert.equal(store.pollingInFlight, true);
  requests[0].resolve(response({ task_id: 'T', conversation_id: 'A', status: 'running',
    next_offset: 12, events: [{ idx: 11, type: 'text_chunk', data: { chunk: 'old' } }] }));
  await old;
  assert.equal(received.some((event) => event.idx === 11), true);
  assert.equal(store.lastEventIndex, 12);
  assert.equal(store.pollingInFlight, false, 'old finally clears newer in-flight ownership');
  requests[1].resolve(response({ task_id: 'T', status: 'running', events: [], next_offset: 0 }));
  await fresh;
  return '停止并重新接管同一任务后，旧轮询响应仍获接受、推进新游标，并清掉新请求的单飞标记。';
}

async function oldPoll404StopsNewTask() {
  const request = deferred();
  const store = taskModule(() => request.promise);
  store.currentTaskId = 'TA'; store.taskStatus = 'running';
  const old = store.pollTaskEvents(noop);
  store.stopPolling();
  store.currentTaskId = 'TB'; store.taskStatus = 'running'; store.isPolling = true;
  request.resolve({ ok: false, status: 404 });
  await old;
  assert.equal(store.currentTaskId, null);
  assert.equal(store.isPolling, false);
  return '旧任务 TA 的迟到 404 进入无身份校验的 catch，停止了当前任务 TB。';
}

async function staleHistoryBodyWins() {
  const body = deferred();
  const api = load('static/src/app/methods/history.ts', {}, {
    fetch: async () => ({ ok: true, json: () => body.promise })
  });
  const host = {
    currentConversationId: 'A', messages: [], historyLoadSeq: 0,
    historyLoading: false, historyLoadingFor: null, logMessageState: noop,
    refreshBlankHeroState: noop, renderHistoryMessages(messages) { this.messages.push(...messages); },
    settleHistoryRenderAndScroll: async () => {}
  };
  const old = api.historyMethods.fetchAndDisplayHistory.call(host);
  await flush();
  host.currentConversationId = 'B'; host.messages = [{ role: 'user', content: 'B' }];
  body.resolve({ success: true, data: { conversation_id: 'A', messages: [{ role: 'user', content: 'A' }] } });
  await old;
  assert.equal(host.currentConversationId, 'B');
  assert.equal(host.messages[0].content, 'A');
  return '历史路径在 response.json() 前校验；解析等待时切到 B，A 的响应仍覆盖 B 的消息。';
}

async function coldWindowGapIgnored() {
  const received = [];
  const store = taskModule(async () => response({ task_id: 'T', status: 'running',
    window_start: 100, next_offset: 102, events: [{ idx: 101, type: 'text_chunk', data: {} }] }));
  store.currentTaskId = 'T'; store.taskStatus = 'running'; store.lastEventIndex = 0;
  await store.pollTaskEvents((event) => received.push(event));
  assert.equal(received.some((event) => event.type === 'event_window_gap'), false);
  assert.equal(received.some((event) => event.idx === 101), true);
  return '冷恢复 from=0、事件窗口从 100 开始时未报告缺口，直接接受剩余尾部事件。';
}

(async () => {
  for (const [name, run] of Object.entries({ staleBootstrapWins, reconcilePreemptsRestore,
    oldSameTaskPollAccepted, oldPoll404StopsNewTask, staleHistoryBodyWins, coldWindowGapIgnored })) {
    console.log(`CONFIRMED ${name}: ${await run()}`);
  }
  console.log('All six isolated schedules confirmed. No services or real conversation data accessed.');
})().catch((error) => { console.error(error); process.exitCode = 1; });
