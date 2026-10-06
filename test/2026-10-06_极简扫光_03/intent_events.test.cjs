const assert = require('node:assert/strict');
const test = require('node:test');
const path = require('node:path');
const { createLoader, repo } = require('../2026-10-06_极简扫光_02/helpers.cjs');
const load = createLoader({
  '../common': { debugLog() {} },
  '../../../stores/personalization': {},
  '@/locales': { t: (key) => key }
});
const { toolMethods } = load(path.join(repo, 'static/src/app/methods/taskPolling/tool.ts'));

function harness() {
  const msg = { actions: [] };
  let updates = 0;
  const context = {
    ...toolMethods,
    preparingTools: new Map(),
    chatEnsureAssistantMessage: () => msg,
    toolFindAction: (id, preparingId) => msg.actions.find((a) => a.id === id || a.id === preparingId),
    toolRegisterAction() {}, toolTrackAction() {}, conditionalScrollToBottom() {},
    cloneToolArguments: (value) => ({ ...value }),
    buildToolLabel: () => '',
    $forceUpdate() { updates++; },
    _rebuildingFromScratch: true
  };
  return { context, msg, get updates() { return updates; } };
}

test('a closing quote releases the intent gate even if the text has not changed', () => {
  const h = harness();
  h.context.handleToolPreparing({ id: 'tool', name: 'write_file', intent: '写入文件', intent_complete: false });
  const tool = h.msg.actions[0].tool;
  assert.equal(tool.intent_complete, false);
  const updates = h.updates;
  h.context.handleToolIntent({ id: 'tool', intent: '写入文件', intent_complete: true });
  assert.equal(tool.intent_complete, true);
  assert.equal(tool.intent_full, '写入文件');
  assert.equal(h.updates, updates + 1);
  h.context.handleToolIntent({ id: 'tool', intent: '写入文件', intent_complete: true });
  assert.equal(h.updates, updates + 1);
});

test('a full intent received in the first chunk is immediately eligible for entry', () => {
  const h = harness();
  h.context.handleToolPreparing({ id: 'tool', name: 'write_file', intent: '完整', intent_complete: true });
  h.context.handleToolPreparing({ id: 'tool', name: 'write_file', intent: '完整', intent_complete: false });
  assert.equal(h.msg.actions.length, 1);
  assert.equal(h.msg.actions[0].tool.intent_complete, true);
});

test('tool_start carries the complete arguments when the earlier close event was missed', () => {
  const h = harness();
  h.context.handleToolPreparing({ id: 'preparing', name: 'write_file', intent: '部分' });
  h.context.handleToolStart({ id: 'execution', preparing_id: 'preparing', name: 'write_file',
    arguments: { intent: '完整意图', content: 'data' } });
  assert.equal(h.msg.actions.length, 1);
  assert.equal(h.msg.actions[0].tool.intent_full, '完整意图');
  assert.equal(h.msg.actions[0].tool.intent_complete, true);
  assert.equal(h.msg.actions[0].tool.status, 'running');
});

test('tool_start without a preparation event also marks arguments complete', () => {
  const h = harness();
  h.context.handleToolStart({ id: 'execution', name: 'read_file', arguments: { path: 'a.txt' } });
  assert.equal(h.msg.actions[0].tool.intent_complete, true);
  assert.equal(h.msg.actions[0].tool.arguments.path, 'a.txt');
});
