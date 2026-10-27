const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const vue = require('../../node_modules/vue');
const pinia = require('../../node_modules/pinia');
const ts = require('../../node_modules/typescript');
const compiler = require('../../node_modules/@vue/compiler-sfc');
const { renderToString } = require('../../node_modules/@vue/server-renderer');
const { compile, deferred } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const root = path.resolve(__dirname, '../..');
const response = data => ({ ok: true, json: async () => data });
const account = count => ({ success: true, data: { quotas: {
  fast: { count, limit: 200 }, thinking: { count: 2, limit: 200 },
  search: { count: 3, limit: 20 }, role: 'user'
} } });
function fixture() {
  pinia.setActivePinia(pinia.createPinia());
  const pending = [], timers = new Map(), warnings = [];
  let calls = 0, timerId = 0;
  const { useResourceStore } = compile('static/src/stores/resource.ts', {
    pinia, '@/locales': { t: key => key },
    '../app/methods/auxiliaryOwnership': {}
  }, {
    fetch: () => { calls++; const request = deferred(); pending.push(request); return request.promise; },
    setInterval: callback => { const id = ++timerId; timers.set(id, callback); return id; },
    clearInterval: id => timers.delete(id),
    console: { warn: (...args) => warnings.push(args) }
  });
  return { store: useResourceStore(), pending, timers, warnings, calls: () => calls };
}
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
async function polling() {
  {
    const f = fixture();
    f.store.setUsageQuota(account(9).data);
    f.store.startUsageQuotaPolling();
    const duplicate = f.store.fetchUsageQuota();
    assert.equal(f.calls(), 1, 'initialization and polling share one request');
    f.pending.shift().resolve(response({ success: true, applicable: false, data: null }));
    await duplicate; await flush();
    assert.equal(f.store.usageQuota.applicable, false);
    assert.equal(f.store.usageQuota.fast.count, 0, 'previous account state is cleared');
    assert.equal(f.timers.size, 0);
    assert.equal(f.store.usageQuotaInFlight, false);
    f.store.startUsageQuotaPolling();
    await f.store.fetchUsageQuota();
    assert.equal(f.calls(), 1, 'host polling stays stopped');
    assert.equal(f.warnings.length, 0);
  }
  {
    const f = fixture();
    f.store.startUsageQuotaPolling();
    f.pending.shift().resolve(response(account(4)));
    await flush();
    assert.equal(f.store.usageQuota.applicable, true);
    assert.equal(f.store.usageQuota.fast.count, 4);
    assert.equal(f.timers.size, 1);
    [...f.timers.values()][0]();
    f.pending.shift().resolve(response(account(5)));
    await flush();
    assert.equal(f.store.usageQuota.fast.count, 5);
    assert.equal(f.calls(), 2);
    f.store.stopUsageQuotaPolling();
  }
  {
    const f = fixture();
    const old = f.store.fetchUsageQuota();
    const oldRequest = f.pending.shift();
    f.store.stopUsageQuotaPolling();
    f.store.startUsageQuotaPolling();
    oldRequest.resolve(response({ success: true, applicable: false, data: null }));
    await old;
    assert.equal(f.store.usageQuota.applicable, null, 'late response cannot overwrite state');
    assert.equal(f.store.usageQuotaInFlight, true, 'old finally cannot clear a new request');
    assert.equal(f.timers.size, 1);
    f.pending.shift().resolve(response(account(7)));
    await flush();
    assert.equal(f.store.usageQuota.fast.count, 7);
    assert.equal(f.store.usageQuotaInFlight, false);
    f.store.stopUsageQuotaPolling();
  }
  {
    const f = fixture();
    const body = deferred();
    const old = f.store.fetchUsageQuota();
    f.pending.shift().resolve({ ok: true, json: () => body.promise });
    await flush();
    f.store.stopUsageQuotaPolling();
    body.resolve(account(99));
    await old;
    assert.equal(f.store.usageQuota.applicable, null, 'stop during JSON parsing invalidates response');
  }
}
function drawer() {
  const file = 'static/src/components/token/TokenDrawer.vue';
  const { descriptor, errors } = compiler.parse(fs.readFileSync(path.join(root, file), 'utf8'), { filename: file });
  assert.deepEqual(errors, []);
  const script = compiler.compileScript(descriptor, { id: 'quota-regression', inlineTemplate: true });
  const code = ts.transpileModule(script.content, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS
  } }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(code, { module, exports: module.exports, require(key) {
    if (key === 'vue') return vue;
    if (key === '@/locales') return { t: key => key, currentLocale: vue.ref('zh-CN') };
    if (key.endsWith('.vue')) return { default: vue.defineComponent({ render: () => null }) };
    throw new Error(`Unexpected import ${key}`);
  } }, { filename: file });
  return module.exports.default;
}
async function rendering() {
  const component = drawer();
  for (const applicable of [false, true]) {
    let formatted = 0;
    const app = vue.createSSRApp(component, {
      visible: true, collapsed: false, conversationId: null,
      currentConversationTokens: {}, currentContextTokens: 0,
      containerStatus: null, containerNetRate: { down_bps: null, up_bps: null },
      projectStorage: { used_bytes: 0 }, usageQuota: { ...account(4).data.quotas, applicable },
      formatTokenCount: String, formatPercentage: String, formatBytes: String,
      formatQuotaValue: value => { formatted++; return `quota-${value.count}/${value.limit}`; },
      formatResetTime: String, formatRate: String
    });
    app.config.globalProperties.$t = key => key;
    const html = await renderToString(app);
    assert.equal(html.includes('sidebar.hostModeNoQuota'), !applicable);
    assert.equal(formatted, applicable ? 3 : 0);
    assert.equal(html.includes('quota-4/200'), applicable);
  }
}
(async () => {
  await polling();
  await rendering();
  console.log('Quota regression passed: 4 polling scenarios and 2 real TokenDrawer SSR states.');
})().catch(error => { console.error(error); process.exitCode = 1; });
