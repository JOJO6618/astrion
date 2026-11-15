const assert = require('node:assert/strict');
const { fixture, answer, commandRequest, tab, flush, pinia, compile, noop, locale, randomUUID } = require('./fixture.cjs');
const tests = [];
const test = (name, run) => tests.push([name, run]);
const base = 'static/src/app/methods/';
const flat = (value) => JSON.parse(JSON.stringify(value));

test('legacy shell page (no key) degrades to web behaviour and never polls or commands', async () => {
  const f = fixture({ identity: { key: '', placement: 'main', visible: true, legacy: true, state: null } });
  await f.store.hydrate();
  assert.equal(f.store.enabled, false); assert.equal(f.requests.length, 0);
  assert.equal(await f.store.command('create', {}), null);
});

test('view hydrates from the identity snapshot without a GET and attaches with its own key', async () => {
  const f = fixture({ identity: { key: 'tab_A', placement: 'main', visible: true, legacy: false,
    state: f_state(3, [tab('tab_A', 'A')], 'tab_A') } });
  await f.store.hydrate();
  assert.equal(f.requests.length, 0); assert.equal(f.store.revision, 3);
  assert.equal(f.store.viewKey, 'tab_A'); assert.equal(f.store.ownsView, false);
  const attaching = f.store.attach();
  const request = await commandRequest(f, 'attach');
  assert.deepEqual(flat(JSON.parse(request.options.body).payload),
    { key: 'tab_A', renderer_id: f.store.localRendererId });
  answer(request, { success: true, result: {}, state: f.state(4, [tab('tab_A', 'A')], 'tab_A',
    { tab_A: f.store.localRendererId }) });
  await attaching;
  assert.equal(f.store.ownsView, true);
  // 另一份文档接管后本文档失去写权
  f.store.applyServerSnapshot(f.state(5, [tab('tab_A', 'A')], 'tab_A', { tab_A: 'other-document' }));
  assert.equal(f.store.ownsView, false);
});
function f_state(revision, tabs, active) {
  return { revision, tabs, active_key: active, workspace_id: 'ws1', renderers: {} };
}

test('command responses are forwarded to the shell once; stale ones are not; broadcasts are not echoed', async () => {
  const f = fixture(); await f.store.init();
  const closing = f.store.closeTab('x'); const request = await commandRequest(f, 'close');
  answer(request, { success: true, state: f.state(5, [tab('tab_A', 'A')], 'tab_A') }); await closing;
  assert.equal(f.forwarded.length, 1); assert.equal(f.forwarded[0].revision, 5);
  const stale = f.store.closeTab('y'); const second = await commandRequest(f, 'close');
  answer(second, { success: true, state: f.state(4, [], '') }); await stale;
  assert.equal(f.forwarded.length, 1);
  f.listeners[0](f.state(9, [tab('tab_A', 'A'), tab('tab_B', 'B')], 'tab_B'));
  assert.equal(f.store.revision, 9); assert.equal(f.forwarded.length, 1);
});

test('chrome role polls and forwards every newer snapshot; views never poll', async () => {
  const chrome = fixture({ chrome: true });
  assert.equal(chrome.store.role, 'chrome');
  chrome.store.startPolling(); await flush();
  answer(chrome.requests[0], { success: true, ...chrome.state(2, [tab('t', 'A')], 't') }); await flush();
  assert.equal(chrome.forwarded.length, 1); assert.equal(chrome.store.revision, 2);
  chrome.store.stopPolling();
  const view = fixture(); view.store.startPolling(); await flush();
  assert.equal(view.requests.length, 0);
});

test('mainTabs hides detached tabs and page tabs carry page fields', () => {
  const f = fixture();
  f.store.applyServerSnapshot(f.state(1, [tab('a', 'A'), tab('d', 'D', 'ws1', { placement: 'detached' }),
    { key: 'p', kind: 'page', page_type: 'git', route: '/git?workspace_id=ws1', opener_key: 'a',
      workspace_id: 'ws1', workspace_label: 'One', placement: 'main' }], 'a'));
  assert.deepEqual(f.store.mainTabs.map((t) => t.key), ['a', 'p']);
  const page = f.store.tabs.find((t) => t.key === 'p');
  assert.equal(page.pageType, 'git'); assert.equal(page.openerKey, 'a'); assert.equal(page.route, '/git?workspace_id=ws1');
});

