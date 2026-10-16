const assert = require('node:assert/strict');
const { compile, noop, locale } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const { fixture, flush, drain, respond } = require('../2026-10-12_工作区切换重构/navigation_fixture.cjs');
const base = 'static/src/app/methods/';
const tests = [];
const test = (name, run) => tests.push([name, run]);
const plain = value => JSON.parse(JSON.stringify(value));

test('top, workspace-group and browser-back entries use the common new-page navigation', async () => {
  const navigations = [];
  const methods = compile(base + 'conversation/action.ts', {
    '../common': {}, '@/locales': locale, '../../state': { persistNewConversationType: noop },
    './shared': {}, './session': {},
    '../../../stores/conversation': { useConversationStore: () => ({ sidebarConversationType: 'multi_agent' }) },
    '../../../stores/conversationTabs': { useConversationTabsStore: () => ({ enabled: false }) }
  }).actionMethods;
  const host = { ...methods, currentHostWorkspaceId: 'ws1',
    enterNewConversationPage: async options => navigations.push(options) };
  await host.createNewConversation(); await host.createWorkspaceConversation('ws2');
  assert.deepEqual(plain(navigations), [{ workspaceId: 'ws1' }, { workspaceId: 'ws2' }]);
  assert.equal(host.newConversationType, 'multi_agent');
  const route = compile(base + 'ui/route.ts', { '@/locales': locale,
    '../conversation/session': {}, './workspaceStartup': {},
    '../../../stores/conversationTabs': {} },
    { URLSearchParams, window: { location: { search: '?workspace_id=ws3' } } });
  await route.routeMethods.handlePopState.call(host, { state: {} });
  assert.equal(navigations[2].workspaceId, 'ws3');
});

for (const workspace of ['ws1', 'ws2']) {
  test(`new entry in ${workspace} uses only the explicitly departed same-workspace view`, async () => {
    const f = fixture();
    const pending = f.host.handleHostWorkspaceSwitch(workspace, { newConversation: true });
    await drain(f, { workspaceView: { active_conversation_id: 'running' } });
    await pending;
    const request = f.requests.find(r => r.url === '/api/new-conversation/settings');
    assert.equal(JSON.parse(request.options.body).previous_conversation_id, workspace === 'ws1' ? 'A' : null);
    assert.equal(f.host.currentHostWorkspaceId, workspace);
    assert.equal(f.host.currentConversationId, null);
    assert.equal(f.host.newConversationSettingsReady, true);
    assert.equal(f.host.historyLoading, false);
    assert.equal(f.requests.some(r => /\/bootstrap|^\/api\/(multiagent\/)?conversations$/.test(r.url)), false);
  });
}

test('late new-page preparation cannot overwrite a newer navigation', async () => {
  const f = fixture();
  const first = f.host.handleHostWorkspaceSwitch('ws2', { newConversation: true });
  await flush();
  const old = f.requests.find(r => r.url === '/api/new-conversation/settings');
  const next = f.host.handleHostWorkspaceSwitch('ws3', { newConversation: true });
  await drain(f);
  await Promise.all([first, next]);
  respond(old, { success: true, data: { settings: { work_mode: 'plan' } } });
  await flush();
  assert.equal(f.host.currentHostWorkspaceId, 'ws3');
  assert.equal(f.host.currentWorkMode, 'execute');
});

test('new-page local modes preserve null and restore permission/execution across plan', async () => {
  const f = fixture();
  f.host.currentConversationId = null;
  f.host.currentWorkMode = 'ask'; f.host.currentPermissionMode = 'unrestricted';
  f.host.currentExecutionMode = 'direct'; f.host.currentNetworkPermission = 'restricted';
  f.host.setNewConversationMode('work', 'plan');
  assert.equal(f.host.currentPermissionMode, 'readonly');
  assert.equal(f.host.currentExecutionMode, 'sandbox');
  f.host.setNewConversationMode('execution', 'direct');
  assert.equal(f.host.currentExecutionMode, 'sandbox');
  f.host.setNewConversationMode('work', 'execute');
  assert.equal(f.host.currentPermissionMode, 'unrestricted');
  assert.equal(f.host.currentExecutionMode, 'direct');
  f.host.setNewConversationMode('permission', 'approval');
  assert.equal(f.host.currentExecutionMode, 'sandbox');
  f.host.setNewConversationMode('permission', 'unrestricted');
  assert.equal(f.host.currentExecutionMode, 'direct');
  assert.equal(f.requests.length, 0);
});

