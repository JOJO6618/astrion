import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { parse, compileScript } from '@vue/compiler-sfc';
import { build } from 'esbuild';

// Exercise the real SFC setup and Vue watchers without a browser or screenshot.
const source = await fs.readFile('static/src/components/avatar/StatusAvatar.vue', 'utf8');
const { descriptor } = parse(source);
const script = compileScript(descriptor, { id: 'avatar-state-regression' });
const bundled = await build({ stdin: { contents: `${script.content}\nexport { createRenderer, nextTick } from 'vue';`, loader: 'ts', resolveDir: process.cwd() },
  bundle: true, write: false, format: 'esm', platform: 'node',
  plugins: [{ name: 'avatar-source-alias', setup(builder) {
    builder.onResolve({ filter: /^@\// }, args => ({ path: path.resolve('static/src', args.path.slice(2) + '.ts') }));
  } }] });
const { default: Avatar, createRenderer, nextTick } = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString('base64')}`);

test('pointer updates with identical tool-key content do not reinitialize idle eye tracking', async () => {
  let idleTimers = 0;
  globalThis.window = { setTimeout: () => ++idleTimers, clearTimeout() {} };
  globalThis.document = { addEventListener() {}, removeEventListener() {} };
  const noop = () => {};
  const renderer = createRenderer({ patchProp: noop, insert: noop, remove: noop, createElement: () => ({}), createText: () => ({}),
    createComment: () => ({}), setText: noop, setElementText: noop, parentNode: () => null, nextSibling: () => null });
  const app = renderer.createApp({ ...Avatar, render: () => null }, { mode: 'idle', toolKeys: [], tracking: true, pointer: { x: 100, y: 100 } });
  app.mount({});
  try {
    const initial = idleTimers;
    assert.equal(initial, 1);
    for (let index = 0; index < 6; index++) {
      app._instance.props.pointer = { x: 100 + index * 10, y: 100 };
      // The quick-window template produces a new array on every pointer render.
      app._instance.props.toolKeys = [];
      await nextTick();
    }
    assert.equal(idleTimers, initial, 'Mouse movement must not restart idle state or reset the eyes');
    app._instance.props.mode = 'work'; await nextTick();
    app._instance.props.mode = 'idle'; await nextTick();
    assert.equal(idleTimers, initial + 1, 'A genuine mode change must still initialize the new state');
  } finally { app.unmount(); delete globalThis.window; delete globalThis.document; }
});