test('page close returns to opener when present in main, else right neighbour, else left', () => {
  const f = fixture(); const { pageReturnTarget } = f.storeModule;
  let rev = 0;
  const norm = (rows) => { f.store.applyServerSnapshot(f.state(++rev, rows, 'a')); return f.store.tabs; };
  const page = (opener) => ({ key: 'p', kind: 'page', page_type: 'settings', route: '/settings',
    opener_key: opener, placement: 'main' });
  assert.equal(pageReturnTarget(norm([tab('a', 'A'), tab('b', 'B'), page('b'), tab('c', 'C')]), 'p').key, 'b');
  assert.equal(pageReturnTarget(norm([tab('a', 'A'), page('gone'), tab('c', 'C')]), 'p').key, 'c');
  assert.equal(pageReturnTarget(norm([tab('a', 'A'), tab('b', 'B', 'ws1', { placement: 'detached' }), page('b')]), 'p').key, 'a');
  assert.equal(pageReturnTarget(norm([page('')]), 'p'), null);
});

test('leaving a page activates the opener through show + activate and never closes the page tab', async () => {
  const f = fixture({ identity: { key: 'p', placement: 'main', visible: true, legacy: false, state: null } });
  f.store.applyServerSnapshot(f.state(1, [tab('a', 'A'),
    { key: 'p', kind: 'page', page_type: 'settings', route: '/settings', opener_key: 'a', placement: 'main' }], 'p'));
  await f.store.init();
  const leaving = f.store.leavePage();
  const request = await commandRequest(f, 'activate');
  assert.equal(JSON.parse(request.options.body).payload.key, 'a'); assert.deepEqual(f.shown, ['a']);
  answer(request, { success: true, result: { key: 'a' }, state: f.state(2, f.store.tabs.map((t) => ({ ...t,
    conversation_id: t.conversationId, workspace_id: t.workspaceId, page_type: t.pageType, opener_key: t.openerKey })), 'a') });
  await leaving;
  assert.equal(f.requests.some((r) => r.options.body && JSON.parse(r.options.body).action === 'close'), false);
});

test('no_workspace on a command stays on the page and never navigates', async () => {
  const f = fixture({ identity: { key: 't', placement: 'main', visible: true, legacy: false, state: null } });
  await f.store.init();
  const pending = f.store.addNewTab({ workspaceId: '' }); const request = await commandRequest(f, 'create');
  answer(request, { success: false, error: 'A workspace is required', code: 'no_workspace' }, 400);
  assert.equal(await pending, null); assert.equal(f.store.commandError, '');
});

function desktopMethods(f, session) {
  return compile(base + 'ui/conversationTabs.ts', {
    '../../../stores/conversationTabs': { useConversationTabsStore: () => f.store },
    './desktopView': { startDesktopView: noop, stopDesktopView: noop, desktopCreationTab: noop },
    '../conversation/session': { currentConversationSession: () => session },
    '@/locales': locale
  }, f.globals).conversationTabsMethods;
}

