const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const vue = require('../../node_modules/vue');
const ts = require('../../node_modules/typescript');
const compiler = require('../../node_modules/@vue/compiler-sfc');
const { renderToString } = require('../../node_modules/@vue/server-renderer');
const { compile, deferred } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const handoff = compile('static/src/chat/handoff.ts');
const filename = 'static/src/components/chat-mode/HandoffCard.vue';
const { descriptor } = compiler.parse(fs.readFileSync(filename, 'utf8'), { filename });
const script = compiler.compileScript(descriptor, { id: 'handoff-regression' });
const template = compiler.compileTemplate({ source: descriptor.template.content, filename, id: 'handoff-regression' });
assert.deepEqual(template.errors, []);
function evaluate(source, imports, globals = {}) {
  const code = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
  }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(code, { module, exports: module.exports, require: key => {
    if (key in imports) return imports[key];
    throw new Error(`Unmocked import: ${key}`);
  }, ...globals });
  return module.exports;
}
const renderModule = evaluate(template.code, { vue });
const initial = (patch = {}) => ({ id: 'label-1', message_id: 'a1', sequence: 1, status: 'pending',
  reason: 'files', leaf_id: 'a1', countdown_enabled: true, presentation_id: 'view',
  deadline: Date.now() / 1000 + 30, ...patch });
function fixture(patch = {}, presentationId = 'view') {
  const mounted = [], cleanup = [], events = [], requests = [], navigations = [], timers = new Map();
  let serial = 0;
  let handler = async () => initial(patch);
  const window = { document: { hidden: false, addEventListener() {}, removeEventListener() {} },
    addEventListener() {}, removeEventListener() {},
    setTimeout(fn) { timers.set(++serial, fn); return serial; },
    clearTimeout(id) { timers.delete(id); }, setInterval() { return ++serial; }, clearInterval() {},
    location: { assign: url => navigations.push(url) } };
  const request = (path, payload) => { requests.push({ path, payload }); return handler(path, payload); };
  const component = evaluate(script.content, {
    vue: { ...vue, onMounted: fn => mounted.push(fn), onBeforeUnmount: fn => cleanup.push(fn) },
    '@/chat/api': { chatApi: request, command: request, operationId: () => `operation-${++serial}` },
    '@/chat/handoff': handoff,
    '@/stores/conversationTabs': { useConversationTabsStore: () => ({ enabled: false }) }
  }, { window });
  const props = { conversationId: 'conv_test', handoffId: 'label-1', presentationId, initialState: initial(patch) };
  const card = component.default.setup(props, { expose() {}, emit: (...args) => events.push(args) });
  async function html() {
    const context = vue.proxyRefs({ ...card, ...props, $t: key => key });
    return renderToString(vue.createSSRApp({ render: () => renderModule.render(context, []) }));
  }
  return { card, requests, events, navigations, cleanup, mounted, window, html,
    respond(fn) { handler = fn; } };
}
const cases = [];
const test = (name, fn) => cases.push([name, fn]);
test('all labels map by their own ID; processed and obsolete labels stay hidden', () => {
  const states = [initial(), initial({ id: 'second', sequence: 2 }),
    initial({ id: 'done', status: 'completed' }), initial({ id: 'continued', status: 'declined' }),
    initial({ id: 'old', status: 'superseded' }), initial({ id: 'other-branch', leaf_id: 'other' })];
  const document = { metadata: { chat: { latest_leaf_id: 'a1', handoffs: Object.fromEntries(states.map(s => [s.id, s])) } } };
  assert.deepEqual(Array.from(handoff.visibleHandoffs(document, [{ message_id: 'a1' }]), s => s.id), ['label-1', 'second']);
});
test('reloaded pending card keeps buttons without any countdown', async () => {
  const f = fixture({ countdown_enabled: false, deadline: null }, 'reloaded');
  const html = await f.html();
  assert.equal((html.match(/<button/g) || []).length, 2);
  assert.ok(!html.includes('chatMode.expires'));
  await f.card.poll();
  assert.ok(f.requests[0].path.includes('handoff_id=label-1'));
  assert.equal(f.requests[0].payload, undefined);
});
test('completed and continued cards render no interactive section', async () => {
  for (const status of ['completed', 'declined', 'superseded']) {
    const f = fixture({ status });
    assert.ok(!(await f.html()).includes('<section'));
    await f.card.apply(initial({ status: 'completed', agent_url: '/agent' }));
    assert.deepEqual(f.navigations, []);
  }
});
test('fresh countdown heartbeats target the exact suggestion and presentation', async () => {
  const f = fixture();
  assert.ok((await f.html()).includes('chatMode.expires'));
  await f.card.poll();
  assert.equal(f.requests[0].payload.handoff_id, 'label-1');
  assert.equal(f.requests[0].payload.presentation_id, 'view');
  assert.equal(f.requests[0].payload.decision, 'observe');
  f.card.suspend();
  assert.equal(f.requests[1].payload.keepalive, true);
  assert.equal(JSON.parse(f.requests[1].payload.body).decision, 'pause');
  assert.ok(!(await f.html()).includes('chatMode.expires'));
});
test('old poll responses cannot overwrite a newer switch decision', async () => {
  const f = fixture();
  const pending = deferred();
  f.respond((_, payload) => payload?.decision === 'observe' ? pending.promise : Promise.resolve(initial({ status: 'preparing' })));
  const poll = f.card.poll();
  await f.card.decide('accept');
  pending.resolve(initial());
  await poll;
  assert.equal(f.card.state.value.status, 'preparing');
  await f.card.apply(initial({ status: 'completed', agent_url: '/agent', agent_conversation_id: 'agent' }));
  assert.deepEqual(f.navigations, ['/agent']);
});
test('a failed switch retains retry actions and never marks the card completed', async () => {
  const f = fixture();
  f.respond(async () => initial({ status: 'failed', countdown_enabled: false, error: 'Summary unavailable' }));
  await f.card.decide('accept');
  assert.equal(f.card.state.value.status, 'failed');
  assert.deepEqual(f.navigations, []);
  assert.equal((await f.html()).match(/<button/g).length, 2);
});
(async () => {
  for (const [name, fn] of cases) { await fn(); console.log(`PASS ${name}`); }
  console.log(`${cases.length} handoff frontend checks passed`);
})().catch(error => { console.error(error); process.exitCode = 1; });
