const assert = require('node:assert/strict');
const { compile, deferred, noop, locale, pinia } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const base = 'static/src/app/methods/';
const tests = [];
const test = (name, run) => tests.push([name, run]);
const plain = value => JSON.parse(JSON.stringify(value));

function fixture(fetch = async () => ({ ok: true, json: async () => ({ success: true, data: {} }) })) {
  let session = { conversationId: 'conv_A', workspaceId: 'ws1', controller: new AbortController() };
  const goals = compile(base + 'taskPolling/goal.ts', {
    '@/locales': locale, '@/components/input/approvalModel': { updateReviewRecords: noop },
    '../message/ownership': { ensureMessageSession: () => session },
    '../conversation/session': { ownsConversationSession: (_host, owner) => owner === session }
  }, { fetch }).goalMethods;
  const host = { ...goals, currentConversationId: 'conv_A', currentHostWorkspaceId: 'ws1',
    goalProgress: { conversation_id: 'conv_A', run_id: 'goal1', goal: 'Deliver report', status: 'blocked', revision: 5 },
    goalDialogOpen: true, currentWorkMode: 'ask', currentPermissionMode: 'readonly',
    uiPushToast: noop };
  return { host, navigate() { session = { ...session }; host.currentConversationId = 'conv_B'; } };
}

test('blocked goal continuation sends ordinary user input with explicit continuation action', async () => {
  const { host } = fixture();
  let options;
  host.sendMessage = async value => { options = value; return true; };
  assert.equal(await host.handleContinueGoal(), true);
  assert.equal(options.goalAction, 'continue');
  assert.equal(options.goalOptions, undefined);
  assert.equal(host.currentWorkMode, 'execute');
  assert.equal(host.currentPermissionMode, 'auto_approval');
  assert.equal(host.goalDialogOpen, false);
});

test('start preserves existing unrestricted permission and passes optional limits', async () => {
  const { host } = fixture();
  host.currentPermissionMode = 'unrestricted';
  let options;
  host.sendMessage = async value => { options = value; return true; };
  await host.handleStartGoal({ goal: '  Build report  ', max_turns: 3 });
  assert.equal(options.presetText, 'Build report');
  assert.deepEqual(plain(options.goalOptions), { max_turns: 3, max_tokens: null });
  assert.equal(host.currentPermissionMode, 'unrestricted');
});

test('close uses control endpoint and leaves the current task running', async () => {
  const requests = [];
  const { host } = fixture(async (url, options) => {
    requests.push({ url, options });
    return { ok: true, json: async () => ({ success: true, data: {
      conversation_id: 'conv_A', run_id: 'goal1', goal: 'Deliver report', status: 'stopped', revision: 6
    } }) };
  });
  host.taskInProgress = true;
  host.cancelTask = () => { throw new Error('close must not cancel task'); };
  assert.equal(await host.handleCloseGoal(), true);
  assert.equal(host.taskInProgress, true);
  assert.equal(requests[0].url, '/api/conversations/conv_A/goal');
  assert.deepEqual(JSON.parse(requests[0].options.body), { action: 'close', run_id: 'goal1' });
  assert.equal(host.goalProgress.status, 'stopped');
});

test('late start response cannot close another conversation dialog or overwrite its modes', async () => {
  const f = fixture();
  const pending = deferred();
  f.host.sendMessage = () => pending.promise;
  const start = f.host.handleStartGoal({ goal: 'A' });
  f.navigate();
  pending.resolve(true);
  assert.equal(await start, false);
  assert.equal(f.host.currentWorkMode, 'ask');
  assert.equal(f.host.goalDialogOpen, true);
});

test('late close response cannot overwrite another conversation goal', async () => {
  const response = deferred();
  const f = fixture(() => response.promise);
  const close = f.host.handleCloseGoal();
  f.navigate();
  response.resolve({ ok: true, json: async () => ({ success: true, data: {
    conversation_id: 'conv_A', status: 'stopped', revision: 100
  } }) });
  assert.equal(await close, false);
  assert.equal(f.host.goalProgress.status, 'blocked');
});

test('stale revisions and other workspace events are rejected', () => {
  const { host } = fixture();
  host.handleGoalProgress({ conversation_id: 'conv_A', revision: 4, status: 'running' });
  host.handleGoalProgress({ conversation_id: 'conv_A', workspace_id: 'ws2', revision: 9, status: 'running' });
  host.handleGoalProgress({ conversation_id: 'conv_B', revision: 9, status: 'running' });
  assert.equal(host.goalProgress.status, 'blocked');
  host.handleGoalProgress({ conversation_id: 'conv_A', revision: 6, status: 'retrying' });
  assert.equal(host.goalRunning, true);
});

