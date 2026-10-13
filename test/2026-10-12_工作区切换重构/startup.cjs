const assert = require('node:assert/strict');
const { compile, locale } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const { fixture, flush, respond } = require('./navigation_fixture.cjs');
const base = 'static/src/app/methods/ui/';
function startupFixture() {
  const f = fixture();
  const globals = { fetch: f.fetch, window: { location: { pathname: '/new' } },
    URLSearchParams, history: { replaceState() {} } };
  const imports = { '@/locales': locale,
    './workspaceSelection': compile(base + 'workspaceSelection.ts', {}, globals),
    '../conversation/session': f.session,
    './workspaceNavigation': f.navigation,
    '../../../stores/conversationTabs': { useConversationTabsStore: () => ({ enabled: false }) } };
  const startup = compile(base + 'workspaceStartup.ts', imports, globals);
  const route = compile(base + 'route.ts', { ...imports, './workspaceStartup': startup }, globals);
  Object.assign(f.host, route.routeMethods, { currentHostWorkspaceId: '', currentConversationId: null,
    inputMessage: '', multiAgentIndexRebuildTriggered: true,
    isExplicitNewConversationRoute: () => true, isConversationIndependentRoute: () => false });
  return { ...f, startup };
}
async function run() {
  const f = startupFixture();
  const pending = f.host.bootstrapRoute(); await flush();
  assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].url, '/api/workspace-view');
  respond(f.requests[0], { success: true, data: {
    host_mode: true, workspace: { workspace_id: 'agents', project_path: '/synthetic/agents' } } });
  await flush();
  assert.equal(f.requests.length, 2);
  assert.equal(f.requests[1].url, '/api/input-draft?workspace_id=agents');
  respond(f.requests[1], { success: true, data: { content: 'the correct workspace draft' } });
  await pending;
  assert.equal(f.host.currentHostWorkspaceId, 'agents');
  assert.equal(f.host.inputMessage, 'the correct workspace draft');
  assert.equal(f.requests.some(r => r.url.includes('workspace_id=default')), false);
  console.log('PASS initial /new resolves real workspace before restoring its draft');

  const early = startupFixture();
  assert.equal((await early.host.restoreComposerDraftState()).pendingWorkspace, true);
  early.host.inputMessage = 'typed before initialization';
  assert.equal((await early.host.persistComposerDraftNow()).pendingWorkspace, true);
  assert.equal(early.requests.length, 0); assert.equal(early.host.composerDraftDirty, true);
  const prepared = early.startup.prepareInitialWorkspaceIdentity(early.host, () => true); await flush();
  respond(early.requests[0], { success: true, data: {
    host_mode: true, workspace: { workspace_id: 'agents' } } }); await prepared;
  await early.host.restoreComposerDraftState();
  assert.equal(early.host.inputMessage, 'typed before initialization');
  assert.equal(early.requests.length, 1);
  console.log('PASS unknown initial scope makes no default requests and preserves early typing');

  const stale = startupFixture();
  let owns = true;
  const resolving = stale.startup.prepareInitialWorkspaceIdentity(stale.host, () => owns); await flush();
  owns = false; stale.host.currentHostWorkspaceId = 'new-owner';
  respond(stale.requests[0], { success: true, data: { host_mode: true,
    workspace: { workspace_id: 'old-response' } } });
  assert.equal((await resolving).superseded, true);
  assert.equal(stale.host.currentHostWorkspaceId, 'new-owner');
  console.log('PASS late initial identity response cannot replace a newer navigation');

  const empty = startupFixture();
  const creating = empty.host.bootstrapRoute(); await flush();
  respond(empty.requests[0], { success: false, code: 'no_workspace' }); await creating;
  assert.equal(empty.host.workspaceBootstrapActive, true);
  assert.equal(empty.requests.length, 1);
  console.log('PASS zero-workspace startup never reads or writes a phantom default draft');

  const failure = startupFixture();
  const starting = failure.host.bootstrapRoute(); await flush();
  respond(failure.requests[0], { success: true, data: {
    host_mode: true, workspace: { workspace_id: 'agents' } } }); await flush();
  respond(failure.requests[1], { success: false, error: 'synthetic draft failure' }, false);
  await starting;
  assert.equal(failure.host.initialRouteResolved, true);
  assert.equal(failure.host.currentHostWorkspaceId, 'agents');
  assert.equal(failure.toasts.length, 1);
  assert.equal(failure.toasts[0].message, 'synthetic draft failure');
  console.log('PASS draft read failure is reported without rejecting page initialization');

  const directory = fixture();
  const directoryMethods = compile(base + 'hostWorkspace.ts', {
    '@/locales': locale,
    '../auxiliaryOwnership': { beginAuxiliaryRequest: () => () => true },
    '../conversation/session': directory.session,
    './workspaceNavigation': directory.navigation,
    '../../../stores/conversation': { useConversationStore: () => directory.store }
  }, { fetch: directory.fetch }).hostWorkspaceMethods;
  const load = directoryMethods.fetchHostWorkspaces.call(directory.host); await flush();
  respond(directory.requests[0], { success: true, data: {
    current_workspace_id: 'ws2', default_workspace_id: 'ws1', workspaces: [
      { workspace_id: 'ws1', is_current: false }, { workspace_id: 'ws2', is_current: true }
    ] } }); await load;
  assert.equal(directory.host.currentHostWorkspaceId, 'ws1');
  assert.equal(directory.store.conversationCacheWorkspaceId, 'ws1');
  assert.equal(directory.host.hostWorkspaces.find(w => w.workspace_id === 'ws1').is_current, true);
  console.log('PASS directory selection cannot overwrite the committed visible workspace');

  const items = directory.host.hostWorkspaces;
  const unavailable = directoryMethods.fetchHostWorkspaces.call(directory.host); await flush();
  respond(directory.requests[1], { success: false, error: 'synthetic directory failure' }, false);
  await unavailable;
  assert.equal(directory.host.hostWorkspaces, items);
  assert.equal(directory.host.currentHostWorkspaceId, 'ws1');
  assert.equal(directory.store.conversationCacheWorkspaceId, 'ws1');
  assert.equal(directory.toasts.at(-1).message, 'synthetic directory failure');
  console.log('PASS directory failure preserves visible identity and usable cached items');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
