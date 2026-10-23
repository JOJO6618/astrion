const assert = require('node:assert/strict');
const pinia = require('../../node_modules/pinia');
const vue = require('../../node_modules/vue');
const { randomUUID } = require('node:crypto');
const { compile, deferred, noop, locale } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const { fixture: navigationFixture, flush, respond, bootstrap, drain } = require('../2026-10-12_工作区切换重构/navigation_fixture.cjs');
const tests = [];
const test = (name, run) => tests.push([name, run]);
const tab = (key, id = '', workspace = 'ws1') => ({ key, kind: id ? 'conv' : 'new',
  conversation_id: id, workspace_id: workspace, title: id, error: '', running: false });
function fixture() {
  const requests = [], timers = new Map(), assigned = [];
  let timerId = 0;
  const window = { __ASTRION_DESKTOP__: true, setTimeout: (fn) => {
    timers.set(++timerId, fn); return timerId;
  }, clearTimeout: (id) => timers.delete(id), location: {
    pathname: '/new', search: '', assign: (url) => assigned.push(url)
  } };
  const fetch = (url, options = {}) => {
    const pending = deferred(); requests.push({ url, options, ...pending }); return pending.promise;
  };
  const globals = { window, fetch, crypto: { randomUUID } };
  const transport = compile('static/src/stores/desktopNavigationTransport.ts', {}, globals);
  const storeModule = compile('static/src/stores/conversationTabs.ts', {
    pinia, './desktopNavigationTransport': transport
  }, globals);
  const store = storeModule.useConversationTabsStore(pinia.createPinia());
  const state = (revision, tabs, active = '', id = 'nav-A', key = active, status = 'pending', page = '') => ({
    revision, tabs, active_key: active, resume_key: active, workspace_id: 'ws1',
    renderer_id: store.localRendererId, navigation: { id, key, status, page, error: '' }
  });
  return { requests, timers, window, assigned, transport, store, state, globals };
}
function answer(request, body, status = 200) {
  request.resolve({ ok: status < 400, status, statusText: 'synthetic', json: async () => body });
}
async function commandRequest(f, action) {
  await flush();
  const request = f.requests.find((r) => !r.used && r.options.method === 'POST' && JSON.parse(r.options.body).action === action);
  assert.ok(request, 'missing command ' + action);
  request.used = true;
  return request;
}
async function ack(f, action, state, result = {}) {
  const request = await commandRequest(f, action);
  answer(request, { success: true, state, result }); await flush(); return request;
}

test('older revisions and same-revision old responses cannot replace current tabs', () => {
  const f = fixture();
  f.store.applyServerSnapshot(f.state(8, [tab('B', 'B')], 'B', 'nav-B', 'B', 'committed'), 12);
  f.store.applyServerSnapshot(f.state(7, [tab('A', 'A')], 'A'), 20);
  f.store.applyServerSnapshot(f.state(8, [tab('B', 'stale-title')], 'B'), 11);
  assert.equal(f.store.activeKey, 'B'); assert.equal(f.store.tabs[0].conversationId, 'B');
});

test('hydrate failure is retryable and never enables an empty-state overwrite', async () => {
  const f = fixture();
  const failed = f.store.hydrate();
  answer(f.requests[0], { success: false, error: 'not ready' }, 503);
  await assert.rejects(failed); assert.equal(f.store.hydrated, false);
  const retry = f.store.hydrate();
  answer(f.requests[1], { success: true, ...f.state(1, [tab('A')], 'A') });
  await retry; assert.equal(f.store.hydrated, true); assert.equal(f.store.tabs.length, 1);
});

test('new tab uses one command identity and only the returned server key', async () => {
  const f = fixture();
  const created = f.store.addNewTab({ workspaceId: 'ws1' });
  const request = await commandRequest(f, 'create');
  const payload = JSON.parse(request.options.body);
  assert.ok(payload.payload.key.startsWith('tab_'));
  assert.equal(f.store.tabs.length, 0);
  answer(request, { success: true, result: { key: payload.payload.key },
    state: f.state(1, [tab(payload.payload.key)]) });
  await created; assert.equal(f.store.tabs[0].key, payload.payload.key);
});

test('network uncertainty retries the identical command instead of creating twice', async () => {
  const f = fixture(); const transport = new f.transport.NavigationTransport();
  const pending = transport.command({ command_id: 'same', action: 'create', payload: { key: 'tab_A' } });
  await flush(); const first = f.requests[0]; first.reject(new TypeError('connection lost'));
  await flush(); assert.equal(f.requests[1].options.body, first.options.body);
  answer(f.requests[1], { success: true, state: {} }); await pending;
});

