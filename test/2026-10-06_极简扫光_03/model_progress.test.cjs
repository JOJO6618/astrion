const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const { parse } = require('@vue/compiler-sfc');
const { createLoader, repo } = require('../2026-10-06_极简扫光_02/helpers.cjs');
const filename = path.join(repo, 'static/src/components/chat/MinimalBlocks.vue');
const script = parse(fs.readFileSync(filename, 'utf8')).descriptor.scriptSetup.content;
const tree = ts.createSourceFile(filename, script, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
const names = ['getFirstLine', 'getToolSummaryText', 'isActiveToolAction', 'getToolBatchKey',
  'isSummaryIntentReady', 'getSummaryToolItems', 'isSummaryRunning', 'getSummarySweepInput'];
const declarations = tree.statements.filter((node) => ts.isVariableStatement(node) &&
  node.declarationList.declarations.some((declaration) => names.includes(declaration.name.getText(tree))));
assert.equal(declarations.length, names.length);
const source = declarations.map((node) => node.getText(tree)).join('\n') +
  '\nreturn { isSummaryRunning, getSummarySweepInput };';
const compiled = ts.transpileModule(source, { compilerOptions: {
  target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS
} }).outputText;
const { getLatestToolBatch } = createLoader()(path.join(repo, 'static/src/components/chat/toolSummaryBatch.ts'));

function harness(actions, all = actions) {
  const props = { actions: all, conversationRunning: true, isLatestMessage: true };
  const group = { id: 'group', type: 'summary', actions };
  return new Function('props', 'blockGroups', 'personalizationStore', 'currentLocale',
    't', 'getLatestToolSegment', 'getCompletedSummaryText', compiled)(
    props, { value: [group] }, { form: { tool_intent_enabled: true } }, { value: 'zh-CN' },
    (key) => key, getLatestToolBatch, () => 'completed summary');
}
const tool = (id, complete = true) => ({ id, type: 'tool', toolBatchId: 'batch',
  tool: { name: 'write_file', intent_full: `intent ${id}`, status: 'preparing', intent_complete: complete } });

test('a text_start empty action already settles the preceding summary', () => {
  const action = tool('a');
  const api = harness([action], [action, { id: 'text', type: 'text', content: '', streaming: true }]);
  assert.equal(api.isSummaryRunning([action], 'group'), false);
  const input = api.getSummarySweepInput([action], 'group');
  assert.equal(input.animate, false);
  assert.equal(input.kind, 'static');
});

test('a later same-batch tool does not interrupt the first entry', () => {
  const actions = [tool('a'), tool('b', false)];
  const input = harness(actions).getSummarySweepInput(actions, 'group');
  assert.notEqual(input.forceComplete, true);
  assert.equal(input.identity, 'batch:tools');
  assert.equal(input.text, 'intent a');
  assert.equal(input.ready, true);
});

test('a delayed first intent stays gated, then receives its full entry before parallel rolling', () => {
  const actions = [tool('a', false), tool('b')];
  const api = harness(actions);
  assert.equal(api.getSummarySweepInput(actions, 'group').ready, false);
  actions[0].tool.intent_complete = true;
  const input = api.getSummarySweepInput(actions, 'group');
  assert.equal(input.ready, true);
  assert.equal(input.animate, true);
  assert.notEqual(input.forceComplete, true);
});

test('tool_start and historical arguments can supply a full intent without rendered typing', () => {
  const action = tool('a');
  action.tool = { name: 'write_file', status: 'running', arguments: { intent: 'full from arguments' } };
  const input = harness([action]).getSummarySweepInput([action], 'group');
  assert.equal(input.text, 'full from arguments');
  assert.equal(input.ready, true);
});

test('a new thinking action after tools selects thinking rather than the stale batch', () => {
  const actions = [tool('a'), { id: 'thinking-next', type: 'thinking', content: 'first\nsecond', streaming: true }];
  const input = harness(actions).getSummarySweepInput(actions, 'group');
  assert.equal(input.kind, 'thinking');
  assert.equal(input.text, 'first');
  assert.equal(input.sweeping, true);
  assert.equal(input.forceComplete, false);
});
