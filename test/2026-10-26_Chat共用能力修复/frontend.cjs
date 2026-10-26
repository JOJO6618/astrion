const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const vue = require('../../node_modules/vue');
const compiler = require('../../node_modules/@vue/compiler-sfc');
const ts = require('../../node_modules/typescript');
const { renderToString } = require('../../node_modules/@vue/server-renderer');
const { compile } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const locale = { t: (key, values) => values ? `${key}:${JSON.stringify(values)}` : key };
const labels = compile('static/src/utils/generatingLabel.ts', { '@/locales': locale });
const errors = compile('static/src/utils/taskErrorToast.ts', { '@/locales': locale });
const types = compile('static/src/chat/types.ts');
const tick = async () => { for (let i = 0; i < 16; i++) await Promise.resolve(); };

function view(status = 'running', cursor = 2) {
  return { document: { id: 'conv_test', title: 'Test', messages: [],
    metadata: { network_enabled: true, chat: { latest_leaf_id: null, turns: [] } } },
    display: { task: { task_id: 'task', status }, next_event_idx: cursor,
      messages: [{ id: 'placeholder', role: 'assistant', awaitingFirstContent: true, generatingLabel: '', actions: [] }], state: {} } };
}
function fixture() {
  let current = view();
  const timers = new Map(), notifications = [], routes = [];
  let serial = 0;
  const api = {
    loadView: async () => current,
    chatApi: async route => { routes.push(route); return route.startsWith('/tasks/') ? current : []; }
  };
  const { useChat } = compile('static/src/chat/useChat.ts', {
    vue: { ...vue, onBeforeUnmount() {} }, './api': api, './types': types,
    '@/utils/generatingLabel': labels
  }, { window: { setTimeout(fn) { timers.set(++serial, fn); return serial; }, clearTimeout(id) { timers.delete(id); } } });
  return { chat: useChat(data => notifications.push(errors.taskErrorToast(data))), notifications, routes,
    set(value) { current = value; },
    async poll() { const [id, fn] = [...timers][0]; timers.delete(id); fn(); await tick(); }, timers };
}
async function settingsRender() {
  const { descriptor } = compiler.parse(fs.readFileSync('static/src/App.vue', 'utf8'));
  const template = compiler.compileTemplate({ source: descriptor.template.content, filename: 'App.vue', id: 'settings-regression' });
  assert.deepEqual(template.errors, []);
  const code = ts.transpileModule(template.code, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(code, { module, exports: module.exports, require: key => { assert.equal(key, 'vue'); return vue; } });
  const app = vue.createSSRApp({
    components: {
      AppShell: vue.defineComponent({ setup(_, ctx) { return () => vue.h('main', ctx.slots.default?.()); } }),
      SettingsShell: vue.defineComponent({ setup() { return () => vue.h('section', { 'data-settings-ready': '' }, 'Settings'); } })
    },
    data: () => ({ settingsRoute: 'settings/chat', isConnected: false, messages: [], dragOverActive: false }),
    render: module.exports.render
  });
  app.config.warnHandler = () => {};
  app.config.globalProperties.$t = locale.t;
  const html = await renderToString(app);
  assert.ok(html.includes('data-settings-ready'), 'Settings render without Agent connection');
  assert.ok(!html.includes('app-loading-state'));
}
async function settingsLifecycle() {
  const noop = () => {};
  const { mounted } = compile('static/src/app/lifecycle.ts', {
    '../stores/chatActions': {}, '../stores/sandboxSetup': {},
    '../composables/useScrollControl': { normalizeScrollLock: noop },
    './bootstrap': { setupShowImageObserver: noop, setupImagePreviewDelegation: noop },
    './methods/common': { debugLog: noop }
  }, { window: { addEventListener: noop } });
  let restored = false;
  await mounted.call({ settingsRoute: 'settings/chat', setupMobileViewportWatcher: noop,
    bootstrapRoute: async () => {}, $nextTick: fn => fn(), ensureScrollListener: noop,
    restoreConversationTabView: async () => { restored = true; },
    loadInitialData() { throw Error('Settings initialized Agent resources'); } });
  assert.equal(restored, true);
}
(async () => {
  const first = [{ id: 'a', role: 'assistant', awaitingFirstContent: true, generatingLabel: '' }];
  labels.prepareGeneratingLabels(first);
  assert.ok(first[0].generatingLabel.startsWith('stores.generating'));
  const second = [{ ...first[0], generatingLabel: '' }];
  labels.prepareGeneratingLabels(second, first);
  assert.equal(second[0].generatingLabel, first[0].generatingLabel);
  const variants = new Set(Array.from({ length: 150 }, () => labels.randomGeneratingLabel()));
  assert.ok(variants.size > 1, 'Multiple original waiting labels remain available');
  const f = fixture(); await f.chat.load('conv_test');
  const stableLabel = f.chat.display.value.messages[0].generatingLabel;
  f.set({ view: view('running', 3), status: 'running', events: [{ idx: 2, type: 'error',
    data: { retry: true, message: 'MissingSessionID', attempt: 1, max_attempts: 5, retry_in: 10 } }] });
  await f.poll();
  assert.equal(f.notifications.length, 1);
  assert.equal(f.notifications[0].duration, 10000);
  assert.equal(f.notifications[0].type, 'info');
  assert.match(f.notifications[0].message, /MissingSessionID/);
  assert.equal(f.chat.display.value.messages[0].generatingLabel, stableLabel);
  f.set({ view: view('failed', 5), status: 'failed', error: 'MissingSessionID', events: [
    { idx: 3, type: 'error', data: { fatal: true, message: 'MissingSessionID' } },
    { idx: 4, type: 'task_complete', data: {} } ] });
  await f.poll();
  assert.equal(f.notifications.length, 2, 'Fatal event and result.error show one toast');
  assert.equal(f.notifications[1].type, 'error');
  assert.equal(f.notifications[1].duration, 8000);
  assert.equal(f.timers.size, 0);
  const failed = fixture(); failed.set(view('failed', 5)); await failed.chat.load('conv_test');
  failed.set({ view: view('failed', 5), status: 'failed', error: 'Already failed', events: [] });
  await failed.poll();
  assert.equal(failed.notifications.length, 1, 'Failure before initial bootstrap remains visible');
  assert.equal(failed.timers.size, 0);
  assert.equal(errors.taskErrorToast({ error_type: 'parameter_format_error', message: 'bad arguments' }).type, 'warning');
  await settingsRender();
  await settingsLifecycle();
  console.log('PASS: shared labels, retry/fatal errors, failure before bootstrap, settings render and workspace-free lifecycle');
})().catch(error => { console.error(error); process.exitCode = 1; });
