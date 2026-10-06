const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('../../node_modules/typescript');
const root = path.resolve(__dirname, '../..');
const computed = getter => ({ get value() { return getter(); } });
function evaluate(source, bindings = {}) {
  const context = vm.createContext({ exports: {}, ...bindings });
  vm.runInContext(ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS }
  }).outputText, context);
  return context;
}
function load(relative) {
  return evaluate(fs.readFileSync(path.join(root, relative), 'utf8')).exports;
}
function functions(relative, names, bindings) {
  const source = fs.readFileSync(path.join(root, relative), 'utf8')
    .split('<script setup lang="ts">')[1].split('</script>')[0];
  const ast = ts.createSourceFile(relative, source, ts.ScriptTarget.Latest, true);
  const nodes = ast.statements.filter(node => {
    if (ts.isFunctionDeclaration(node)) return names.includes(node.name?.text);
    return ts.isVariableStatement(node) && node.declarationList.declarations
      .some(decl => names.includes(decl.name.getText(ast)));
  });
  assert.equal(nodes.length, names.length);
  return evaluate(nodes.map(node => node.getText(ast)).join('\n') +
    '\nthis.api = {' + names.join(',') + '};', bindings).api;
}
const { mergeAssistantDisplayRuns } = load('static/src/components/chat/assistantDisplayRuns.ts');
const { getLatestToolBatch } = load('static/src/components/chat/toolSummaryBatch.ts');
const { getMessageVisibility } = load('static/src/utils/messageVisibility.ts');
const assistant = (id, actions, extra = {}) => ({ role: 'assistant', id, actions, ...extra });
const tool = id => ({ id, type: 'tool', toolBatchId: id, tool: { name: id } });
const user = source => ({ role: 'user', metadata: { message_source: source } });
function fixture(messages) {
  const props = { messages };
  const preference = { value: 'hidden' };
  const api = functions('static/src/components/chat/ChatArea.vue', [
    'isMultiAgentMessage', 'isHiddenUserMessage', 'isEmptyAssistantMessage',
    'filteredMessages', 'citationsForMessage', 'citationsFinalForMessage',
    'messageKeyCache', 'messageKeySeq', 'getMessageKey',
    'isStackable', 'isEmptyTextAction', 'splitActionGroups'
  ], {
    props, compactMessageDisplay: preference, computed, mergeAssistantDisplayRuns,
    getMessageVisibility, isActionVisible: () => true, userMDebug: () => {},
    collectedCitations: { value: [] }
  });
  return { api, preference, props };
}

test('hidden guidance and compression join both minimal and stacked steps', () => {
  const a = tool('a'); const b = tool('b'); const c = tool('c');
  const originals = [user('user'), assistant('first', [a]), user('guidance'),
    assistant('second', [b]), user('compression'), assistant('third', [c])];
  const f = fixture(originals);
  const result = f.api.filteredMessages.value;
  assert.equal(result.length, 2);
  assert.deepEqual(Array.from(result[1].actions, action => action.id), ['a', 'b', 'c']);
  const minimal = functions('static/src/components/chat/MinimalBlocks.vue', ['blockGroups'], {
    props: { actions: result[1].actions }, computed
  });
  assert.equal(minimal.blockGroups.value.length, 1);
  assert.equal(minimal.blockGroups.value[0].actions.length, 3);
  const stacks = f.api.splitActionGroups(result[1].actions, 1);
  assert.equal(stacks.length, 1);
  assert.equal(stacks[0].actions.length, 3);
  assert.equal(getLatestToolBatch(result[1].actions).length, 1);
  assert.equal(originals.length, 6);
  assert.equal(originals[1].actions.length, 1);
  assert.strictEqual(result[1].actions[0], a);
  f.preference.value = 'full';
  assert.equal(f.api.filteredMessages.value.length, 6);
  f.preference.value = 'brief';
  assert.equal(f.api.filteredMessages.value.length, 6);
});

test('normal users and Team Leader communication retain message boundaries', () => {
  const team = user('sub_agent');
  team.metadata.auto_message_type = 'multi_agent_output';
  team.metadata.visibility = 'chat';
  for (const boundary of [user('user'), user('presend'), team]) {
    const f = fixture([assistant('first', [tool('a')]), boundary, assistant('last', [tool('b')])]);
    assert.equal(f.api.filteredMessages.value.length, 3);
  }
});

test('body text retains its position and separates tool groups after merging', () => {
  const text = { id: 'text', type: 'text', content: 'answer' };
  const f = fixture([assistant('first', [tool('a'), text]), user('guidance'), assistant('last', [tool('b')])]);
  const actions = f.api.filteredMessages.value[0].actions;
  const minimal = functions('static/src/components/chat/MinimalBlocks.vue', ['blockGroups'], {
    props: { actions }, computed
  });
  assert.deepEqual(Array.from(minimal.blockGroups.value, group => group.type), ['summary', 'text', 'summary']);
  assert.deepEqual(Array.from(f.api.splitActionGroups(actions), group => group.kind), ['stack', 'single', 'stack']);
  assert.strictEqual(actions[1], text);
});

test('merged list identity is stable and streaming state follows latest source', () => {
  const first = assistant('first', [tool('a')], { streaming: false });
  const last = assistant('last', [tool('b')], { streaming: true, currentStreamingType: 'tool' });
  const f = fixture([first]);
  const key = f.api.getMessageKey(f.api.filteredMessages.value[0], 0);
  f.props.messages.push(user('guidance'), last);
  let merged = f.api.filteredMessages.value[0];
  assert.equal(f.api.getMessageKey(merged, 0), key);
  assert.equal(merged.streaming, true);
  last.actions.push(tool('c'));
  last.streaming = false;
  last.currentStreamingType = null;
  merged = f.api.filteredMessages.value[0];
  assert.equal(f.api.getMessageKey(merged, 0), key);
  assert.equal(merged.actions.length, 3);
  assert.equal(merged.streaming, false);
  assert.equal(merged.currentStreamingType, null);
});

test('merged citations retain both segments and finalize only when all are final', () => {
  const citation = id => ({ id, type: 'url_citation', url: `https://example.com/${id}` });
  const first = assistant('first', [tool('a')], { metadata: { citations: [citation('a')] } });
  const last = assistant('last', [tool('b')], { metadata: {} });
  const f = fixture([first, user('compression'), last]);
  let merged = f.api.filteredMessages.value[0];
  assert.equal(f.api.citationsFinalForMessage(merged), false);
  assert.deepEqual(Array.from(f.api.citationsForMessage(merged), item => item.id), ['a']);
  last.metadata.citations = [citation('b')];
  merged = f.api.filteredMessages.value[0];
  assert.equal(f.api.citationsFinalForMessage(merged), true);
  assert.deepEqual(Array.from(f.api.citationsForMessage(merged), item => item.id), ['a', 'b']);
  last.metadata.citations = [];
  assert.deepEqual(Array.from(f.api.citationsForMessage(f.api.filteredMessages.value[0]), item => item.id), ['a']);
});