test('send readiness needs attach + renderer ownership + matching view identity', () => {
  const f = fixture(); f.store.viewKey = 'n';
  const session = { workspaceId: 'ws1', conversationId: '' };
  const host = { currentConversationId: null, historyLoading: false, hostWorkspaceSwitching: false };
  const ready = () => desktopMethods(f, session).isDesktopNavigationReady.call(host);
  f.store.applyServerSnapshot(f.state(1, [tab('n')], 'n', { n: f.store.localRendererId }));
  assert.equal(ready(), false, 'not attached');
  f.store.attached = true; assert.equal(ready(), true);
  f.store.applyServerSnapshot(f.state(2, [tab('n')], 'n', { n: 'someone-else' }));
  assert.equal(ready(), false, 'renderer replaced');
  f.store.applyServerSnapshot(f.state(3, [tab('n')], 'n', { n: f.store.localRendererId }));
  host.hostWorkspaceSwitching = true; assert.equal(ready(), false); host.hostWorkspaceSwitching = false;
  // 空白标签 ↔ 空会话；对话标签 ↔ 同一对话与工作区
  host.currentConversationId = 'A'; session.conversationId = 'A';
  assert.equal(ready(), false, 'new tab cannot show a conversation');
  f.store.applyServerSnapshot(f.state(4, [tab('n', 'conv_A')], 'n', { n: f.store.localRendererId }));
  assert.equal(ready(), true);
  session.workspaceId = 'ws2'; assert.equal(ready(), false);
  session.workspaceId = 'ws1'; host.currentConversationId = 'B'; session.conversationId = 'B';
  assert.equal(ready(), false);
  // 页面标签不可发送
  f.store.applyServerSnapshot(f.state(5, [{ key: 'n', kind: 'page', page_type: 'settings', route: '/settings',
    opener_key: '', workspace_id: '', placement: 'main' }], 'n', { n: f.store.localRendererId }));
  assert.equal(ready(), false);
});

test('blank new view without a conversation session (cold start / shell-created view) can send', () => {
  // 根因：空白 /new 路由只 leaveConversationView（无 session），旧守卫 !session 直接拒绝发送。
  const f = fixture(); f.store.viewKey = 'n';
  f.store.applyServerSnapshot(f.state(1, [tab('n')], 'n', { n: f.store.localRendererId })); f.store.attached = true;
  const host = { currentConversationId: null, currentHostWorkspaceId: 'ws1', historyLoading: false, hostWorkspaceSwitching: false };
  assert.equal(desktopMethods(f, undefined).isDesktopNavigationReady.call(host), true);
  host.currentHostWorkspaceId = 'ws2'; // 页面可见工作区与标签不一致仍拒绝
  assert.equal(desktopMethods(f, undefined).isDesktopNavigationReady.call(host), false);
  host.currentHostWorkspaceId = 'ws1'; host.currentConversationId = 'conv_x'; // 空白标签不得显示对话
  assert.equal(desktopMethods(f, undefined).isDesktopNavigationReady.call(host), false);
  // 创建标签捕获同样不依赖 session
  const view = compile(base + 'ui/desktopView.ts', {
    '../../../stores/conversationTabs': { useConversationTabsStore: () => f.store },
    '../conversation/session': { currentConversationSession: () => undefined },
    '@/locales': locale, './viewVisibility': f.visibility }, f.globals);
  assert.deepEqual(flat(view.desktopCreationTab({ currentHostWorkspaceId: 'ws1' })), { key: 'n', workspaceId: 'ws1' });
});

test('view-bound creation tab only exists for a blank own tab with the visible workspace', () => {
  const f = fixture(); f.store.viewKey = 'n';
  const view = compile(base + 'ui/desktopView.ts', {
    '../../../stores/conversationTabs': { useConversationTabsStore: () => f.store },
    '../conversation/session': { currentConversationSession: () => ({ workspaceId: 'ws1' }) },
    '@/locales': locale, './viewVisibility': f.visibility
  }, f.globals);
  f.store.applyServerSnapshot(f.state(1, [tab('n'), tab('o')], 'o'));
  assert.deepEqual(flat(view.desktopCreationTab({})), { key: 'n', workspaceId: 'ws1' });
  f.store.applyServerSnapshot(f.state(2, [tab('n', 'conv_A'), tab('o')], 'o'));
  assert.equal(view.desktopCreationTab({}), null);
});

test('desktop view attach retries on failure, then stop cancels the retry', async () => {
  const f = fixture(); const calls = []; const toasts = [];
  const store = { enabled: true, viewKey: 'k', hydrate: async () => {},
    attach: async () => { calls.push('attach'); if (calls.length === 1) throw new Error('boom'); } };
  const view = compile(base + 'ui/desktopView.ts', {
    '../../../stores/conversationTabs': { useConversationTabsStore: () => store },
    '../conversation/session': {}, '@/locales': locale, './viewVisibility': f.visibility
  }, f.globals);
  const host = { uiPushToast: (t) => toasts.push(t) };
  await view.startDesktopView(host); assert.equal(calls.length, 1); assert.equal(toasts.length, 1);
  assert.equal(f.timers.size, 1);
  [...f.timers.values()][0](); await flush(); assert.equal(calls.length, 2); assert.equal(toasts.length, 1);
  await view.startDesktopView(host); assert.equal(calls.length, 2, 'second start is a no-op');
  view.stopDesktopView(host);
});

