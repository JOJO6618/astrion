const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const test = require('node:test');
const { parse } = require('@vue/compiler-sfc');
const ts = require('typescript');
const { computed, reactive } = require('vue');

test('an empty streaming text action only separates steps when visible text arrives', () => {
  const file = join(__dirname, '../../static/src/components/chat/MinimalBlocks.vue');
  const script = parse(readFileSync(file, 'utf8')).descriptor.scriptSetup.content;
  const tree = ts.createSourceFile(file, script, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const groups = tree.statements.find(node => ts.isVariableStatement(node) &&
    node.declarationList.declarations.some(decl => decl.name.getText(tree) === 'blockGroups'));
  const js = ts.transpile(groups.getText(tree));
  const props = reactive({ actions: [
    { id: 's1', type: 'thinking' }, { id: 'text', type: 'text', content: '', streaming: true },
    { id: 't1', type: 'tool' }, { id: 's2', type: 'thinking' }, { id: 't2', type: 'tool' }
  ] });
  const blocks = new Function('computed', 'props', js + '\nreturn blockGroups;')(computed, props);
  for (const blank of ['', '  \n']) {
    props.actions[1].content = blank;
    assert.equal(blocks.value.length, 1);
    assert.deepEqual(blocks.value[0].actions.map(step => step.id), ['s1', 't1', 's2', 't2']);
  }
  props.actions[1].content += '进度说明';
  assert.deepEqual(blocks.value.map(group => group.type), ['summary', 'text', 'summary']);
});
