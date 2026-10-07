/* Exercise the real loader/session/history with adversarial response ordering. */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const ts = require('../../node_modules/typescript');
const root = path.resolve(__dirname, '../..');
const noop = () => {};
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
};
const flush = async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); };
const plain = (value) => JSON.parse(JSON.stringify(value));
function compile(file, globals, imports = {}) {
  const module = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS }
  }).outputText;
  vm.runInNewContext(code, {
    module, exports: module.exports, AbortController, Promise, Map, WeakMap,
    setTimeout, clearTimeout, Date, console: { log: noop, warn: noop, error: noop },
    require: (key) => {
      if (Object.hasOwn(imports, key)) return imports[key];
      throw new Error(`Unmocked dependency ${file}: ${key}`);
    }, ...globals
  }, { filename: file });
  return module.exports;
}
function fixture() {
  const requests = [], attachments = [], microtasks = [], workflowWrites = [];
  let commits = 0, messages = [];
  const session = compile('static/src/app/methods/conversation/session.ts', {});
  const display = compile('static/src/app/methods/conversation/display.ts', {});
  const task = { clearTask: noop, attachSnapshot: (...args) => attachments.push(args) };
  const approval = compile('static/src/components/input/approvalModel.ts', {});
  const history = compile('static/src/app/methods/history.ts', {}, {
    './common': { debugLog: noop }, './conversation/display': display, '@/locales': { t: (key) => key }
  }).historyMethods;
  const methods = compile('static/src/app/methods/conversation/bootstrap.ts', {
    history: { pushState: noop, replaceState: noop },
    fetch: (url, options = {}) => {
      const pending = deferred(); requests.push({ url, options, ...pending });
      return pending.promise;
    }
  }, {
    './display': display, '../common': { debugLog: noop }, '../ui/shared': { parseSystemNoticeLabel: (value) => value }, './session': session,
    '../auxiliaryOwnership': { bindAuxiliaryConversationHost: noop },
    '../../../stores/task': { useTaskStore: () => task },
    '../../../stores/quickDock': { useQuickDockStore: () => ({ setEditedFiles: noop }) },
    '../../../stores/preview': { usePreviewStore: () => ({ setRuntime: noop, setTargets: noop }) },
    '../../../stores/conversation': { useConversationStore: () => ({ $patch: noop }) },
    '../../../stores/workflow': { useWorkflowStore: () => ({ setWorkflow: (...args) => workflowWrites.push(args) }) },
    '../../../stores/conversationTabs': { useConversationTabsStore: () => ({ enabled: false }) },
    '../../../stores/personalization': { usePersonalizationStore: () => ({ form: { hide_tool_approval_panel: true } }) },
    '@/components/input/approvalModel': approval
  }).bootstrapMethods;
  const host = {
    ...methods, ...history, currentConversationId: null, currentHostWorkspaceId: 'ws',
    historyLoading: false, preparingTools: new Map(), approvalSnapshotVersion: 0,
    currentPermissionMode: 'auto_approval',
    cloneToolArguments: (value) => ({ ...value }), buildToolLabel: () => 'label',
    clearLocalTaskUiState: noop, modelSet: noop, startTitleTyping: noop,
    applyRuntimeQueuedMessages: noop, handleCompressionState: noop,
    promoteConversationToTop: noop, refreshBlankHeroState: noop,
    startRunningStateReconcile: noop, stopRunningStateReconcile: noop,
    scrollHistoryToBottomInstant: noop, conditionalScrollToBottom: noop,
    $nextTick: (fn) => microtasks.push(fn), logMessageState: noop,
    chatSetThinkingLock: noop, toolRegisterAction: noop, toolTrackAction: noop,
    stripConversationPrefix: (value) => value.replace(/^conv_/, '')
  };
  Object.defineProperty(host, 'messages', { get: () => messages,
    set: (value) => { commits++; messages = value; } });
  return { host, session, requests, attachments, microtasks, workflowWrites,
    get commits() { return commits; } };
}
function payload(id, overrides = {}) {
  return { success: true, data: {
    conversation_id: id, meta: { title: id },
    messages: [{ role: 'user', message_id: `${id}-u`, content: 'old input' }],
    display: { messages: [{ role: 'assistant', id: `${id}-runtime`, actions: [
      { id: `${id}-text`, type: 'text', content: `live ${id}`, streaming: true }
    ] }], state: { streaming: true }, task: { task_id: `${id}-task`, status: 'running' }, next_event_idx: 257 },
    running: { is_truly_active: true, is_main_running: true }, ...overrides
  } };
}
function resolve(request, result) {
  request.resolve({ ok: true, json: async () => result });
}
async function lateBootstrapCannotOverwriteNewNavigation() {
  const f = fixture();
  const a = f.host.enterConversation('A');
  const b = f.host.enterConversation('B');
  resolve(f.requests[1], payload('B')); await b;
  resolve(f.requests[0], payload('A')); const old = await a;
  assert.equal(old.superseded, true);
  assert.equal(f.host.currentConversationId, 'B');
  assert.equal(f.commits, 1);
  assert.equal(f.attachments.length, 1);
  assert.equal(f.attachments[0][1], 257);
  assert.equal(f.requests[0].options.signal.aborted, true);
  assert.equal(f.host.messages.at(-1).actions[0].content, 'live B');
}
async function abaNavigationUsesIdentityNotConversationName() {
  const f = fixture();
  const a = f.host.enterConversation('A');
  const b = f.host.enterConversation('B');
  const freshA = f.host.enterConversation('A');
  resolve(f.requests[2], payload('A', { meta: { title: 'new A' } })); await freshA;
  resolve(f.requests[0], payload('A', { meta: { title: 'old A' } })); await a;
  resolve(f.requests[1], payload('B')); await b;
  assert.equal(f.commits, 1);
  assert.equal(f.host.currentConversationTitle, 'new A');
}
async function jsonParsingAndFinallyCannotChangeNewSession() {
  const f = fixture(), parsed = deferred();
  const a = f.host.enterConversation('A');
  f.requests[0].resolve({ ok: true, json: () => parsed.promise }); await flush();
  const b = f.host.enterConversation('B');
  parsed.resolve(payload('A')); await a;
  assert.equal(f.host.historyLoading, true);
  assert.equal(f.host.historyLoadingFor, 'B');
  resolve(f.requests[1], payload('B')); await b;
  assert.equal(f.commits, 1);
}
async function leavingCancelsBootstrapAndEventCallbacks() {
  const f = fixture();
  const a = f.host.enterConversation('A');
  f.host.leaveConversationView();
  resolve(f.requests[0], payload('A')); await a;
  assert.equal(f.commits, 0);
  const b = f.host.enterConversation('B'); resolve(f.requests[1], payload('B')); await b;
  let events = 0; f.host.handleTaskEvent = () => events++;
  const callback = f.attachments[0][2];
  callback({ type: 'text_chunk' }); assert.equal(events, 1);
  f.host.leaveConversationView(); callback({ type: 'text_chunk' });
  assert.equal(events, 1);
}
async function commitDoesNotWaitForLayoutOrWorkflowAndUsesWatermark() {
  const f = fixture();
  const load = f.host.enterConversation('A'); resolve(f.requests[0], payload('A')); await load;
  assert.equal(f.commits, 1);
  assert.equal(f.host.messages.length, 2);
  assert.equal(f.attachments[0][1], 257);
  assert.equal(f.microtasks.length, 1, 'layout still pending after live attach');
  assert.equal(f.requests.length, 2, 'only snapshot plus auxiliary workflow, no event replay request');
  assert.ok(f.requests[1].url.startsWith('/api/workflow/'));
  assert.equal(f.host.historyLoading, false);
}
async function staleWorkflowCannotOverwriteNewConversation() {
  const f = fixture();
  const a = f.host.enterConversation('A'); resolve(f.requests[0], payload('A')); await a;
  const workflowA = f.requests[1];
  const b = f.host.enterConversation('B'); resolve(f.requests[2], payload('B')); await b;
  resolve(workflowA, { snapshot: { owner: 'A' } }); await flush();
  assert.equal(f.workflowWrites.some(([snapshot]) => snapshot?.owner === 'A'), false);
}
async function snapshotHydratesApprovalsAndToolAliases() {
  const f = fixture();
  const registered = [], locks = [];
  f.host.toolRegisterAction = (action, alias) => registered.push(alias);
  f.host.chatSetThinkingLock = (id) => locks.push(id);
  const result = payload('A');
  result.data.display.messages[0].actions.push(
    { id: 'think', blockId: 'think', type: 'thinking', streaming: true, content: 'partial' },
    { id: 'tool-action', type: 'tool', tool: { id: 'call', executionId: 'execute', preparingId: 'prepare', name: 'read_file', arguments: {}, status: 'running' } }
  );
  result.data.display.state = { streaming: true,
    pending_tool_approvals: [{ approval_id: 'access', tool_name: 'run_command', approval_type: 'full_access', auto_review_required: true }],
    resolved_tool_approval_ids: ['old'], pending_user_questions: [{ question_id: 'q' }],
    pending_plan_approvals: [{ approval_id: 'plan' }],
    approval_review_records: [{ id: 'r', kind: 'auto', progress: [] }]
  };
  const load = f.host.enterConversation('A'); resolve(f.requests[0], result); await load;
  assert.equal(f.host.approvalPanelCollapsed, false);
  assert.equal(f.host.userQuestionDialogVisible, true);
  assert.equal(f.host.pendingPlanApprovals[0].approval_id, 'plan');
  assert.deepEqual(plain(f.host.resolvedToolApprovalIds), ['old']);
  assert.deepEqual(registered, ['tool-action', 'call', 'execute', 'prepare']);
  assert.equal(f.host.preparingTools.get('prepare').id, 'tool-action');
  assert.deepEqual(locks, ['think']);
}
async function reconcileCannotStealLoadingSession() {
  const f = fixture(); f.host.currentConversationId = 'A';
  const load = f.host.enterConversation('B');
  await f.host.refreshConversationSnapshot();
  assert.equal(f.requests.length, 1);
  resolve(f.requests[0], payload('B')); await load;
}
async function historyIdentityStableAndSameNameToolsStaySeparate() {
  const f = fixture();
  const raw = [{ role: 'user', message_id: 'u', content: 'same' },
    { role: 'assistant', message_id: 'a', content: 'text', reasoning_content: 'thought',
      tool_calls: ['one', 'two'].map((id) => ({ id, function: { name: 'read_file', arguments: '{}' } })) },
    { role: 'tool', tool_call_id: 'missing', name: 'read_file', content: 'wrong result' },
    { role: 'tool', tool_call_id: 'one', name: 'read_file', content: '{"output":"right"}' },
    { role: 'user', message_id: 'u2', content: 'same' }];
  const a = f.host.buildHistoryState(raw), b = f.host.buildHistoryState(raw);
  assert.equal(f.commits, 0, 'history conversion cannot publish partial UI');
  assert.deepEqual(plain(a.messages.map((m) => [m.id, (m.actions || []).map((x) => x.id)])),
    plain(b.messages.map((m) => [m.id, (m.actions || []).map((x) => x.id)])));
  assert.equal(a.messages.length, 3);
  const tools = a.messages[1].actions.filter((action) => action.type === 'tool');
  assert.equal(tools[0].tool.result.output, 'right');
  assert.equal(tools[1].tool.result, null);
}
(async () => {
  for (const test of [lateBootstrapCannotOverwriteNewNavigation,
    abaNavigationUsesIdentityNotConversationName, jsonParsingAndFinallyCannotChangeNewSession,
    leavingCancelsBootstrapAndEventCallbacks, commitDoesNotWaitForLayoutOrWorkflowAndUsesWatermark,
    staleWorkflowCannotOverwriteNewConversation, snapshotHydratesApprovalsAndToolAliases,
    reconcileCannotStealLoadingSession, historyIdentityStableAndSameNameToolsStaySeparate]) {
    await test(); console.log(`PASS ${test.name}`);
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