test('duplicate start clicks do not create duplicate submissions', async () => {
  const { host } = fixture();
  const response = deferred();
  let count = 0;
  host.sendMessage = () => { count++; return response.promise; };
  const first = host.handleStartGoal({ goal: 'A' });
  assert.equal(await host.handleStartGoal({ goal: 'A' }), false);
  response.resolve(false);
  await first;
  assert.equal(count, 1);
  host.sendMessage = async () => true;
  assert.equal(await host.handleStartGoal({ goal: 'A' }), true);
});

test('goal alone makes QuickDock visible even without other windows', () => {
  const input = { goalProgress: { goal: 'A', status: 'paused' } };
  const stores = compile('static/src/stores/quickDock.ts', {
    pinia, './file': { useFileStore: () => ({}) },
    './subAgent': { useSubAgentStore: () => ({ subAgents: [] }) },
    './backgroundCommand': { useBackgroundCommandStore: () => ({ commands: [] }) },
    './workflow': { useWorkflowStore: () => ({ isActive: false, exiting: false }) },
    './preview': { usePreviewStore: () => ({ targets: [] }) },
    './input': { useInputStore: () => input }
  });
  const dock = stores.useQuickDockStore();
  assert.equal(dock.hasContent, true);
  input.goalProgress.status = 'stopped';
  assert.equal(dock.hasContent, false);
});

for (const multi of [false, true]) {
  test(`first goal send creates exactly one ${multi ? 'multi-agent' : 'normal'} conversation with execute/automatic modes`, async () => {
    const { fixture: navigationFixture, flush, respond } = require('../2026-10-12_工作区切换重构/navigation_fixture.cjs');
    const f = navigationFixture();
    const helper = compile(base + 'ui/newConversation.ts', {
      '@/locales': locale, '../conversation/session': f.session
    }, { fetch: f.fetch });
    const options = [];
    const conversationStore = { conversationsCache: {
      normal: { list: [], loaded: true }, multi_agent: { list: [], loaded: true }
    }, workspaceGroups: [], $patch: noop, setSidebarConversationType: noop };
    const send = compile(base + 'message/send.ts', {
      '../common': { debugLog: noop }, '@/locales': locale,
      '../../../stores/model': { useModelStore: () => ({ models: [] }) },
      '../../../stores/personalization': { usePersonalizationStore: () => ({ form: {} }) },
      '../../../stores/preview': { usePreviewStore: () => ({ resetAutoOpen: noop }) },
      './shared': { extractSkillRefsFromMessage: () => [] },
      './ownership': {
        ensureMessageSession: host => f.session.currentConversationSession(host),
        createOwnedMessageTask: async (...args) => { options.push(args[7]); return { task_id: 'goal-task' }; }
      },
      './controls': { messageControlMethods: {} }, '../conversation/session': f.session,
      '../ui/newConversation': helper,
      '../../../stores/conversation': { useConversationStore: () => conversationStore },
      '../../../stores/conversationTabs': { useConversationTabsStore: () => ({ enabled: false }) }
    }, { fetch: f.fetch, history: { replaceState: noop } }).sendMethods;
    Object.assign(f.host, send, { currentConversationId: null, historyLoading: false,
      hostWorkspaceSwitching: false, newConversationSettingsReady: true,
      newConversationType: multi ? 'multi_agent' : 'agent', inputMessage: 'keep this draft',
      isConnected: true, isQuotaExceeded: () => false, isConversationBlank: () => true,
      currentModelKey: 'model', runMode: 'fast', thinkingMode: false, reasoningEffort: null,
      currentWorkMode: 'plan', currentPermissionMode: 'readonly', currentExecutionMode: 'sandbox',
      currentNetworkPermission: 'restricted', newConversationRestoreModes: {},
      chatAddUserMessage: () => ({}), chatStartAssistantMessage: noop,
      stripConversationPrefix: id => id, uiDismissToast: noop });
    f.session.beginConversationSession(f.host, '', 'ws1');
    const pending = f.host.sendMessage({ presetText: 'Deliver report', goalAction: 'start',
      goalOptions: { max_turns: null, max_tokens: null } });
    await flush();
    assert.equal(f.requests.length, 1);
    const body = JSON.parse(f.requests[0].options.body);
    assert.equal(body.settings.work_mode, 'execute');
    assert.equal(body.settings.permission_mode, 'auto_approval');
    assert.equal(body.settings.execution_mode, 'sandbox');
    respond(f.requests[0], { success: true, conversation_id: 'conv_NEW' });
    assert.equal(await pending, true);
    assert.equal(f.host.currentConversationId, 'conv_NEW');
    assert.equal(options[0].goal_action, 'start');
    assert.equal(options[0].message_source, 'user');
    assert.equal(f.host.inputMessage, 'keep this draft');
    assert.equal(f.requests.length, 1);
  });
}

(async () => {
  for (const [name, run] of tests) { await run(); console.log('PASS', name); }
  console.log(`Completed ${tests.length} goal frontend regressions.`);
})().catch(error => { console.error(error); process.exitCode = 1; });