test('becoming visible refreshes the paused auxiliary pollers once', async () => {
  const f = fixture(); const calls = [];
  const store = { enabled: true, viewKey: 'k', hydrate: async () => {}, attach: async () => {} };
  const view = compile(base + 'ui/desktopView.ts', {
    '../../../stores/conversationTabs': { useConversationTabsStore: () => store },
    '../conversation/session': {}, '@/locales': locale, './viewVisibility': f.visibility
  }, f.globals);
  const host = { fetchStatusSnapshot: () => calls.push('status'), refreshRunningWorkspaceTasks: () => calls.push('tasks'),
    reconcileRunningStateOnce: () => calls.push('reconcile'), refreshProjectGitSummary: () => calls.push('git'),
    fetchTerminalCount: () => calls.push('terminals') };
  await view.startDesktopView(host);
  f.visibility.setViewVisible(false); assert.deepEqual(calls, []);
  f.visibility.setViewVisible(true);
  assert.deepEqual(calls, ['status', 'tasks', 'reconcile', 'git', 'terminals']);
  view.stopDesktopView(host); f.visibility.setViewVisible(false); f.visibility.setViewVisible(true);
  assert.equal(calls.length, 5);
});

test('view visibility: default visible, hidden slows task polling to 1s and idle pollers pause', () => {
  const f = fixture(); const v = f.visibility;
  assert.equal(v.isViewVisible(), true); assert.equal(v.pollingDelayMs(), 250);
  v.setViewVisible(false); assert.equal(v.isViewVisible(), false); assert.equal(v.pollingDelayMs(), 1000);
  const intervals = []; const calls = [];
  const git = compile(base + 'ui/git.ts', { './viewVisibility': v, '@/locales': locale, '../conversation/session': {} }, { ...f.globals, window: { ...f.window,
    setInterval: (fn) => { intervals.push(fn); return intervals.length; }, clearInterval: noop } }).gitMethods;
  const host = { isConnected: true, fetchTerminalCount: () => calls.push('terminals'),
    refreshProjectGitSummary: () => calls.push('git') };
  git.startTerminalCountIdleRefresh.call(host); git.startProjectGitSummaryIdleRefresh.call(host);
  intervals.forEach((fn) => fn()); assert.deepEqual(calls, []);
  v.setViewVisible(true); intervals.forEach((fn) => fn()); assert.deepEqual(calls, ['terminals', 'git']);
});

test('web page without shell bridge is always visible and tabs stay disabled', () => {
  const globals = { window: {}, crypto: { randomUUID } };
  const visibility = compile('static/src/app/methods/ui/viewVisibility.ts', {}, globals);
  assert.equal(visibility.isViewVisible(), true);
  const transport = compile('static/src/stores/desktopNavigationTransport.ts', {}, globals);
  const storeModule = compile('static/src/stores/conversationTabs.ts', { pinia, './desktopNavigationTransport': transport,
    '../app/methods/ui/viewVisibility': visibility }, { ...globals, crypto: { randomUUID } });
  assert.equal(storeModule.useConversationTabsStore(pinia.createPinia()).enabled, false);
});

test('desktop views never write the session-selected workspace', async () => {
  const f = fixture(); f.store.enabled = true;
  const selection = compile(base + 'ui/workspaceSelection.ts', {
    '../../../stores/conversationTabs': { useConversationTabsStore: () => f.store } }, f.globals);
  const result = await selection.selectWorkspaceIdentity({ currentHostWorkspaceId: 'ws1' }, 'ws2', () => true);
  assert.equal(result.current_workspace_id, 'ws2'); assert.equal(f.requests.length, 0);
  f.store.enabled = false;
  const web = selection.selectWorkspaceIdentity({ currentHostWorkspaceId: 'ws1', versioningHostMode: true }, 'ws2', () => true);
  await flush(); assert.equal(f.requests.length, 1); assert.ok(f.requests[0].url.includes('/select?'));
  answer(f.requests[0], { success: true, data: { current_workspace_id: 'ws2' } }); await web;
});

