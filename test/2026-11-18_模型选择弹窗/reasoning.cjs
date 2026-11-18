// 顶栏模型弹窗：推理滑块档位换算（快速模式并入「无」）与 Agent 页 setHeaderReasoning。
// 运行：node test/2026-11-18_模型选择弹窗/reasoning.cjs
const assert = require('node:assert/strict');
const { compile, locale } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');

const cases = [];
const test = (name, fn) => cases.push([name, fn]);
const R = compile('static/src/utils/reasoningStops.ts');
const ALL = ['low', 'medium', 'high', 'xhigh', 'max'];
// 被测模块跑在 vm 上下文里，数组原型跨 realm，先转成本 realm 再比较
const same = (actual, expected) => assert.deepEqual(JSON.parse(JSON.stringify(actual)), expected);

test('capability follows model flags', () => {
  same(R.reasoningCapabilityFor({ supportsThinking: true, supportsReasoningEffort: true }), {
    canThink: true, canSkipThinking: true, levels: ALL
  });
  // Codex 档位按 supportedReasoningLevels 过滤，未定义的档位丢弃
  same(R.reasoningCapabilityFor({
    supportsThinking: true, supportsReasoningEffort: true,
    supportedReasoningLevels: [{ effort: 'low' }, { effort: 'high' }, { effort: 'ultra' }]
  }).levels, ['low', 'high']);
  same(R.reasoningCapabilityFor({ supportsThinking: true }).levels, []);
  assert.equal(R.reasoningCapabilityFor({ fastOnly: true, supportsThinking: false }).canThink, false);
  assert.equal(R.reasoningCapabilityFor({ supportsThinking: true, thinkingOnly: true }).canSkipThinking, false);
  assert.equal(R.reasoningCapabilityFor(null).canThink, false);
});

test('stops: none on the left, levels or a single thinking stop on the right', () => {
  const stops = (cap) => JSON.parse(JSON.stringify(R.reasoningStops(cap)));
  assert.deepEqual(stops({ canThink: true, canSkipThinking: true, levels: ALL }), ['none', ...ALL]);
  assert.deepEqual(stops({ canThink: true, canSkipThinking: true, levels: [] }), ['none', 'on']);
  assert.deepEqual(stops({ canThink: true, canSkipThinking: false, levels: ['low', 'high'] }), ['low', 'high']);
  // 少于两档时弹窗不显示滑块
  assert.deepEqual(stops({ canThink: false, canSkipThinking: true, levels: [] }), ['none']);
  assert.deepEqual(stops({ canThink: true, canSkipThinking: false, levels: [] }), ['on']);
});

test('current stop maps run mode, effort and default memory', () => {
  const cap = { canThink: true, canSkipThinking: true, levels: ['low', 'medium', 'high', 'xhigh'] };
  assert.equal(R.currentReasoningStop(cap, false, 'high', 'high'), 'none');
  assert.equal(R.currentReasoningStop(cap, true, 'medium', 'high'), 'medium');
  // 默认推理（effort=null）：停在记忆档位
  assert.equal(R.currentReasoningStop(cap, true, null, 'xhigh'), 'xhigh');
  // 记忆档位被新模型裁掉：退到 high，再退到最高档
  assert.equal(R.currentReasoningStop(cap, true, null, 'max'), 'high');
  assert.equal(R.fallbackLevel(['low', 'medium'], 'max'), 'medium');
  assert.equal(R.currentReasoningStop({ canThink: true, canSkipThinking: true, levels: [] }, true, null, null), 'on');
  // thinkingOnly 不能停在「无」
  assert.equal(R.currentReasoningStop({ canThink: true, canSkipThinking: false, levels: ['low', 'high'] }, false, 'low', 'low'), 'low');
});

function agentHost(overrides = {}) {
  const calls = [];
  const { modeMethods } = compile('static/src/app/methods/ui/mode.ts', {
    '@/locales': locale,
    '../../../stores/model': { useModelStore: () => ({ currentModel: {} }) },
    '../../../stores/quickDock': { useQuickDockStore: () => ({}) },
    '../auxiliaryOwnership': { beginAuxiliaryRequest: () => () => true }
  });
  const host = {
    isConnected: true, streamingMessage: false, resolvedRunMode: 'fast', reasoningEffort: 'high',
    setReasoningEffort(value) { calls.push(['effort', value]); this.reasoningEffort = value; },
    async setRunMode(mode) { calls.push(['mode', mode]); this.resolvedRunMode = mode; },
    ...overrides
  };
  host.setHeaderReasoning = modeMethods.setHeaderReasoning.bind(host);
  return { host, calls };
}

test('agent: picking a level from none switches to thinking and sets effort once', async () => {
  const { host, calls } = agentHost();
  await host.setHeaderReasoning({ thinking: true, effort: 'max' });
  assert.deepEqual(calls, [['effort', 'max'], ['mode', 'thinking']]);
});

test('agent: none only switches run mode and keeps the effort memory', async () => {
  const { host, calls } = agentHost({ resolvedRunMode: 'thinking' });
  await host.setHeaderReasoning({ thinking: false });
  assert.deepEqual(calls, [['mode', 'fast']]);
  assert.equal(host.reasoningEffort, 'high');
});

test('agent: default toggle writes null without touching an unchanged run mode', async () => {
  const { host, calls } = agentHost({ resolvedRunMode: 'thinking' });
  await host.setHeaderReasoning({ thinking: true, effort: null });
  assert.deepEqual(calls, [['effort', null]]);
});

test('agent: locked while streaming or disconnected', async () => {
  for (const state of [{ streamingMessage: true }, { isConnected: false }]) {
    const { host, calls } = agentHost(state);
    await host.setHeaderReasoning({ thinking: true, effort: 'low' });
    assert.deepEqual(calls, []);
  }
});

(async () => {
  for (const [name, run] of cases) { await run(); console.log(`PASS ${name}`); }
  console.log(`${cases.length} reasoning picker groups passed`);
})().catch((error) => { console.error(error); process.exitCode = 1; });