test('business rejection is returned and is not silently retried', async () => {
  const f = fixture(); const transport = new f.transport.NavigationTransport();
  const pending = transport.command({ command_id: 'same', action: 'activate', payload: { key: 'closed' } });
  await flush(); answer(f.requests[0], { success: false, error: 'closed' }, 409);
  await assert.rejects(pending, (error) => error.status === 409);
  assert.equal(f.requests.length, 1);
});

test('polls do not overlap and stopping invalidates late responses and timers', async () => {
  const f = fixture(); const transport = new f.transport.NavigationTransport();
  const applied = [];
  transport.start((value) => applied.push(value), noop);
  assert.equal(f.requests.length, 1);
  // Only the request deadline exists; no interval can spawn a second request.
  assert.equal(f.timers.size, 1);
  transport.stop(); answer(f.requests[0], { success: true }); await flush();
  assert.equal(applied.length, 0); assert.equal(f.timers.size, 0);
});

test('command transport serializes writes without waiting for content loading', async () => {
  const f = fixture(); const transport = new f.transport.NavigationTransport();
  const first = transport.command({ command_id: 'one', action: 'create', payload: {} });
  const second = transport.command({ command_id: 'two', action: 'close', payload: {} });
  await flush(); assert.equal(f.requests.length, 1);
  answer(f.requests[0], { success: true }); await first; await flush();
  assert.equal(f.requests.length, 2);
  answer(f.requests[1], { success: true }); await second;
});

test('binding uses the captured source key even after another tab becomes active', async () => {
  const f = fixture();
  f.store.applyServerSnapshot(f.state(1, [tab('source'), tab('other')], 'other'));
  const pending = f.store.bindConversation('source', 'ws1', 'created', 'new');
  const request = await commandRequest(f, 'bind');
  assert.equal(JSON.parse(request.options.body).payload.key, 'source');
  answer(request, { success: true, result: { bound: true },
    state: f.state(2, [tab('source', 'created'), tab('other')], 'other') });
  await pending; assert.equal(f.store.activeKey, 'other'); assert.equal(f.store.tabs[0].key, 'source');
});

async function coordinated() {
  const f = fixture();
  const view = navigationFixture({ desktopTabs: f.store });
  view.host.isConversationIndependentRoute = () => f.window.location.pathname === '/settings';
  const coordinator = compile('static/src/app/methods/ui/desktopNavigation.ts', {
    vue, '../../../stores/conversationTabs': { useConversationTabsStore: () => f.store },
    '../conversation/session': view.session, '@/locales': locale
  }, f.globals);
  f.store.applyServerSnapshot(f.state(1, [tab('tab_A', 'A'), tab('tab_B', 'B', 'ws2')], 'tab_A', 'nav-A', 'tab_A', 'committed'));
  f.store.startupNavigationId = 'nav-A';
  const starting = coordinator.startDesktopNavigation(view.host);
  await ack(f, 'attach', f.state(2, [tab('tab_A', 'A'), tab('tab_B', 'B', 'ws2')], 'tab_A', 'attached-A', 'tab_A', 'committed'));
  await starting;
  return { ...f, view, coordinator };
}

test('close during B load revokes its late snapshot and only A can commit', async () => {
  const f = await coordinated();
  const rows = [tab('tab_A', 'A'), tab('tab_B', 'B', 'ws2')];
  f.store.applyServerSnapshot(f.state(3, rows, 'tab_A', 'nav-B', 'tab_B'));
  await flush();
  const old = f.view.requests.find((r) => r.url.includes('/B/bootstrap'));
  assert.ok(old, JSON.stringify({ requests: f.view.requests.map(r => r.url), toasts: f.view.toasts,
    attached: f.store.attached, renderer: f.store.rendererId, local: f.store.localRendererId,
    nav: f.store.navigation, commandError: f.store.commandError }));
  const closing = f.store.closeTab('tab_B');
  await ack(f, 'close', f.state(4, [tab('tab_A', 'A')], 'tab_A', 'back-A', 'tab_A'));
  await closing;
  respond(old, bootstrap('B', 'ws2'));
  await drain(f.view); await flush();
  await ack(f, 'commit', f.state(5, [tab('tab_A', 'A')], 'tab_A', 'back-A', 'tab_A', 'committed'));
  await flush();
  assert.equal(f.view.host.currentConversationId, 'A');
  assert.equal(f.store.tabs.length, 1); assert.equal(f.store.tabs[0].key, 'tab_A');
  assert.equal(f.view.attachments.some((args) => args[0].task_id === 'T-B'), false);
  assert.equal(f.requests.filter((r) => r.options.method === 'POST' && JSON.parse(r.options.body).action === 'open').length, 0);
  f.coordinator.stopDesktopNavigation(f.view.host);
});

