// 桌面多视图前端测试共享夹具（v3：每个内容视图固定绑定一个标签，壳层桥由测试注入）。
const assert = require('node:assert/strict');
const pinia = require('../../../node_modules/pinia');
const { randomUUID } = require('node:crypto');
const { compile, deferred, noop, locale } = require('../../2026-10-07_运行对话快照/ts_fixture.cjs');
const { flush } = require('../../2026-10-12_工作区切换重构/navigation_fixture.cjs');
const tab = (key, id = '', workspace = 'ws1', extra = {}) => ({ key, kind: id ? 'conv' : 'new',
  conversation_id: id, workspace_id: workspace, title: id, error: '', running: false,
  placement: 'main', ...extra });
// v3：每个内容视图固定绑定一个标签；壳层桥（astrionShell）由测试注入。
function fixture({ chrome = false, identity } = {}) {
  const requests = [], timers = new Map(), forwarded = [], shown = [], listeners = [];
  let timerId = 0;
  const window = { __ASTRION_DESKTOP__: true, setTimeout: (fn) => {
    timers.set(++timerId, fn); return timerId;
  }, clearTimeout: (id) => timers.delete(id), location: { pathname: '/new', search: '' } };
  if (chrome) window.__ASTRION_CHROME__ = true;
  window.astrionShell = {
    identity: async () => identity === undefined ?
      { key: 'tab_A', placement: 'main', visible: true, legacy: false, state: null } : identity,
    onTabsState: (cb) => { listeners.push(cb); return () => {}; },
    forwardTabsState: (state) => forwarded.push(state),
    show: (key) => shown.push(key), onVisibility: noop
  };
  const fetch = (url, options = {}) => {
    const pending = deferred(); requests.push({ url, options, ...pending }); return pending.promise;
  };
  const globals = { window, fetch, crypto: { randomUUID } };
  const transport = compile('static/src/stores/desktopNavigationTransport.ts', {}, globals);
  const visibility = compile('static/src/app/methods/ui/viewVisibility.ts', {}, globals);
  const storeModule = compile('static/src/stores/conversationTabs.ts', {
    pinia, './desktopNavigationTransport': transport,
    '../app/methods/ui/viewVisibility': visibility
  }, globals);
  const store = storeModule.useConversationTabsStore(pinia.createPinia());
  const state = (revision, tabs, active = '', renderers = {}) => ({
    revision, tabs, active_key: active, workspace_id: 'ws1', renderers
  });
  return { requests, timers, window, forwarded, shown, listeners, transport, store, state,
    globals, storeModule, visibility };
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
module.exports = { fixture, answer, commandRequest, tab, flush, pinia, compile, noop, locale, deferred, randomUUID };
