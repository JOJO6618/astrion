const assert = require('node:assert/strict');
const { compile, deferred, noop, pinia, locale } = require('./ts_fixture.cjs');
const base = 'static/src/app/methods/';
function fixture() {
  const requests = [];
  const fetch = (url) => { const req = deferred(); requests.push({ url, ...req }); return req.promise; };
  const session = compile(`${base}conversation/session.ts`);
  const conversation = { currentConversationId: 'A' };
  const owner = compile(`${base}auxiliaryOwnership.ts`, {
    '../../stores/conversation': { useConversationStore: () => conversation }, './conversation/session': session
  });
  const host = { currentConversationId: 'A', currentHostWorkspaceId: 'ws' };
  session.beginConversationSession(host, 'A', 'ws'); owner.bindAuxiliaryConversationHost(host);
  const storeImports = { pinia, '@/locales': locale, './conversation': { useConversationStore: () => conversation },
    '../app/methods/auxiliaryOwnership': owner };
  const file = compile('static/src/stores/file.ts', storeImports, { fetch }).useFileStore();
  const resource = compile('static/src/stores/resource.ts', storeImports, { fetch }).useResourceStore();
  const sub = compile('static/src/stores/subAgent.ts', storeImports, { fetch }).useSubAgentStore();
  const dialog = compile(`${base}ui/dialog.ts`, {
    '../auxiliaryOwnership': owner, '@/locales': locale,
    '../../../../components/input/approvalModel': {},
    '@/components/input/approvalModel': {}
  }, { fetch }).dialogMethods;
  const approvals = compile(`${base}taskPolling/approvals.ts`, {
    '@/locales': locale, '../auxiliaryOwnership': owner,
    '../../../stores/personalization': {}, '@/components/input/approvalModel': {}
  }).approvalMethods;
  const tools = compile(`${base}taskPolling/tool.ts`, {
    './approvals': { approvalMethods: approvals },
    '../common': { debugLog: noop }, '../auxiliaryOwnership': owner,
    '../../../stores/personalization': {}, '@/locales': locale, './aiStream': {},
    '../conversation/session': session, '@/components/input/approvalModel': {}
  }).toolMethods;
  Object.assign(host, dialog, tools, { pendingUserQuestions: [], pendingPlanApprovals: [],
    answeringUserQuestionIds: [], userQuestionActiveIndex: 0, $forceUpdate: noop,
    restoreUserQuestionTitle: noop, notifyUserQuestion: noop });
  return { host, session, owner, conversation, requests, file, resource, sub };
}
function respond(request, body) { request.resolve({ ok: true, json: async () => body }); }
const tests = [];
function test(name, fn) { tests.push([name, fn]); }
test('view epoch rejects A to B to A responses', () => {
  const f = fixture(); const owns = f.owner.beginAuxiliaryRequest(f.host, 'q');
  f.session.beginConversationSession(f.host, 'B', 'ws');
  f.session.beginConversationSession(f.host, 'A', 'ws');
  assert.equal(owns(), false);
});
test('empty to conversation to empty also invalidates old requests', () => {
  const f = fixture(); f.session.leaveConversationSession(f.host); f.host.currentConversationId = null;
  const owns = f.owner.captureConversationView(f.host);
  f.session.beginConversationSession(f.host, 'B', 'ws'); f.session.leaveConversationSession(f.host);
  assert.equal(owns(), false);
});
test('request sequence rejects older requests within the same view', () => {
  const f = fixture(); const first = f.owner.beginAuxiliaryRequest(f.host, 'q');
  const second = f.owner.beginAuxiliaryRequest(f.host, 'q');
  assert.equal(first(), false); assert.equal(second(), true);
});
test('live todo prevents an older GET from overwriting it', async () => {
  const f = fixture(); const pending = f.file.fetchTodoList();
  f.file.setTodoList({ instruction: 'live', tasks: [] }, true);
  respond(f.requests[0], { success: true, data: { instruction: 'stale', tasks: [] } }); await pending;
  assert.equal(f.file.todoList.instruction, 'live'); assert.equal(f.file.todoListLive, true);
});
test('live context tokens win over an older GET', async () => {
  const f = fixture(); const pending = f.resource.updateCurrentContextTokens('A');
  f.resource.setCurrentContextTokens(123);
  respond(f.requests[0], { success: true, data: { total_tokens: 1 } }); await pending;
  assert.equal(f.resource.currentContextTokens, 123);
});
test('closing activity rejects old entries, errors and loading cleanup', async () => {
  const f = fixture(); f.sub.activeAgent = { task_id: 'S' };
  const pending = f.sub.fetchSubAgentActivity('S');
  f.sub.closeSubAgent();
  respond(f.requests[0], { success: true, data: { entries: [{ id: 'old' }], status: 'running' } }); await pending;
  assert.equal(f.sub.activityEntries.length, 0); assert.equal(f.sub.activityLoading, false);
});
test('old activity failure cannot clear newer loading state', async () => {
  const f = fixture(); const first = f.sub.fetchSubAgentActivity('S1');
  const second = f.sub.fetchSubAgentActivity('S2');
  f.requests[0].reject(new Error('late failure')); await first;
  assert.equal(f.sub.activityLoading, true); assert.equal(f.sub.activityError, null);
  respond(f.requests[1], { success: true, data: { entries: [{ id: 'new' }], status: 'running' } }); await second;
  assert.equal(f.sub.activityEntries[0].id, 'new'); assert.equal(f.sub.activityLoading, false);
});
test('resolved question cannot be resurrected by old pending GET', async () => {
  const f = fixture(); const pending = f.host.fetchPendingUserQuestions();
  f.host.pendingUserQuestions = [{ question_id: 'Q' }];
  f.host.handleUserQuestionsResolved({ question_ids: ['Q'] });
  respond(f.requests[0], { success: true, data: { questions: [{ question_id: 'Q' }] } }); await pending;
  assert.equal(f.host.pendingUserQuestions.length, 0); assert.equal(f.host.userQuestionDialogVisible, false);
});
test('resolved plan cannot be resurrected by old pending GET', async () => {
  const f = fixture(); f.host.fetchWorkMode = noop; f.host.fetchPermissionMode = noop;
  f.host.fetchExecutionMode = noop; f.host.fetchNetworkPermission = noop;
  const pending = f.host.fetchPendingPlanApprovals();
  f.host.pendingPlanApprovals = [{ approval_id: 'P' }];
  f.host.handlePlanApprovalResolved({ approval_id: 'P' });
  respond(f.requests[0], { success: true, data: { approvals: [{ approval_id: 'P' }] } }); await pending;
  assert.equal(f.host.pendingPlanApprovals.length, 0);
});
test('composer answer cannot reopen dialog after navigating away', async () => {
  const f = fixture(); const answer = deferred(); f.host.pendingUserQuestions = [{ question_id: 'Q' }];
  f.host.submitUserQuestionAnswers = () => answer.promise;
  const pending = f.host.answerUserQuestionFromComposer('answer');
  f.session.beginConversationSession(f.host, 'B', 'ws'); f.host.userQuestionDialogVisible = false;
  answer.resolve(); assert.equal(await pending, false); assert.equal(f.host.userQuestionDialogVisible, false);
});
(async () => { for (const [name, fn] of tests) { await fn(); console.log(`PASS ${name}`); } })()
  .catch((error) => { console.error(error); process.exitCode = 1; });