test('sidebar click on the visible conversation is a no-op; another conversation sends open', async () => {
  const f = fixture(); f.store.enabled = true; const sent = [];
  const nav = compile(base + 'ui/workspaceNavigation.ts', {
    '../../../stores/conversationTabs': { useConversationTabsStore: () => f.store },
    '../../../stores/conversation': {}, '../../../stores/task': {}, '../conversation/session': {},
    './workspaceSelection': {}, './navigationRefresh': {}, './newConversation': {}, '@/locales': locale
  }, f.globals).workspaceNavigationMethods;
  const host = { currentConversationId: 'conv_A', currentHostWorkspaceId: 'ws1', versioningHostMode: true,
    historyLoading: false, openDesktopConversation: (p) => { sent.push(p); return { success: true }; },
    requestDesktopWorkspace: (id, o) => sent.push({ ws: id, ...o }) };
  const same = await nav.handleSelectWorkspaceConversation.call(host, { conversationId: 'A', workspaceId: 'ws1' });
  assert.equal(same.skipped, true); assert.equal(sent.length, 0);
  await nav.handleSelectWorkspaceConversation.call(host, { conversationId: 'B', workspaceId: 'ws1' });
  assert.deepEqual(flat(sent), [{ conversationId: 'B', workspaceId: 'ws1' }]);
  await nav.handleHostWorkspaceSwitch.call(host, 'ws2', { newConversation: true });
  assert.equal(sent[1].ws, 'ws2');
});

test('route load failure marks only this view failed and never opens another tab', async () => {
  const f = fixture(); const calls = [];
  f.store.enabled = true; f.store.failView = async (message) => calls.push(['fail', message]);
  const route = compile(base + 'ui/route.ts', {
    '@/locales': locale, '../../../stores/conversationTabs': { useConversationTabsStore: () => f.store },
    '../conversation/session': {}, './workspaceStartup': {}
  }, f.globals).routeMethods;
  const host = { leaveConversationView: () => calls.push('leave'), uiPushToast: () => calls.push('toast'),
    enterNewConversationPage: () => calls.push('create-new') };
  await route.failDesktopViewOrNew.call(host, new Error('gone'));
  assert.deepEqual(calls, ['leave', 'toast', ['fail', 'gone']]);
  f.store.enabled = false; calls.length = 0;
  route.failDesktopViewOrNew.call(host, 'x'); assert.deepEqual(calls, ['create-new']);
});

test('tab reorder plan: threshold geometry, live shifts and before_key', () => {
  const { planReorder, DRAG_THRESHOLD_PX } = compile('static/src/components/chrome/tabReorder.ts');
  const rects = ['a', 'b', 'c', 'd'].map((key, i) => ({ key, left: i * 100, width: 100 }));
  assert.ok(DRAG_THRESHOLD_PX > 0);
  const none = planReorder(rects, 'b', 10); assert.equal(none.beforeKey, null); assert.deepEqual(flat(none.shifts), {});
  const right = planReorder(rects, 'a', 160);
  assert.deepEqual(flat(right.order), ['b', 'a', 'c', 'd']); assert.equal(right.beforeKey, 'c');
  assert.deepEqual(flat(right.shifts), { b: -100 });
  const end = planReorder(rects, 'a', 500); assert.equal(end.beforeKey, ''); assert.deepEqual(flat(end.order), ['b', 'c', 'd', 'a']);
  const left = planReorder(rects, 'd', -250); assert.deepEqual(flat(left.order), ['a', 'd', 'b', 'c']);
  assert.equal(left.beforeKey, 'b'); assert.deepEqual(flat(left.shifts), { b: 100, c: 100 });
  const first = planReorder(rects, 'c', -400); assert.equal(first.beforeKey, 'a');
});

(async () => {
  for (const [name, run] of tests) { await run(); console.log('PASS ' + name); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
