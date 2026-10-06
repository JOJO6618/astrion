const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const { parse, compileScript, registerTS } = require('@vue/compiler-sfc');
const vue = require('vue');
const { repo } = require('../2026-10-06_极简扫光_02/helpers.cjs');
registerTS(() => ts);

function harness() {
  let lifecycle;
  const filename = path.join(repo, 'static/src/components/chat/SummarySweepText.vue');
  const source = fs.readFileSync(filename, 'utf8');
  const script = compileScript(parse(source, { filename }).descriptor, {
    id: 'summary-test', inlineTemplate: true,
    fs: { fileExists: fs.existsSync, readFile: (file) => fs.readFileSync(file, 'utf8') }
  }).content;
  const compiled = ts.transpileModule(script, {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS }
  }).outputText;
  const module = { exports: {} };
  new Function('module', 'exports', 'require', compiled)(module, module.exports, (name) => {
    if (name === 'vue') return vue;
    if (name === '@/composables/useSummarySweep') return {
      useSummarySweep(root, content, base, incoming, beam, getInput, settled, adopt) {
        lifecycle = { getInput, settled, adopt };
      }
    };
    if (name === './SummaryToolReel.vue') return { default: vue.defineComponent({
      props: ['items', 'offsetPx', 'phase'],
      setup(props) { return () => vue.h('span', { 'data-reel': true }, props.items.join('|')); }
    }) };
    throw new Error(`Unexpected dependency: ${name}`);
  });
  const node = (type, text = '') => ({ type, text, props: {}, children: [], parent: null });
  const renderer = vue.createRenderer({
    createElement: node,
    createText: (text) => node('#text', text),
    createComment: (text) => node('#comment', text),
    patchProp: (element, key, old, next) => { element.props[key] = next; },
    setText: (element, text) => { element.text = text; },
    setElementText: (element, text) => { element.text = text; element.children = []; },
    parentNode: (element) => element.parent,
    nextSibling(element) {
      const siblings = element.parent?.children || [];
      return siblings[siblings.indexOf(element) + 1] || null;
    },
    insert(element, parent, anchor) {
      if (element.parent) element.parent.children = element.parent.children.filter((item) => item !== element);
      element.parent = parent;
      const index = anchor ? parent.children.indexOf(anchor) : -1;
      parent.children.splice(index < 0 ? parent.children.length : index, 0, element);
    },
    remove(element) {
      if (element.parent) element.parent.children = element.parent.children.filter((item) => item !== element);
      element.parent = null;
    }
  });
  const props = vue.shallowReactive({ text: 'first', identity: 'batch:tools', kind: 'tool',
    animate: true, sweeping: false, reelView: undefined });
  const root = node('root');
  const app = renderer.createApp({ setup: () => () => vue.h(module.exports.default, { ...props }) });
  app.mount(root);
  const reelNodes = () => {
    const visit = (element) => [element, ...element.children.flatMap(visit)];
    return visit(root).filter((element) => element.props['data-reel']);
  };
  return { props, lifecycle, app, reelNodes };
}

test('the actual Vue wrapper attaches the reel only after the entry settles', async () => {
  const h = harness();
  h.props.reelView = { items: ['first', 'second'], offsetPx: 0, phase: 'idle' };
  await vue.nextTick();
  assert.equal(h.reelNodes().length, 0);
  h.lifecycle.settled(h.lifecycle.getInput());
  h.props.reelView = { ...h.props.reelView };
  await vue.nextTick();
  assert.equal(h.reelNodes()[0].text, 'first|second');
  h.app.unmount();
});

test('outgoing reel stays rendered until adoption, then cannot cover new thinking or static text', async () => {
  const h = harness();
  h.lifecycle.settled(h.lifecycle.getInput());
  h.props.reelView = { items: ['first', 'second'], offsetPx: -26, phase: 'idle' };
  await vue.nextTick();
  h.props.identity = 'thinking';
  h.props.kind = 'thinking';
  h.props.text = 'next thinking';
  h.props.reelView = undefined;
  await vue.nextTick();
  assert.equal(h.reelNodes().length, 1);
  h.lifecycle.adopt();
  await vue.nextTick();
  assert.equal(h.reelNodes().length, 0);
  h.lifecycle.settled(h.lifecycle.getInput());
  h.props.reelView = { items: ['stale'], offsetPx: 0, phase: 'settle' };
  await vue.nextTick();
  assert.equal(h.reelNodes().length, 0);
  h.props.kind = 'static';
  h.props.reelView = { ...h.props.reelView };
  await vue.nextTick();
  assert.equal(h.reelNodes().length, 0);
  h.app.unmount();
});
