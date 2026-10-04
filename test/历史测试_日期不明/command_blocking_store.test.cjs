const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const ts = require('typescript');
const pinia = require('pinia');

const source = fs.readFileSync(path.join(__dirname, '../../static/src/stores/commandBlocking.ts'), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS }
}).outputText;
const tick = () => new Promise((resolve) => setImmediate(resolve));
const config = (patch = {}) => ({ success: true, enabled: true, rules: [], recommended_rules: ['danger'], ...patch });
const response = (payload, ok = true) => ({ ok, status: ok ? 200 : 500, json: async () => payload });
function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}
function fixture(handler) {
  pinia.setActivePinia(pinia.createPinia());
  const calls = [];
  const module = { exports: {} };
  vm.runInNewContext(compiled, {
    module, exports: module.exports,
    require: (id) => id === '@/locales' ? { t: (key) => key } : require(id),
    fetch: async (url, options) => {
      calls.push({ url, options });
      return handler(url, options, calls.length);
    }
  });
  const store = module.exports.useCommandBlockingStore();
  return { store, calls };
}

// These tests execute the real Pinia store and simulate only HTTP responses.
test('recommendations require explicit import and save; cancel discards the draft', async () => {
  const { store, calls } = fixture(() => response(config({ rules: ['custom'], recommended_rules: ['CUSTOM', 'danger'] })));
  store.openDialog();
  await tick();
  assert.equal(store.draft, 'custom');
  store.applyRecommended();
  store.applyRecommended();
  assert.equal(store.draft, 'custom\ndanger');
  assert.equal(store.rules.join(','), 'custom');
  assert.equal(calls.length, 1, 'import must not send a POST');
  store.closeDialog();
  assert.equal(store.draft, '');
  store.openDialog();
  await tick();
  assert.equal(store.draft, 'custom');
});

test('saving rules sends only normalized rules, preserving the server toggle', async () => {
  const { store, calls } = fixture((url, options) => options.method === 'POST'
    ? response(config({ enabled: false, rules: ['first', 'second'] }))
    : response(config({ enabled: false })));
  store.openDialog();
  await tick();
  store.draft = ' first \n FIRST\n\nsecond ';
  await store.saveRules();
  assert.deepEqual(JSON.parse(calls[1].options.body), { rules: ['first', 'second'] });
  assert.equal(store.enabled, false);
  assert.equal(store.dialogOpen, false);
});

test('toggle sends only enabled and never overwrites an unsaved rule draft', async () => {
  const { store, calls } = fixture((url, options) => response(config({ enabled: options.method !== 'POST', rules: ['server'] })));
  store.openDialog();
  await tick();
  store.draft = 'unsaved';
  await store.setEnabled(false);
  assert.deepEqual(JSON.parse(calls[1].options.body), { enabled: false });
  assert.equal(store.draft, 'unsaved');
  assert.equal(store.enabled, false);
});

test('initial load prevents an early toggle from being overwritten by stale GET', async () => {
  const pending = deferred();
  const { store, calls } = fixture(() => pending.promise);
  const loading = store.fetchState();
  await store.setEnabled(false);
  assert.equal(calls.length, 1);
  pending.resolve(response(config()));
  await loading;
  assert.equal(store.enabled, true);
  assert.equal(store.loaded, true);
});

test('close and reopen ignore a previous dialog request that finishes late', async () => {
  const old = deferred();
  const latest = deferred();
  const { store } = fixture((url, options, count) => count === 1 ? old.promise : latest.promise);
  store.openDialog();
  store.closeDialog();
  store.openDialog();
  latest.resolve(response(config({ rules: ['latest'] })));
  await tick();
  old.resolve(response(config({ rules: ['old'] })));
  await tick();
  assert.equal(store.draft, 'latest');
  assert.equal(store.loading, false);
});

test('background refresh never discards edited rules', async () => {
  const { store } = fixture(() => response(config({ rules: ['saved'] })));
  store.openDialog();
  await tick();
  store.draft = 'editing';
  await store.fetchState();
  assert.equal(store.draft, 'editing');
});

test('writes serialize against reads and other writes', async () => {
  const pending = deferred();
  const { store, calls } = fixture(() => pending.promise);
  store.loaded = true;
  store.draft = 'keep';
  const toggling = store.setEnabled(false);
  await store.fetchState();
  await store.saveRules();
  store.openDialog();
  assert.equal(calls.length, 1);
  assert.equal(store.dialogOpen, false);
  pending.resolve(response(config({ enabled: false })));
  await toggling;
  assert.equal(store.draft, 'keep');
  assert.equal(store.enabled, false);
});

test('malformed successful responses cannot replace rules or permit saving', async () => {
  const { store, calls } = fixture(() => response({}));
  store.rules = ['preserved'];
  store.openDialog();
  await tick();
  assert.equal(store.loadFailed, true);
  assert.equal(store.loaded, false);
  assert.equal(store.rules.join(','), 'preserved');
  assert.ok(store.error);
  store.draft = 'accidental-empty-overwrite';
  await store.saveRules();
  assert.equal(calls.length, 1);
  assert.equal(store.dialogOpen, true);
});

test('toggle failures roll back the optimistic switch and retain the error', async () => {
  const { store } = fixture(() => response({ success: false, error: 'write failed' }, false));
  store.loaded = true;
  await store.setEnabled(false);
  assert.equal(store.enabled, true);
  assert.equal(store.toggling, false);
  assert.equal(store.error, 'write failed');
});

test('failed rule save keeps the editor and draft available for retry', async () => {
  const { store } = fixture(() => response({ success: false, error: 'write failed' }, false));
  store.loaded = true;
  store.dialogOpen = true;
  store.draft = 'unsaved';
  await store.saveRules();
  assert.equal(store.dialogOpen, true);
  assert.equal(store.draft, 'unsaved');
  assert.equal(store.saving, false);
  assert.equal(store.error, 'write failed');
});
