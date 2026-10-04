import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { parse, compileScript } from '@vue/compiler-sfc';
import { build } from 'esbuild';

const { descriptor } = parse(await fs.readFile('static/src/quick/QuickPrompt.vue', 'utf8'));
const script = compileScript(descriptor, { id: 'quick-multiline-regression' });
const bundled = await build({ stdin: { contents: `${script.content}\nexport { createRenderer } from 'vue';`, loader: 'ts', resolveDir: process.cwd() },
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
  const app = renderer.createApp({ ...Prompt, render: () => null }, { modelValue: '' });
  app.mount({});
  const setup = app._instance.setupState;
  const input = { selectionStart: 0, selectionEnd: 0, scrollLeft: 0, scrollTop: 0, clientWidth: 220, clientHeight: 31 };
  const mirror = { style: {}, scrollHeight: 31 }, marker = { offsetLeft: 0, offsetTop: 12, offsetHeight: 21 };
  setup.input = input; setup.mirror = mirror; setup.marker = marker;
  context.after(() => { app.unmount(); delete globalThis.ResizeObserver; delete globalThis.document; });
  return { setup, input, mirror, marker, app };
}

test('input grows one to five lines and stops growing after the fifth', async context => {
  const { setup, mirror } = fixture(context);
  for (const lines of [1, 2, 3, 5, 6, 10]) {
    mirror.scrollHeight = lines * 31;
    await setup.syncCaret();
    assert.equal(setup.inputHeight, Math.min(lines, 5) * 31);
    assert.equal(mirror.style.width, '220px');
  }
  mirror.scrollHeight = 31; await setup.syncCaret();
  assert.equal(setup.inputHeight, 31, 'Deleting text restores the single-line input');
});
test('soft-wrapped text positions the caret on the measured second line', async context => {
  const { setup, input, mirror, marker, app } = fixture(context);
  app._instance.props.modelValue = 'long text without explicit newline';
  input.selectionStart = input.selectionEnd = 20; input.clientHeight = 62;
  mirror.scrollHeight = 62; marker.offsetLeft = 40; marker.offsetTop = 43;
  await setup.syncCaret();
  assert.equal(setup.inputHeight, 62);
  assert.equal(setup.caret.top, 36);
  assert.equal(setup.caret.left, 40);
  assert.equal(setup.caret.visible, true);
});
test('scrolling beyond five lines keeps the caret centered within the visible line', async context => {
  const { setup, input, mirror, marker } = fixture(context);
  mirror.scrollHeight = 8 * 31; input.clientHeight = 5 * 31;
  marker.offsetTop = 7 * 31 + 12; marker.offsetLeft = 32; input.scrollTop = 3 * 31;
  await setup.syncCaret();
  assert.equal(setup.inputHeight, 155);
  assert.equal(setup.caret.top, 129);
  assert.equal(setup.caret.visible, true);
  input.scrollTop = 0; await setup.syncCaret();
  assert.equal(setup.caret.visible, false);
});
