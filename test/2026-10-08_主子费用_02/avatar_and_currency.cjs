const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('../../node_modules/typescript');
const root = path.resolve(__dirname, '../..');
function compile(source) {
  return ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
  }).outputText;
}

const stores = { subAgents: [], commands: [], form: { tool_intent_enabled: true } };
function avatarRequire(name) {
  if (name === '@/locales') return { t: key => key, currentLocale: { value: 'zh-CN' } };
  if (name === 'pinia') return { mapState: () => ({}), mapWritableState: () => ({}) };
  if (name.includes('avatarFace')) return { toolFaceKey: name => name };
  if (name.includes('messageVisibility')) return { messageStartsWork: () => true };
  return new Proxy({}, { get: () => () => stores });
}
const avatarModule = {};
new Function('require', 'exports', compile(fs.readFileSync(path.join(root, 'static/src/app/computed.ts'), 'utf8')))(avatarRequire, avatarModule);
const avatar = avatarModule.computed.avatarStatus;
const tool = (text, status = 'completed') => ({ type: 'tool', tool: {
  name: 'read_file', status, intent_full: text, intent_rendered: text, intent_complete: true
} });
function host(actions, currentStreamingType = 'text') {
  return {
    messages: [{ role: 'assistant', actions, currentStreamingType, activeThinkingId: null }],
    taskInProgress: true, streamingMessage: true, apiRequestPending: false,
    stopRequested: false, preparingTools: new Map(), activeTools: new Map()
  };
}
function expectBlank(state) {
  const status = avatar.call(state);
  assert.equal(status.mode, 'work');
  assert.equal(status.text, '');
  assert.equal(status.intentRetained, undefined);
  assert.equal(status.apiWaiting, undefined);
}
expectBlank(host([tool('old intent'), { type: 'thinking', content: 'finished' }, { type: 'text', streaming: true, content: '' }]));
expectBlank(host([tool('old intent'), { type: 'text', streaming: true, content: 'body' }]));
expectBlank(host([tool('old intent'), { type: 'text', streaming: false, content: 'finished body' }], null));
// Explicit text phase also clears stale retained intent while the first body action mounts.
expectBlank(host([tool('old intent')]));
stores.commands = [{ status: 'running' }];
expectBlank(host([tool('old intent'), { type: 'text', streaming: true }]));
stores.commands = [];
const nextTool = host([tool('old intent'), { type: 'text', content: 'body', streaming: false }, tool('new intent', 'running')], null);
assert.equal(avatar.call(nextTool).text, 'new intent');
assert.equal(avatar.call(nextTool).mode, 'tool');
const waiting = host([tool('old intent'), { type: 'text', content: 'body', streaming: false }], null);
waiting.apiRequestPending = true;
assert.equal(avatar.call(waiting).apiWaiting, true);
assert.equal(avatar.call(host([tool('old intent')], null)).intentRetained, true);
console.log('Body-output status regressions passed (8 scenarios).');

// Execute the actual SFC script with reactive data and inert lifecycle hooks;
// verify the same formatter used by both the total and each actor row.
const sfc = fs.readFileSync(path.join(root, 'static/src/components/token/CostSummary.vue'), 'utf8');
const script = sfc.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
const props = { conversationId: 'synthetic', active: false, costs: {
  total_usd: '2', has_priced: true, partial: false, actors: [], exchange_rate: { usd_cny: 7 }
} };
const preference = { form: { display_currency: 'USD' } };
function costRequire(name) {
  if (name === 'vue') return {
    computed: fn => ({ get value() { return fn(); } }),
    ref: value => ({ value }), watch: () => {}, onBeforeUnmount: () => {}
  };
  if (name === '@/locales') return { t: key => key };
  if (name.includes('personalization')) return { usePersonalizationStore: () => preference };
  if (name.includes('resource')) return { useResourceStore: () => ({}) };
  throw new Error(`Unexpected import: ${name}`);
}
const costs = {};
new Function('require', 'exports', 'defineProps', compile(script) + '\nexports.money = money; exports.totalText = totalText; exports.actorAmount = actorAmount;')(
  costRequire, costs, () => props
);
assert.equal(costs.totalText.value, '$2.0000');
preference.form.display_currency = 'CNY';
assert.equal(costs.totalText.value, '¥14.0000');
assert.equal(costs.actorAmount({ priced_requests: 1, cost_usd: '1' }), '¥7.0000');
assert.equal(costs.money('0'), '¥0.0000');
props.costs.exchange_rate.usd_cny = undefined;
assert.equal(costs.totalText.value, '—');
preference.form.display_currency = 'USD';
assert.equal(costs.totalText.value, '$2.0000');
console.log('Currency display regressions passed (6 scenarios).');
