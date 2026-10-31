const assert = require('node:assert/strict');
const vue = require('../../node_modules/vue');
const pinia = require('../../node_modules/pinia');
const { compile, deferred } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const locale = { t: (key, args) => `${key}${args ? JSON.stringify(args) : ''}`, currentLocale: vue.ref('zh-CN') };
const reply = (id, percent) => ({ ok: true, json: async () => ({
  success: true, account_id: id, account_hash: `hash-${id}`,
  usage: { rate_limit: {
    primary_window: { used_percent: percent, reset_at: Date.now() / 1000 + 3600 },
    secondary_window: { used_percent: percent + 1, reset_at: Date.now() / 1000 + 86400 }
  } }
}) });
const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); await vue.nextTick(); };
function fixture() {
  pinia.setActivePinia(pinia.createPinia());
  const requests = [], timers = new Map();
  let id = 0;
  const module = compile('static/src/stores/chatgptQuota.ts', {
    pinia, vue, '@/locales': locale
  }, { URLSearchParams,
    fetch: (url, options) => { const pending = deferred(); requests.push({ url, options, ...pending }); return pending.promise; },
    setTimeout: callback => { const key = ++id; timers.set(key, callback); return key; },
    clearTimeout: key => timers.delete(key)
  });
  return { module, store: module.useChatgptQuotaStore(), requests, timers };
}
async function main() {
  const f = fixture();
  const current = vue.ref('a');
  const shown = vue.computed(() => f.store.entries[current.value]?.usage?.rate_limit.primary_window.used_percent);
  const observed = [];
  const stop = vue.watch(shown, value => observed.push(value));
  const a = f.store.fetchQuota('a');
  const duplicate = f.store.fetchQuota('a');
  assert.equal(f.requests.length, 1, 'panel/card share in-flight request');
  current.value = 'b';
  const b = f.store.fetchQuota('b');
  f.requests[1].resolve(reply('b', 70));
  await b; await flush();
  assert.equal(shown.value, 70);
  assert.deepEqual(observed, [70], 'first response updates reactive subscribers');
  f.requests[0].resolve(reply('a', 10));
  await a; await duplicate; await flush();
  assert.equal(shown.value, 70, 'late account A never overwrites visible account B');
  await f.store.fetchQuota('a');
  assert.equal(f.requests.length, 2, 'fresh cache is reused');
  const force = f.store.fetchQuota('b', true);
  assert.match(f.requests[2].url, /refresh=1/);
  f.requests[2].reject(new Error('offline'));
  await force;
  assert.equal(f.store.entries.b.usage, null);
  assert.equal(f.store.entries.a.accountHash, 'hash-a');
  assert.equal(f.timers.size, 0, 'completed/error paths clear deadlines');
  const bad = f.store.fetchQuota('c');
  f.requests[3].resolve(reply('wrong-account', 99));
  await bad;
  assert.equal(f.store.entries.c.usage, null, 'reject mismatched backend identity');
  const timeout = f.store.fetchQuota('d');
  [...f.timers.values()][0]();
  assert.equal(f.requests[4].options.signal.aborted, true);
  f.requests[4].reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
  await timeout;
  assert.equal(f.store.entries.d.loading, false);
  stop();

  const settings = vue.reactive({ isAdmin: true });
  const model = vue.ref({ key: 'a/model', providerId: 'a', isChatgptModel: true });
  const active = vue.ref(false), intervals = new Map(), cleanup = [];
  let intervalId = 0;
  const composable = compile('static/src/components/token/useChatgptQuota.ts', {
    vue: { ...vue, onBeforeUnmount: callback => cleanup.push(callback) },
    '@/locales': locale, '@/stores/settings': { useSettingsStore: () => settings },
    '@/stores/chatgptQuota': f.module
  }, {
    setInterval: callback => { const key = ++intervalId; intervals.set(key, callback); return key; },
    clearInterval: key => intervals.delete(key)
  });
  const scope = vue.effectScope();
  const quota = scope.run(() => composable.useChatgptQuota(() => model.value, () => active.value));
  assert.equal(quota.accountId.value, 'a');
  active.value = true; await flush();
  assert.equal(quota.windows.value.length, 2);
  assert.equal(quota.windows.value[0].percent, 10);
  assert.equal(intervals.size, 1);
  model.value = { key: 'api/model', providerId: 'api', isChatgptModel: false };
  await flush();
  assert.equal(quota.applicable.value, true, 'ordinary models still show subscription quota');
  const accountLookup = f.requests.at(-1);
  assert.equal(accountLookup.url, '/api/codex/accounts');
  accountLookup.resolve({ ok: true, json: async () => ({ success: true, accounts: [
    { id: 'missing', connected: false, credential_missing: true },
    { id: 'a', connected: true }, { id: 'b', connected: true }
  ] }) });
  await flush();
  assert.equal(quota.accountId.value, 'a', 'first logged-in account is used for ordinary models');
  assert.equal(quota.windows.value[0].percent, 10);
  assert.equal(intervals.size, 1);
  model.value = { key: 'b/model', providerId: 'b', isChatgptModel: true };
  await flush();
  const quotaLookup = f.requests.at(-1);
  assert.match(quotaLookup.url, /account_id=b/);
  quotaLookup.resolve(reply('b', 45));
  await flush();
  assert.equal(quota.accountId.value, 'b', 'OAuth model overrides the default account');
  assert.equal(quota.windows.value[0].percent, 45);
  model.value = { key: 'unknown', providerId: 'not-an-oauth-account' };
  await flush();
  assert.equal(quota.accountId.value, 'a', 'unknown model metadata cannot select a foreign provider');
  active.value = false; await flush();
  assert.equal(intervals.size, 0, 'hidden cards stop polling');
  settings.isAdmin = false; await flush();
  assert.equal(quota.applicable.value, false, 'ordinary users do not call admin API');
  cleanup.forEach(callback => callback()); scope.stop();
  console.log('Passed quota deduplication, reactive first load, account isolation, cache, refresh, timeout, visibility and role checks.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