test('latest B to C navigation commits content only after manager confirmation', async () => {
  const f = await coordinated();
  const rows = [tab('tab_A', 'A'), tab('tab_B', 'B', 'ws2'), tab('tab_C', 'C', 'ws3')];
  f.store.applyServerSnapshot(f.state(3, rows, 'tab_A', 'nav-B', 'tab_B'));
  await flush(); const old = f.view.requests.find((r) => r.url.includes('/B/bootstrap'));
  f.store.applyServerSnapshot(f.state(4, rows, 'tab_A', 'nav-C', 'tab_C'));
  await drain(f.view, { B: null });
  assert.equal(f.view.host.currentConversationId, 'A');
  await ack(f, 'commit', f.state(5, rows, 'tab_C', 'nav-C', 'tab_C', 'committed'));
  respond(old, bootstrap('B', 'ws2')); await flush();
  assert.equal(f.view.host.currentConversationId, 'C'); assert.equal(f.store.activeKey, 'tab_C');
  assert.equal(f.view.attachments.length, 1);
  f.coordinator.stopDesktopNavigation(f.view.host);
});

test('settings-to-conversation navigation uses explicit workspace and survives document replacement', async () => {
  const f = await coordinated();
  f.window.location.pathname = '/settings';
  f.store.applyServerSnapshot(f.state(3, [tab('tab_A', 'A')], '', 'resume-A', 'tab_A'));
  await flush();
  assert.equal(f.assigned[0], '/A?workspace_id=ws1');
  f.coordinator.stopDesktopNavigation(f.view.host);
});

test('coordinator unmount prevents a late attach response from starting polling', async () => {
  const f = fixture();
  const host = { currentHostWorkspaceId: 'ws1', currentConversationId: null,
    isConversationIndependentRoute: () => false };
  const coordinator = compile('static/src/app/methods/ui/desktopNavigation.ts', {
    vue, '../../../stores/conversationTabs': { useConversationTabsStore: () => f.store },
    '../conversation/session': {}, '@/locales': locale
  }, f.globals);
  f.store.applyServerSnapshot(f.state(1, []));
  const pending = coordinator.startDesktopNavigation(host);
  const request = await commandRequest(f, 'attach');
  coordinator.stopDesktopNavigation(host);
  answer(request, { success: true, state: f.state(2, []), result: {} });
  await pending; assert.equal(f.requests.filter((r) => !r.options.method).length, 0);
});

test('failed attach retries without restarting and does not repeat error toasts', async () => {
  const f = fixture(); const toasts = [];
  const host = { currentHostWorkspaceId: 'ws1', currentConversationId: 'A',
    isConversationIndependentRoute: () => false, uiPushToast: (value) => toasts.push(value) };
  const coordinator = compile('static/src/app/methods/ui/desktopNavigation.ts', {
    vue, '../../../stores/conversationTabs': { useConversationTabsStore: () => f.store },
    '../conversation/session': {}, '@/locales': locale
  }, f.globals);
  const rows = [tab('tab_A', 'A')];
  f.store.applyServerSnapshot(f.state(1, rows, 'tab_A', 'old', 'tab_A', 'committed'));
  const starting = coordinator.startDesktopNavigation(host);
  answer(await commandRequest(f, 'attach'), { success: false, error: 'unavailable' }, 503);
  await starting; assert.equal(f.store.attached, false); assert.equal(toasts.length, 1);
  // Discard the poll deadline, then execute only the coordinator retry timer.
  const retry = [...f.timers.entries()].find(([, fn]) => fn.name === 'attach');
  assert.ok(retry); f.timers.delete(retry[0]); const reconnecting = retry[1]();
  await ack(f, 'attach', f.state(2, rows, 'tab_A', 'restored', 'tab_A', 'committed'));
  await reconnecting; assert.equal(f.store.attached, true); assert.equal(toasts.length, 1);
  coordinator.stopDesktopNavigation(host);
  assert.equal(f.timers.size, 0);
});

test('unmount cancels a scheduled attach retry', async () => {
  const f = fixture();
  const host = { currentHostWorkspaceId: 'ws1', isConversationIndependentRoute: () => false };
  const coordinator = compile('static/src/app/methods/ui/desktopNavigation.ts', {
    vue, '../../../stores/conversationTabs': { useConversationTabsStore: () => f.store },
    '../conversation/session': {}, '@/locales': locale
  }, f.globals);
  f.store.applyServerSnapshot(f.state(1, []));
  const starting = coordinator.startDesktopNavigation(host);
  answer(await commandRequest(f, 'attach'), { success: false, error: 'unavailable' }, 503);
  await starting;
  assert.ok([...f.timers.values()].some(fn => fn.name === 'attach'));
  coordinator.stopDesktopNavigation(host);
  assert.equal(f.timers.size, 0);
});

