const assert = require('node:assert/strict');
const { compile, deferred, noop, locale } = require('./ts_fixture.cjs');
const base = 'static/src/app/methods/';
const flush = async () => { for (let n = 0; n < 12; n++) await Promise.resolve(); };
function fixture() {
  const requests = [], ticks = [], conversation = { currentConversationId: 'A' };
  const session = compile(`${base}conversation/session.ts`);
  const owner = compile(`${base}auxiliaryOwnership.ts`, {
    '../../stores/conversation': { useConversationStore: () => conversation }, './conversation/session': session
  });
  const window = { location: { pathname: '/new' }, setTimeout: noop, setInterval: noop };
  const fetch = (url) => { const req = deferred(); requests.push({ url, ...req }); return req.promise; };
  const tabs = { enabled: false };
  const imports = { '@/locales': locale, '../auxiliaryOwnership': owner,
    '../conversation/session': session, '../../../stores/conversation': { useConversationStore: () => conversation },
    '../../../stores/conversationTabs': { useConversationTabsStore: () => tabs },
    '../common': { debugLog: noop }, '../../state': { persistWorkspaceMode: noop },
    '../../../stores/policy': {}, '../../../stores/model': {}, '../../../stores/personalization': {},
    './shared': {} };
  const globals = { fetch, window, history: { replaceState: noop } };
  const host = {
    currentConversationId: 'A', currentHostWorkspaceId: 'ws', versioningHostMode: true,
    dockerProjectMode: false, runningWorkspaceTasks: [], hostWorkspaces: [],
    getStoredWorkspaceTaskIdSet: () => new Set(), setStoredWorkspaceTaskIdSet: noop,
    refreshRunningWorkspaceTasks: noop, isExplicitNewConversationRoute: () => true,
    isConversationIndependentRoute: () => false, initialRouteResolved: false,
    applyStatusSnapshot: noop, restoreComposerDraftState: noop, refreshBlankHeroState: noop,
    startTitleTyping: noop, logMessageState: noop, conversationsOffset: 0,
    $nextTick: (fn) => ticks.push(fn), inputMessage: 'draft', composerDraftLastSyncedContent: '',
    getInputComposerRef: () => ({ restoreComposerDraftMeta: noop }), inputSetMessage: noop,
    autoResizeInput: noop, $refs: {}
  };
  host.beginConversationView = (id, workspace) => session.beginConversationSession(host, id, workspace);
  host.leaveConversationView = () => session.leaveConversationSession(host);
  host.beginConversationView('A', 'ws'); owner.bindAuxiliaryConversationHost(host);
  Object.assign(host,
    compile(`${base}ui/socket.ts`, imports, globals).socketMethods,
    compile(`${base}ui/hostWorkspace.ts`, imports, globals).hostWorkspaceMethods,
    compile(`${base}ui/workspace.ts`, imports, globals).workspaceMethods,
    compile(`${base}ui/composer.ts`, imports, globals).composerMethods,
    compile(`${base}ui/route.ts`, imports, globals).routeMethods
  );
  return { host, session, owner, requests, ticks, tabs, window };
}
function respond(request, body) { request.resolve({ ok: true, json: async () => body }); }
const tests = [];
function test(name, fn) { tests.push([name, fn]); }
test('blank route with late tab hydration cannot overwrite empty-view ABA', async () => {
  const f = fixture(); const tabLoading = deferred(); f.tabs.enabled = true; f.tabs.hydrate = () => tabLoading.promise;
  const pending = f.host.bootstrapRoute(); await flush();
  f.host.beginConversationView('B', 'ws'); f.host.leaveConversationView();
  f.host.currentConversationId = null; f.host.currentConversationTitle = 'latest blank';
  tabLoading.resolve(); assert.equal((await pending).superseded, true);
  assert.equal(f.host.currentConversationTitle, 'latest blank');
});
test('late idle status cannot apply after empty-view ABA', async () => {
  const f = fixture(); f.host.leaveConversationView(); f.host.currentConversationId = null;
  let writes = 0; f.host.applyStatusSnapshot = () => writes++;
  const pending = f.host.fetchStatusSnapshot();
  f.host.beginConversationView('B', 'ws'); f.host.leaveConversationView();
  respond(f.requests[0], { run_mode: 'fast' }); await pending;
  assert.equal(writes, 0);
});
test('idle status preserves the opened conversation mode', async () => {
  const f = fixture(); f.host.runMode = 'thinking'; f.host.isExplicitNewConversationRoute = () => false;
  const pending = f.host.fetchStatusSnapshot(); respond(f.requests[0], { run_mode: 'fast' }); await pending;
  assert.equal(f.host.runMode, 'thinking');
});
test('out-of-order workspace snapshots keep the latest selected workspace', async () => {
  const f = fixture(); f.host.refreshRunningWorkspaceTasks = noop;
  const old = f.host.fetchHostWorkspaces(); const latest = f.host.fetchHostWorkspaces();
  respond(f.requests[1], { success: true, data: { workspaces: [], current_workspace_id: 'latest' } }); await latest;
  respond(f.requests[0], { success: true, data: { workspaces: [], current_workspace_id: 'old' } }); await old;
  assert.equal(f.host.currentHostWorkspaceId, 'latest');
});
test('old workspace task failure cannot clear the replacement list', async () => {
  const f = fixture(); const old = f.host.refreshRunningWorkspaceTasks();
  f.host.beginConversationView('B', 'other'); f.host.currentHostWorkspaceId = 'other';
  f.host.currentConversationId = 'B';
  const latest = f.host.refreshRunningWorkspaceTasks();
  respond(f.requests[1], { success: true, data: [{ task_id: 'new', status: 'running', workspace_id: 'other' }] }); await latest;
  f.requests[0].reject(new Error('old error')); await old;
  assert.equal(f.host.runningWorkspaceTasks[0].task_id, 'new');
});
test('late draft GET cannot write the new view or its scheduled editor metadata', async () => {
  const f = fixture(); let text = '', metadataWrites = 0;
  f.host.inputSetMessage = (value) => { text = value; };
  f.host.getInputComposerRef = () => ({ restoreComposerDraftMeta: () => metadataWrites++ });
  const old = f.host.restoreComposerDraftState();
  f.host.beginConversationView('B', 'ws'); f.host.currentConversationId = 'B';
  respond(f.requests[0], { success: true, data: { content: 'old' } }); await old;
  assert.equal(text, ''); assert.equal(f.ticks.length, 0);
  const latest = f.host.restoreComposerDraftState();
  respond(f.requests[1], { success: true, data: { content: 'new' } }); await latest;
  assert.equal(text, 'new');
  f.host.beginConversationView('C', 'ws'); f.host.currentConversationId = 'C';
  f.ticks.forEach((fn) => fn()); assert.equal(metadataWrites, 0);
});
test('older draft POST cannot clear newer unsaved input', async () => {
  const f = fixture(); const old = f.host.persistComposerDraftNow({ force: true });
  f.host.inputMessage = 'new typing';
  respond(f.requests[0], { success: true }); await old;
  assert.equal(f.host.composerDraftDirty, true); assert.equal(f.host.composerDraftLastSyncedContent, 'draft');
});
test('foreground completion preserves activity when only a background command remains', () => {
  const session = compile(`${base}conversation/session.ts`);
  const task = { currentTaskId: 'T', clearTask: noop };
  const methods = compile(`${base}taskPolling/completion.ts`, {
    '../common': { debugLog: noop, goalModeDebugLog: noop }, './shared': { jsonDebug: noop },
    '@/locales': locale, '../conversation/session': session, '../../../stores/task': { useTaskStore: () => task }
  }).completionMethods;
  let workCompleted = 0;
  const host = new Proxy({ ...methods, currentConversationId: 'A', messages: [], runtimeQueuedMessages: [],
    runtimeGuidanceFallbackQueue: [], runtimeQueueLimit: 5, $nextTick: noop,
    markLatestUserWorkCompleted: () => workCompleted++ }, {
    get: (target, key) => Object.hasOwn(target, key) ? target[key] : noop
  });
  session.beginConversationSession(host, 'A', 'ws');
  host.handleTaskComplete({ task_id: 'T', conversation_id: 'A', has_running_background_commands: true });
  assert.equal(host.taskInProgress, true); assert.equal(host.waitingForBackgroundCommand, true);
  assert.equal(host.waitingForSubAgent, false); assert.equal(workCompleted, 0);
});
(async () => { for (const [name, fn] of tests) { await fn(); console.log(`PASS ${name}`); } })()
  .catch((error) => { console.error(error); process.exitCode = 1; });
