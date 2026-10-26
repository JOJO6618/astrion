const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('../../node_modules/typescript');
const { compile, deferred, noop, locale } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const root = path.resolve(__dirname, '../..');
const stores = { subAgents: [], commands: [], form: { tool_intent_enabled: true } };
const source = fs.readFileSync(path.join(root, 'static/src/app/computed.ts'), 'utf8');
const code = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
}).outputText;
const computedModule = {};
new Function('require', 'exports', code)((name) => {
  if (name === '@/locales') return { ...locale, currentLocale: { value: 'zh-CN' } };
  if (name === 'pinia') return { mapState: () => ({}), mapWritableState: () => ({}) };
  if (name.includes('avatarFace')) return { toolFaceKey: name => name };
  if (name.includes('messageVisibility')) return { messageStartsWork: () => true };
  if (name.includes('avatarStatus')) return compile('static/src/utils/avatarStatus.ts', {
    '@/locales': { ...locale, currentLocale: { value: 'zh-CN' } },
    './avatarFace': { toolFaceKey: name => name },
    './messageVisibility': { messageStartsWork: () => true }
  });
  return new Proxy({}, { get: () => () => stores });
}, computedModule);
const computed = computedModule.computed;
const idleHost = () => ({ messages: [], taskInProgress: false, streamingMessage: false,
  apiRequestPending: false, stopRequested: false, waitingForSubAgent: false,
  waitingForBackgroundCommand: false, preparingTools: new Map(), activeTools: new Map() });

for (const status of ['idle', 'failed', 'timeout', 'completed', 'cancelled', 'terminated', '']) {
  stores.subAgents = [{ status }];
  assert.equal(computed.avatarStatus.call(idleHost()).mode, 'idle', status);
  assert.equal(computed.subAgentCount.call({}), status === 'terminated' ? 0 : 1, status);
}
stores.subAgents = [{ status: 'idle' }, { status: 'running' }, { status: 'terminated' }];
assert.equal(computed.subAgentCount.call({}), 2);
assert.equal(computed.avatarStatus.call(idleHost()).mode, 'work');
stores.subAgents = Array.from({ length: 20 }, () => ({ status: 'idle' }));
assert.equal(computed.subAgentCount.call({}), 20);
assert.equal(computed.avatarStatus.call(idleHost()).mode, 'idle');
stores.subAgents = [null];
assert.equal(computed.subAgentCount.call({}), 0);
stores.commands = [{ status: 'running' }];
assert.equal(computed.avatarStatus.call(idleHost()).mode, 'work');
stores.commands = [{ status: 'completed' }];
assert.equal(computed.avatarStatus.call(idleHost()).mode, 'idle');
assert.equal(computed.avatarStatus.call({ ...idleHost(), taskInProgress: true,
  apiRequestPending: true }).apiWaiting, true);

// Exercise the actual menu handler with deferred rendering and navigation ownership.
const dock = { userCollapsed: true };
let view = 1;
let request = 0;
const { modeMethods } = compile('static/src/app/methods/ui/mode.ts', {
  '@/locales': locale,
  '../../../stores/model': { useModelStore: () => ({}) },
  '../../../stores/quickDock': { useQuickDockStore: () => dock },
  '../auxiliaryOwnership': { beginAuxiliaryRequest: () => {
    const ownerView = view;
    const ownerRequest = ++request;
    return () => view === ownerView && request === ownerRequest;
  } }
});
function makeHost(mobile, activeMobileOverlay = null) {
  const rendered = deferred();
  const result = { isMobileViewport: mobile, activeMobileOverlay, scrolls: 0, opened: [],
    $nextTick: () => rendered.promise,
    $refs: { quickDock: { scrollToSubAgents: () => result.scrolls++ } },
    openMobileOverlay(target) { this.opened.push(target); this.activeMobileOverlay = target; }
  };
  return { host: result, rendered };
}

// Test scroll geometry without moving the document or the conversation's scroll area.
let reduceMotion = false;
const { scrollToSubAgentCard } = compile('static/src/components/chat/quickdock/navigation.ts', {}, {
  getComputedStyle: () => ({ paddingTop: '24px' }),
  window: { matchMedia: () => ({ matches: reduceMotion }) }
});
const scrollCalls = [];
const container = { scrollTop: 100, clientTop: 1,
  getBoundingClientRect: () => ({ top: 20 }),
  querySelector: (selector) => {
    assert.equal(selector, '[data-quick-dock-section="agents"]');
    return { getBoundingClientRect: () => ({ top: 250 }) };
  },
  scrollTo: options => scrollCalls.push(options)
};
scrollToSubAgentCard(container);
assert.equal(scrollCalls[0].top, 305);
assert.equal(scrollCalls[0].behavior, 'smooth');
reduceMotion = true;
scrollToSubAgentCard(container);
assert.equal(scrollCalls[1].behavior, 'instant');
scrollToSubAgentCard(null);
scrollToSubAgentCard({ ...container, querySelector: () => null });
assert.equal(scrollCalls.length, 2);
scrollToSubAgentCard({ ...container, scrollTop: 0,
  getBoundingClientRect: () => ({ top: 500 }) });
assert.equal(scrollCalls[2].top, 0);

(async () => {
  for (const [mobile, overlay] of [[false, null], [true, null], [true, 'quickdock']]) {
    dock.userCollapsed = true;
    const { host, rendered } = makeHost(mobile, overlay);
    const run = modeMethods.handleShowSubAgents.call(host);
    assert.equal(dock.userCollapsed, false);
    assert.equal(host.scrolls, 0);
    assert.equal(host.opened.length, mobile && overlay !== 'quickdock' ? 1 : 0);
    rendered.resolve();
    await run;
    assert.equal(host.scrolls, 1);
  }
  const stale = makeHost(false);
  const staleRun = modeMethods.handleShowSubAgents.call(stale.host);
  view++;
  stale.rendered.resolve();
  await staleRun;
  assert.equal(stale.host.scrolls, 0, 'Navigation invalidates a pending reveal.');

  const repeated = makeHost(false);
  const oldRun = modeMethods.handleShowSubAgents.call(repeated.host);
  const latestRun = modeMethods.handleShowSubAgents.call(repeated.host);
  repeated.rendered.resolve();
  await Promise.all([oldRun, latestRun]);
  assert.equal(repeated.host.scrolls, 1, 'Only the latest click scrolls.');

  const input = fs.readFileSync(path.join(root, 'static/src/components/input/InputComposer.vue'), 'utf8');
  const app = fs.readFileSync(path.join(root, 'static/src/App.vue'), 'utf8');
  const dockSource = fs.readFileSync(path.join(root, 'static/src/components/chat/quickdock/QuickDock.vue'), 'utf8');
  assert.ok(input.includes("@click.stop=\"$emit('show-sub-agents')\""));
  assert.ok(input.includes("$t('input.subAgentCount', { n: subAgentCount })"));
  assert.ok(!input.includes('stop-all-sub-agents'));
  assert.ok(app.includes('@show-sub-agents="handleShowSubAgents"'));
  assert.equal((app.match(/ref="quickDock"/g) || []).length, 2);
  assert.ok(dockSource.includes('kind="agent" data-quick-dock-section="agents"'));
  assert.ok(dockSource.includes('defineExpose({ scrollToSubAgents:'));
  console.log('Passed: retained counts, idle/work avatar, desktop/mobile reveal, ownership and contained scrolling.');
})().catch(error => { console.error(error); process.exitCode = 1; });