test('send readiness requires the visible workspace and conversation to match the active tab', () => {
  const f = fixture(); let session = { workspaceId: 'ws1', conversationId: 'A' };
  const methods = compile('static/src/app/methods/ui/conversationTabs.ts', {
    '../../../stores/conversationTabs': { useConversationTabsStore: () => f.store },
    './desktopNavigation': {}, '../conversation/session': { currentConversationSession: () => session },
    '@/locales': locale
  }, f.globals).conversationTabsMethods;
  const host = { currentConversationId: 'A', historyLoading: false, hostWorkspaceSwitching: false };
  f.store.applyServerSnapshot(f.state(1, [tab('tab_A', 'conv_A')], 'tab_A', 'nav-A', 'tab_A', 'committed'));
  f.store.attached = true;
  assert.equal(methods.isDesktopNavigationReady.call(host), true);
  session = { workspaceId: 'ws2', conversationId: 'A' };
  assert.equal(methods.isDesktopNavigationReady.call(host), false);
  session = { workspaceId: 'ws1', conversationId: 'B' };
  assert.equal(methods.isDesktopNavigationReady.call(host), false);
  session = { workspaceId: 'ws1', conversationId: 'A' }; host.historyLoading = true;
  assert.equal(methods.isDesktopNavigationReady.call(host), false);
  host.historyLoading = false; f.store.navigation.status = 'pending'; f.store.navigation.page = '/settings';
  assert.equal(methods.isDesktopNavigationReady.call(host), false);
});

test('load failure restores the previous blank view without creating another tab', async () => {
  const f = fixture(); const recoveries = [];
  const host = { currentHostWorkspaceId: 'ws1', currentConversationId: null,
    isConversationIndependentRoute: () => false,
    handleSelectWorkspaceConversation: async () => { throw new Error('synthetic load failure'); },
    handleHostWorkspaceSwitch: async (ws, options) => recoveries.push({ ws, options }),
    leaveConversationView: noop };
  const coordinator = compile('static/src/app/methods/ui/desktopNavigation.ts', {
    vue, '../../../stores/conversationTabs': { useConversationTabsStore: () => f.store },
    '../conversation/session': {}, '@/locales': locale
  }, f.globals);
  const rows = [tab('blank'), tab('target', 'B')];
  f.store.applyServerSnapshot(f.state(1, rows, 'blank', 'initial', 'blank', 'committed'));
  const starting = coordinator.startDesktopNavigation(host);
  await ack(f, 'attach', f.state(2, rows, 'blank', 'attached', 'blank', 'committed'));
  await starting; recoveries.length = 0;
  f.store.applyServerSnapshot(f.state(3, rows, 'blank', 'load-B', 'target'));
  await flush();
  await ack(f, 'fail', f.state(4, rows, 'blank', 'load-B', 'target', 'error'));
  await flush();
  assert.equal(recoveries.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(recoveries[0])), {
    ws: 'ws1', options: { newConversation: true, desktopRecovery: true }
  });
  assert.equal(f.store.tabs.length, 2);
  assert.equal(f.requests.some(r => r.options.method === 'POST' && JSON.parse(r.options.body).action === 'create'), false);
  coordinator.stopDesktopNavigation(host);
});

test('startup never loads a stale address or creates a replacement over a pending target', async () => {
  const f = fixture(); f.window.location.pathname = '/deleted-old-address';
  f.store.applyServerSnapshot(f.state(1, [tab('target', 'B')], '', 'pending-B', 'target'));
  const calls = [];
  const host = { currentHostWorkspaceId: 'ws1',
    beginConversationView: () => ({ workspaceId: 'ws1' }),
    isExplicitNewConversationRoute: () => false, isConversationIndependentRoute: () => false,
    leaveConversationView: () => calls.push('leave'),
    enterConversation: () => { calls.push('load-old'); return { success: false }; },
    enterNewConversationPage: () => calls.push('create-new') };
  const route = compile('static/src/app/methods/ui/route.ts', {
    '@/locales': locale, '../../../stores/conversationTabs': { useConversationTabsStore: () => f.store },
    '../conversation/session': { conversationViewEpoch: () => 1, ownsConversationSession: () => true },
    './workspaceStartup': { prepareInitialWorkspaceIdentity: async () => ({ success: true }) }
  }, f.globals).routeMethods;
  await route.bootstrapRoute.call(host);
  assert.deepEqual(calls, ['leave']);
  assert.equal(host.initialRouteResolved, true);
  assert.equal(f.store.navigation.key, 'target');
  assert.equal(f.requests.length, 0);
});

(async () => {
  for (const [name, run] of tests) { await run(); console.log('PASS ' + name); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
