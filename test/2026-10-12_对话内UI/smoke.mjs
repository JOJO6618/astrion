// Small offline checks only; layout and visual acceptance remain manual.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import { build } from 'esbuild';

const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
async function load(entry) {
  const result = await build({
    entryPoints: [path.join(repository, entry)], bundle: true, write: false,
    platform: 'node', format: 'esm', minify: true
  });
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`);
}
const { splitInlineUi } = await load('static/src/utils/inlineUi/parser.ts');
const ui = '<show_ui js="on"><div>一</div><script>globalThis.example = 1;</script></show_ui>';
const parts = splitInlineUi(`before ${ui} after`);
assert.deepEqual(parts.map((part) => part.type), ['text', 'ui', 'text']);
assert.equal(parts[1].closed, true);
assert.equal(parts[1].js, 'on');
assert.equal(parts[1].start, 7);
for (const source of [`\`${ui}\``, `\`\`\`html\n${ui}\n\`\`\``, `~~~html\n${ui}\n~~~`, `<show_html>${ui}</show_html>`]) {
  assert.equal(splitInlineUi(source).some((part) => part.type === 'ui'), false);
}
assert.equal(splitInlineUi('<show_ui js="on"><p>partial')[0].closed, false);
assert.equal(splitInlineUi('<show_ui js="on"')[0].content, '');
assert.equal(splitInlineUi(`${ui}\n${ui}`).filter((part) => part.type === 'ui').length, 2);
const growing = splitInlineUi(`before ${ui} additional text`);
assert.equal(growing[1].start, parts[1].start);

const { buildInlineUiSandbox } = await load('static/src/utils/inlineUi/sandbox.ts');
const document = buildInlineUiSandbox('smoke');
assert.match(document, /connect-src 'none'/);
assert.match(document, /background: transparent/);
const scripts = [...document.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((match) => match[1]);
assert.equal(scripts.length, 2);
for (const source of scripts) new vm.Script(source);

// Exercise the lifecycle without starting a browser or touching a real conversation.
const { INLINE_UI_FRAME_RUNTIME } = await load('static/src/utils/inlineUi/frameRuntime.ts');
const events = [];
const listeners = new Map();
const root = { inert: true, setAttribute() {}, removeAttribute() {}, getBoundingClientRect: () => ({ height: 24 }), querySelector: () => null };
const parent = { postMessage: (message) => events.push(message) };
let executions = 0;
const context = {
  INSTANCE: 'smoke', parent, patch() {},
  requestAnimationFrame: (callback) => callback(),
  setTimeout, clearTimeout, CustomEvent: class {},
  ResizeObserver: class { observe() {} },
  window: { addEventListener: (type, callback) => listeners.set(type, callback), dispatchEvent() {} },
  document: {
    getElementById: () => root,
    documentElement: { style: { setProperty() {} }, dataset: {} },
    body: { style: {} },
    head: { appendChild: () => { executions++; } },
    createElement: (tag) => tag === 'template' ? { content: {}, innerHTML: '' } : { setAttribute() {}, remove() {} },
    addEventListener() {}
  }
};
vm.runInNewContext(INLINE_UI_FRAME_RUNTIME, context);
const deliver = (sequence, closed) => listeners.get('message')({
  source: parent,
  data: { channel: 'astrion-inline-ui', instance: 'smoke', type: 'content', sequence, closed, html: '<p>example</p>', handlers: [], scripts: [{ attributes: {}, content: 'example' }] }
});
deliver(1, false);
assert.equal(executions, 0);
assert.equal(root.inert, true);
deliver(2, true);
await new Promise((resolve) => setImmediate(resolve));
assert.equal(executions, 1);
assert.equal(root.inert, false);
assert.ok(events.some((event) => event.type === 'committed'));
deliver(3, true);
assert.equal(executions, 1);
for (const colorScheme of ['dark', 'light', 'dark']) {
  listeners.get('message')({ source: parent, data: {
    channel: 'astrion-inline-ui', instance: 'smoke', type: 'environment',
    environment: { variables: {}, theme: colorScheme, colorScheme, reducedMotion: false }
  } });
  assert.equal(context.document.documentElement.style.colorScheme, colorScheme);
  assert.equal(root.inert, false);
  assert.equal(executions, 1);
}
await new Promise((resolve) => setTimeout(resolve, 10));
assert.ok(events.some((event) => event.type === 'height' && event.height === 24));

const prompt = readFileSync(path.join(repository, 'prompts/show_ui.txt'), 'utf8');
assert.ok(prompt.includes('<show_ui js="on">'));
assert.ok(prompt.includes('astrion-ui-environment-change'));
console.log('show_ui offline smoke passed: parsing, sandbox script syntax, deferred execution and prompt contract.');
