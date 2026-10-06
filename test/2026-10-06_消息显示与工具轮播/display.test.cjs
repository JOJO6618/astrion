const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('../../node_modules/typescript');
const root = path.resolve(__dirname, '../..');

function compile(source, context) {
  vm.runInContext(ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS }
  }).outputText, context);
}

function loadModule(relative) {
  const context = vm.createContext({ exports: {} });
  compile(fs.readFileSync(path.join(root, relative), 'utf8'), context);
  return context.exports;
}

// Exercise the production functions without mounting the visual component.
function componentFunctions(relative, names, bindings) {
  const source = fs.readFileSync(path.join(root, relative), 'utf8')
    .split('<script setup lang="ts">')[1].split('</script>')[0];
  const ast = ts.createSourceFile(relative, source, ts.ScriptTarget.Latest, true);
  const nodes = ast.statements.filter(node => {
    if (ts.isFunctionDeclaration(node)) return names.includes(node.name?.text);
    return ts.isVariableStatement(node) && node.declarationList.declarations
      .some(decl => names.includes(decl.name.getText(ast)));
  });
  assert.equal(nodes.length, names.length);
  const context = vm.createContext(bindings);
  compile(nodes.map(node => node.getText(ast)).join('\n') +
    '\nthis.api = {' + names.join(',') + '};', context);
  return context.api;
}

const { getLatestToolBatch } = loadModule('static/src/components/chat/toolSummaryBatch.ts');
function tool(id, batch, status = 'running') {
  return { id, type: 'tool', toolBatchId: batch, tool: { name: id, status } };
}

test('new request without reasoning excludes completed previous batch', () => {
  const a = tool('a', 'first', 'completed');
  const b = tool('b', 'second');
  assert.deepEqual(Array.from(getLatestToolBatch([a, b]), item => item.id), ['b']);
});

test('partially completed parallel batch retains every member', () => {
  const actions = [tool('a', 'first', 'completed'), tool('b', 'second', 'completed'), tool('c', 'second')];
  assert.deepEqual(Array.from(getLatestToolBatch(actions), item => item.id), ['b', 'c']);
  actions[2].tool.status = 'completed';
  assert.deepEqual(Array.from(getLatestToolBatch(actions), item => item.id), ['b', 'c']);
});

test('history response batches and reasoning boundaries remain distinct', () => {
  assert.equal(getLatestToolBatch([tool('a', 'history-a'), tool('b', 'history-b')]).length, 1);
  assert.equal(getLatestToolBatch([tool('a', 'first'), { type: 'thinking' }]).length, 0);
});

function reelFixture() {
  const states = {};
  const frames = [];
  const timers = new Map();
  let timerId = 0;
  const bindings = {
    getLatestToolSegment: getLatestToolBatch,
    getToolSummaryText: action => action.tool.name,
    isSummaryRunning: () => true,
    toolReelStates: states,
    toolReelTimeouts: new Map(), toolReelIntervals: new Map(),
    blockGroups: { value: [{ id: 'group', type: 'summary', actions: [] }] },
    TOOL_REEL_ITEM_HEIGHT: 26, TOOL_REEL_INTERVAL_MS: 1450,
    TOOL_REEL_ROLL_MS: 520, TOOL_REEL_SETTLE_MS: 170, TOOL_REEL_OVERSHOOT_PX: 2,
    window: {
      setTimeout(fn) { timers.set(++timerId, fn); return timerId; },
      clearTimeout(id) { timers.delete(id); },
      setInterval() { return ++timerId; }, clearInterval() {},
      requestAnimationFrame(fn) { frames.push(fn); }
    }
  };
  const names = ['isActiveToolAction', 'getToolBatchKey', 'getLatestActiveToolSegment',
    'getToolReelItems', 'shouldShowToolReel', 'clearToolReelTimers',
    'pushToolReelTimeout', 'spinToolReel', 'finishToolReel', 'syncToolReels'];
  const api = componentFunctions('static/src/components/chat/MinimalBlocks.vue', names, bindings);
  return { states, frames, timers, api, group: bindings.blockGroups.value[0] };
}

test('parallel tools rotate until entire batch finishes, then settle on last tool', () => {
  const f = reelFixture();
  f.group.actions = [tool('b', 'bc'), tool('c', 'bc')];
  f.api.syncToolReels();
  assert.deepEqual(Array.from(f.states.group.items), ['b', 'c']);
  f.group.actions[0].tool.status = 'completed';
  f.api.syncToolReels();
  assert.deepEqual(Array.from(f.states.group.items), ['b', 'c']);
  assert.equal(f.states.group.completing, undefined);
  f.group.actions[1].tool.status = 'completed';
  f.api.syncToolReels();
  assert.equal(f.states.group.completing, true);
  f.api.syncToolReels();
  assert.deepEqual(Array.from(f.states.group.items), ['b', 'c']);
  f.frames.shift()();
  const callbacks = Array.from(f.timers.values());
  callbacks[0]();
  assert.equal(f.states.group.index, 1);
  callbacks[1]();
  assert.equal(f.states.group, undefined);
});

test('next single tool cancels previous batch completion and stale animation frame', () => {
  const f = reelFixture();
  f.group.actions = [tool('b', 'bc'), tool('c', 'bc')];
  f.api.syncToolReels();
  f.group.actions.forEach(action => { action.tool.status = 'completed'; });
  f.api.syncToolReels();
  f.group.actions.push(tool('d', 'd'));
  f.api.syncToolReels();
  assert.equal(f.states.group, undefined);
  f.frames.shift()();
  assert.equal(f.timers.size, 0);
});

const visibility = loadModule('static/src/utils/messageVisibility.ts');
const preference = { value: 'hidden' };
const hidden = componentFunctions('static/src/components/chat/ChatArea.vue',
  ['isMultiAgentMessage', 'isHiddenUserMessage'], {
    getMessageVisibility: visibility.getMessageVisibility,
    compactMessageDisplay: preference
  });

test('hide notices and guidance, retain user messages and Team Leader communication', () => {
  for (const source of ['guidance', 'compression', 'compression_handoff', 'sub_agent',
    'background_command', 'goal_review', 'workflow', 'notify']) {
    assert.equal(hidden.isHiddenUserMessage({ role: 'user', metadata: {
      message_source: source, visibility: 'chat'
    }}), true, source);
  }
  for (const source of ['user', 'presend']) {
    assert.equal(hidden.isHiddenUserMessage({ role: 'user', metadata: { message_source: source } }), false);
  }
  assert.equal(hidden.isHiddenUserMessage({ role: 'user', metadata: {
    message_source: 'sub_agent', visibility: 'chat', auto_message_type: 'multi_agent_output'
  }}), false);
  assert.equal(hidden.isHiddenUserMessage({ role: 'assistant' }), false);
  preference.value = 'full';
  assert.equal(hidden.isHiddenUserMessage({ role: 'user', metadata: { message_source: 'guidance' } }), false);
});