test('mode controls on /new never call legacy settings endpoints', async () => {
  const f = fixture(); f.host.currentConversationId = null;
  const imports = { '@/locales': locale,
    '../auxiliaryOwnership': { beginAuxiliaryRequest: () => () => true },
    '@/components/input/approvalModel': {}, '../../../stores/policy': {},
    '../../../stores/personalization': {} };
  Object.assign(f.host,
    compile(base + 'ui/workMode.ts', imports, { fetch: f.fetch }).workModeMethods,
    compile(base + 'ui/permission.ts', imports, { fetch: f.fetch }).permissionMethods,
    { closeWorkModeMenu: noop, closePermissionMenu: noop,
      executionModeEnabled: true, networkPermissionEnabled: true });
  await f.host.changeWorkMode('execute');
  await f.host.changePermissionMode('unrestricted');
  await f.host.changeExecutionMode('direct');
  await f.host.changeNetworkPermission('full');
  for (const method of ['fetchWorkMode', 'fetchPermissionMode', 'fetchExecutionMode', 'fetchNetworkPermission'])
    await f.host[method]();
  assert.equal(f.requests.length, 0);
  assert.equal(f.host.currentWorkMode, 'execute');
  assert.equal(f.host.currentExecutionMode, 'direct');
  assert.equal(f.host.currentNetworkPermission, 'full');
});

test('status polling cannot replace /new selections', () => {
  const resource = compile(base + 'resources.ts', {
    '@/locales': locale, '../../utils/formatters': {},
    '@/utils/downloadFile': {},
  });
  const host = { currentConversationId: null, currentWorkMode: 'execute',
    currentPermissionMode: 'unrestricted', currentExecutionMode: 'direct',
    currentNetworkPermission: 'full', reasoningEffort: null,
    resourceApplyStatusSnapshot: noop, isExplicitNewConversationRoute: () => true };
  resource.resourceMethods.applyStatusSnapshot.call(host, {
    conversation: {}, work_mode: 'plan', permission_mode: 'readonly',
    execution_mode: { mode: 'sandbox' }, network_permission: 'restricted', reasoning_effort: 'high'
  });
  assert.equal(host.currentWorkMode, 'execute'); assert.equal(host.currentExecutionMode, 'direct');
  assert.equal(host.reasoningEffort, null);
});

async function sendFixture(multi) {
  const f = fixture();
  const helper = compile(base + 'ui/newConversation.ts', {
    '@/locales': locale, '../conversation/session': f.session,
  }, { fetch: f.fetch });
  const taskOptions = [];
  const conversationStore = { multiAgentMode: false, conversationsCache: {
    normal: { list: [], loaded: true }, multi_agent: { list: [], loaded: true }
  }, workspaceGroups: [], setSidebarConversationType: noop, $patch: noop };
  const send = compile(base + 'message/send.ts', {
    '../common': { debugLog: noop }, '@/locales': locale,
    '../../../stores/model': { useModelStore: () => ({ models: [] }) },
    '../../../stores/personalization': { usePersonalizationStore: () => ({ form: {} }) },
    './shared': { extractSkillRefsFromMessage: () => [] },
    '../../../stores/preview': { usePreviewStore: () => ({ resetAutoOpen: noop }) },
    './ownership': { ensureMessageSession: host => f.session.currentConversationSession(host),
      createOwnedMessageTask: async (...args) => { taskOptions.push(args[7]); return null; } },
    './controls': { messageControlMethods: {} }, '../conversation/session': f.session,
    '../ui/newConversation': helper,
    '../../../stores/conversation': { useConversationStore: () => conversationStore },
    '../../../stores/conversationTabs': { useConversationTabsStore: () => ({ enabled: false }) }
  }, { fetch: f.fetch, history: { replaceState: noop } }).sendMethods;
  Object.assign(f.host, send, { currentConversationId: null, historyLoading: false,
    hostWorkspaceSwitching: false, newConversationSettingsReady: true, newConversationType: multi ? 'multi_agent' : 'agent',
    inputMessage: 'hello', isConnected: true, isQuotaExceeded: () => false,
    getInputComposerRef: () => null, isConversationBlank: () => true,
    currentModelKey: 'chosen', runMode: 'thinking', thinkingMode: true, reasoningEffort: null,
    currentWorkMode: 'execute', currentPermissionMode: 'unrestricted',
    currentExecutionMode: 'direct', currentNetworkPermission: 'full', newConversationRestoreModes: {},
    chatAddUserMessage: () => ({}), chatStartAssistantMessage() { this.messages.push({ role: 'assistant' }); },
    stripConversationPrefix: id => id, uiDismissToast: noop });
  f.session.beginConversationSession(f.host, '', 'ws1');
  return { ...f, taskOptions };
}
for (const multi of [false, true]) {
  test(`${multi ? 'multi-agent' : 'normal'} first send persists the complete frontend snapshot, including null`, async () => {
    const f = await sendFixture(multi);
    const pending = f.host.sendMessage(); await flush();
    const request = f.requests[0];
    assert.equal(request.url, multi ? '/api/multiagent/conversations' : '/api/conversations');
    const body = JSON.parse(request.options.body);
    assert.deepEqual(body.settings, { model_key: 'chosen', run_mode: 'thinking', thinking_mode: true,
      reasoning_effort: null, work_mode: 'execute', permission_mode: 'unrestricted',
      execution_mode: 'direct', network_permission: 'full', restore_modes: {} });
    f.host.runMode = 'fast'; f.host.currentModelKey = 'changed-during-request';
    respond(request, { success: true, conversation_id: 'new-id' });
    await pending;
    assert.equal(f.taskOptions[0].model_key, 'chosen');
    assert.equal(f.taskOptions[0].run_mode, 'thinking');
  });
}

