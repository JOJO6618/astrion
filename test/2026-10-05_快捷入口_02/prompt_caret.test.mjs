import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { parse, compileScript } from '@vue/compiler-sfc';
import { build } from 'esbuild';

const { descriptor } = parse(await fs.readFile('static/src/quick/QuickPrompt.vue', 'utf8'));
const script = compileScript(descriptor, { id: 'quick-prompt-regression' });
const bundled = await build({ stdin: { contents: `${script.content}\nexport { createRenderer, nextTick } from 'vue';`, loader: 'ts', resolveDir: process.cwd() },
  bundle: true, write: false, format: 'esm', platform: 'node', plugins: [{ name: 'locales', setup(builder) {
    builder.onResolve({ filter: /^@\/locales$/ }, () => ({ path: 'locales', namespace: 'mock' }));
    builder.onLoad({ filter: /.*/, namespace: 'mock' }, () => ({ contents: 'export const t = key => key;' }));
  } }] });
const { default: Prompt, createRenderer } = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString('base64')}`);
function fixture(context) {
  globalThis.ResizeObserver = class { observe() {} disconnect() {} };
  globalThis.document = { addEventListener() {}, removeEventListener() {} };
  const noop = () => {};
  const renderer = createRenderer({ patchProp: noop, insert: noop, remove: noop, createElement: () => ({}), createText: () => ({}), createComment: () => ({}), setText: noop, setElementText: noop, parentNode: () => null, nextSibling: () => null });
  let submits = 0;
  const app = renderer.createApp({ ...Prompt, render: () => null }, { modelValue: '', onSubmit: () => submits++ });
  app.mount({});
  const setup = app._instance.setupState;
  const input = { selectionStart: 0, selectionEnd: 0, scrollLeft: 0, scrollTop: 0, clientWidth: 200, clientHeight: 31 };
  const marker = { offsetLeft: 0, offsetTop: 12, offsetHeight: 21 };
  setup.input = input; setup.marker = marker;
  context.after(() => { app.unmount(); delete globalThis.ResizeObserver; delete globalThis.document; });
  return { setup, input, marker, app, submits: () => submits };
}

test('empty prompt caret uses exactly the placeholder starting column', async context => {
  const { setup } = fixture(context);
  await setup.syncCaret();
  assert.equal(setup.caret.left, 0);
  assert.equal(setup.caret.top, 5, 'Caret must center in the line, independent of mirror baseline');
  assert.equal(setup.caret.visible, true);
});
test('typed and scrolled text use the measured caret without added gaps', async context => {
  const { setup, input, marker, app } = fixture(context);
  app._instance.props.modelValue = 'Astrion'; input.selectionStart = input.selectionEnd = 7;
  marker.offsetLeft = 103; await setup.syncCaret();
  assert.equal(setup.prefix, 'Astrion'); assert.equal(setup.caret.left, 103);
  input.scrollLeft = 60; await setup.syncCaret(); assert.equal(setup.caret.left, 43);
  input.selectionStart = input.selectionEnd = 2; marker.offsetLeft = 31; input.scrollLeft = 0;
  await setup.syncCaret(); assert.equal(setup.prefix, 'As'); assert.equal(setup.suffix, 'trion'); assert.equal(setup.caret.left, 31);
});
test('a scrolled second line retains the same centered caret height', async context => {
  const { setup, input, marker, app } = fixture(context);
  app._instance.props.modelValue = 'first\nsecond'; input.selectionStart = input.selectionEnd = 12;
  marker.offsetTop = 43;
  input.scrollTop = 31;
  await setup.syncCaret();
  assert.equal(setup.caret.top, 5);
  assert.equal(setup.caret.visible, true);
});
test('selected text and an offscreen caret are not shown as an insertion cursor', async context => {
  const { setup, input, marker } = fixture(context);
  input.selectionStart = 0; input.selectionEnd = 2; await setup.syncCaret(); assert.equal(setup.selected, true);
  marker.offsetLeft = 250; await setup.syncCaret(); assert.equal(setup.caret.visible, false);
});
test('Enter submits once while Shift+Enter and Chinese IME events do not submit', context => {
  const { setup, submits } = fixture(context);
  let prevented = 0;
  const key = { key: 'Enter', shiftKey: false, isComposing: false, keyCode: 13, preventDefault: () => prevented++ };
  setup.keydown(key); assert.equal(submits(), 1); assert.equal(prevented, 1);
  setup.keydown({ ...key, shiftKey: true });
  setup.keydown({ ...key, isComposing: true });
  setup.keydown({ ...key, keyCode: 229 });
  setup.composing = true; setup.keydown(key);
  assert.equal(submits(), 1); assert.equal(prevented, 1);
});
