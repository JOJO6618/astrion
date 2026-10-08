const assert = require('node:assert/strict');
const { compile, deferred, noop, locale } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const base = 'static/src/app/methods/';

function fixture() {
  const session = compile(`${base}conversation/session.ts`);
  const requests = [];
  const task = { isPolling: false, currentTaskId: null, clearTask: noop, attachSnapshot: noop };
  const fetch = (url) => {
    const request = deferred();
    requests.push({ url, ...request });
    return request.promise;
  };
  const bootstrap = compile(`${base}conversation/bootstrap.ts`, {
    './session': session, '../common': { debugLog: noop }, './display': {},
    '../ui/shared': {}, '../auxiliaryOwnership': { bindAuxiliaryConversationHost: noop },
    '../../../stores/task': { useTaskStore: () => task },
    '../../../stores/quickDock': { useQuickDockStore: () => ({ setEditedFiles: noop }) },
    '../../../stores/preview': { usePreviewStore: () => ({ setRuntime: noop, setTargets: noop }) },
    '../../../stores/conversation': { useConversationStore: () => ({ $patch: noop }) },
    '../../../stores/workflow': { useWorkflowStore: () => ({ setWorkflow: noop }) },
    '../../../stores/conversationTabs': { useConversationTabsStore: () => ({ enabled: false }) },
    '../../../stores/personalization': { usePersonalizationStore: () => ({ form: {} }) },
    '@/components/input/approvalModel': {}
  }, { fetch }).bootstrapMethods;
  const probe = compile(`${base}taskPolling/probe.ts`, {
    '../conversation/session': session, '../common': { debugLog: noop },
    '../../../stores/task': { useTaskStore: () => task }
  }, { fetch }).probeMethods;
  const host = {
    ...bootstrap, ...probe, currentConversationId: 'A', currentHostWorkspaceId: 'ws',
    historyLoading: false, taskInProgress: true, streamingMessage: true,
    messages: [{ role: 'user', content: 'optimistic' }], preparingTools: new Map(),
    pendingToolApprovals: [], cloneToolArguments: (x) => x, buildToolLabel: () => '',
    toolRegisterAction: noop, toolTrackAction: noop, restoreUserQuestionTitle: noop,
    buildHistoryState: () => ({ messages: [], hasImages: false, hasVideos: false }),
    clearLocalTaskUiState: noop, modelSet: noop, startTitleTyping: noop,
    applyRuntimeQueuedMessages: noop, handleCompressionState: noop, promoteConversationToTop: noop,
    refreshBlankHeroState: noop, $nextTick: noop, fetchConversationWorkflow: noop
  };
  const owner = session.beginConversationSession(host, 'A', 'ws');
  return { host, owner, session, requests, task };
}
const respond = (request, data) => request.resolve({ ok: true, json: async () => data });

async function submissionPausesReconcile() {
  const f = fixture();
  const token = f.session.beginConversationSubmission(f.owner);
  await f.host.reconcileRunningStateOnce();
  await f.host.refreshConversationSnapshot();
  assert.equal(f.requests.length, 0);
  assert.equal(f.session.ownsConversationSession(f.host, f.owner), true);
  assert.equal(f.session.beginConversationSubmission(f.owner), null);
  f.session.endConversationSubmission(f.owner, token);
  assert.equal(f.owner.submission, null);
}
async function lateProbeCannotRefreshAfterSubmission() {
  const f = fixture();
  const probe = f.host.reconcileRunningStateOnce();
  const token = f.session.beginConversationSubmission(f.owner);
  f.session.endConversationSubmission(f.owner, token);
  respond(f.requests[0], { success: true, data: { is_main_running: false, is_truly_active: false } });
  await probe;
  assert.equal(f.requests.length, 1);
  assert.equal(f.session.ownsConversationSession(f.host, f.owner), true);
}
async function lateSnapshotCannotOverwriteSubmission() {
  const f = fixture();
  f.task.isPolling = true;
  f.task.currentTaskId = 'old-task';
  const snapshot = f.host.refreshConversationSnapshot();
  assert.equal(f.task.currentTaskId, 'old-task');
  assert.equal(f.session.ownsConversationSession(f.host, f.owner), true);
  const token = f.session.beginConversationSubmission(f.owner);
  f.session.endConversationSubmission(f.owner, token);
  respond(f.requests[0], { success: true, data: {
    conversation_id: 'A', meta: {}, messages: [], display: { messages: [] }, running: {}
  } });
  assert.equal((await snapshot).superseded, true);
  assert.equal(f.host.messages[0].content, 'optimistic');
  assert.equal(f.host.historyLoading, false);
  assert.equal(f.task.currentTaskId, 'old-task');
}
async function completedToolAliasDoesNotKeepComposerBusy() {
  const f = fixture();
  const action = { type: 'tool', tool: {
    id: 'finished-tool', name: 'read_file', status: 'completed', preparingId: 'preparing-alias'
  } };
  const snapshot = { state: {}, messages: [{ role: 'assistant', actions: [action] }] };
  f.host.hydrateRuntimeDisplay(snapshot, { is_truly_active: false, is_main_running: false });
  assert.equal(f.host.preparingTools.size, 0);
  assert.equal(f.host.streamingMessage, false);
  assert.equal(f.host.taskInProgress, false);
  action.tool.status = 'preparing';
  f.host.hydrateRuntimeDisplay(snapshot, { is_truly_active: true, is_main_running: true });
  assert.equal(f.host.preparingTools.get('preparing-alias'), action);
}
async function newViewSendsInsteadOfQueueingIntoOldTask() {
  const f = fixture();
  f.session.leaveConversationSession(f.host);
  f.host.currentConversationId = null;
  let sent = 0, queued = 0;
  const controls = compile(`${base}message/controls.ts`, {
    '../common': { goalModeDebugLog: noop }, '@/locales': locale,
    '../../../stores/task': { useTaskStore: () => f.task },
    './ownership': {}, '../conversation/session': f.session
  }).messageControlMethods;
  Object.assign(f.host, {
    ...controls, inputMessage: 'new message', composerBusy: true, mainChatIdle: false,
    clearLocalTaskUiState: () => { f.host.composerBusy = false; },
    sendMessage: () => { sent++; }, enqueueRuntimeQueuedMessage: () => { queued++; }
  });
  await f.host.handleSendOrStop();
  assert.equal(sent, 1);
  assert.equal(queued, 0);
}
(async () => {
  for (const test of [submissionPausesReconcile, lateProbeCannotRefreshAfterSubmission,
    lateSnapshotCannotOverwriteSubmission, completedToolAliasDoesNotKeepComposerBusy,
    newViewSendsInsteadOfQueueingIntoOldTask]) {
    await test();
    console.log(`PASS ${test.name}`);
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