test('send before preparation or during navigation cannot allocate a conversation', async () => {
  const f = await sendFixture(false);
  f.host.newConversationSettingsReady = false;
  assert.equal(await f.host.sendMessage(), false);
  f.host.newConversationSettingsReady = true; f.host.hostWorkspaceSwitching = true;
  assert.equal(await f.host.sendMessage(), false);
  assert.equal(f.requests.length, 0);
});

test('late model, thinking and effort responses cannot overwrite the new page', async () => {
  const f = fixture();
  const auxiliary = compile(base + 'auxiliaryOwnership.ts', {
    '../../stores/conversation': { useConversationStore: () => f.store },
    './conversation/session': f.session,
  }, { fetch: f.fetch });
  const modelStore = { currentModelKey: 'old', models: [{ key: 'new' }],
    setModel(key) { this.currentModelKey = key; } };
  const imports = { '@/locales': locale, '../auxiliaryOwnership': auxiliary,
    '../../state': { persistNewConversationType: noop },
    '../../../stores/model': { useModelStore: () => modelStore },
    '../../../stores/policy': { usePolicyStore: () => ({ isModelDisabled: () => false }) } };
  Object.assign(f.host, compile(base + 'ui/model.ts', imports, { fetch: f.fetch }).modelMethods,
    compile(base + 'ui/mode.ts', imports, { fetch: f.fetch }).modeMethods,
    { isConnected: true, currentModelKey: 'old', resolvedRunMode: 'fast', runMode: 'fast',
      reasoningEffort: null, inputCloseMenus: noop, inputSetQuickMenuOpen: noop });
  const model = f.host.handleModelSelect('new');
  const thinking = f.host.setRunMode('thinking');
  await f.host.setReasoningEffort('high');
  const effort = f.host.flushReasoningEffortSave();
  await flush();
  assert.equal(f.requests.length, 3);
  f.host.beginConversationView('', 'ws1'); f.host.currentConversationId = null;
  f.host.runMode = 'fast'; f.host.reasoningEffort = null;
  modelStore.currentModelKey = 'new-page-model';
  for (const request of f.requests)
    respond(request, { success: true, data: { model_key: 'late', mode: 'thinking', reasoning_effort: 'high' } });
  await Promise.all([model, thinking, effort]);
  assert.equal(modelStore.currentModelKey, 'new-page-model');
  assert.equal(f.host.runMode, 'fast'); assert.equal(f.host.reasoningEffort, null);
});

(async () => {
  for (const [name, run] of tests) { await run(); console.log('PASS ' + name); }
  console.log(`Completed ${tests.length} new-conversation frontend regressions.`);
})().catch(error => { console.error(error); process.exitCode = 1; });
