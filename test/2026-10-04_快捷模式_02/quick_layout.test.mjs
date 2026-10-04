import { test } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { quickWindowBounds } from '../../desktop-electron/src/quick/geometry.js';

test('all content heights preserve the same bottom anchor above the macOS Dock', () => {
  const desktop = { x: 0, y: 25, width: 1512, height: 850 };
  // A bottom Dock has reduced the 982px display to an 875px work-area bottom.
  const bounds = [130, 220, 440, 680].map(height => quickWindowBounds(desktop, height));
  assert.equal(new Set(bounds.map(item => item.y + item.height)).size, 1);
  assert.equal(bounds[0].y + bounds[0].height, 863);
  for (const item of bounds) assert.equal(item.x + item.width / 2, 756);
});

test('left Dock, negative display coordinates and oversized panels stay inside the work area', () => {
  const area = { x: -1840, y: 0, width: 1760, height: 1080 };
  const bounds = quickWindowBounds(area, 1800, 2000);
  assert.equal(bounds.y, 12);
  assert.equal(bounds.y + bounds.height, 1068);
  assert.equal(bounds.x, -1828);
  assert.equal(bounds.x + bounds.width, -92);
});

let captureImpl;
const bundled = await build({ entryPoints: ['desktop-electron/src/quick/capture.js'], bundle: true, write: false, format: 'esm', platform: 'node',
  define: { 'import.meta.url': JSON.stringify(new URL('../../desktop-electron/src/quick/capture.js', import.meta.url).href) },
  plugins: [{ name: 'isolated-capture', setup(builder) {
    builder.onResolve({ filter: /^electron$/ }, () => ({ path: 'electron', namespace: 'mock' }));
    builder.onResolve({ filter: /^\.\/screenshot\.js$/ }, () => ({ path: 'screenshot', namespace: 'mock' }));
    builder.onLoad({ filter: /.*/, namespace: 'mock' }, args => ({ contents: args.path === 'electron'
      ? 'export const ipcMain = { on() {} }; export const BrowserWindow = class {}; export const desktopCapturer = {}; export const screen = {}; export const systemPreferences = {};'
      : 'export const prepareNativeCapture = async () => "test-helper"; export const captureRegion = (...args) => globalThis.captureImpl(...args);' }));
  } }] });
const { CaptureController } = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString('base64')}`);
function captureFixture() {
  const events = [], windows = [];
  const quick = { hide() { windows.push('hide'); }, show() { windows.push('show'); }, focus() { windows.push('focus'); }, isDestroyed: () => false, isVisible: () => true,
    webContents: { send: (...args) => events.push(args) } };
  const controller = new CaptureController(quick, () => windows.push('dismiss'));
  controller.prepare = async () => { windows.push('rearm'); };
  const sender = {};
  controller.overlays.push({ display: { id: 2, bounds: { x: -1920, y: 0, width: 1920, height: 1080 } },
    window: { webContents: sender, isDestroyed: () => false, destroy() {} } });
  return { controller, sender, events, windows };
}

test('region capture keeps the quick window visible while awaiting the fresh screenshot', async () => {
  let resolveImage;
  globalThis.captureImpl = (...args) => { captureImpl = args; return new Promise(resolve => { resolveImage = resolve; }); };
  const { controller, sender, events, windows } = captureFixture();
  const rect = { x: 100, y: 100, width: 300, height: 200 };
  const pending = controller.finish({ sender }, rect);
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(windows, ['focus']);
  assert.equal(captureImpl[1].id, 2);
  assert.deepEqual(captureImpl[2], rect);
  resolveImage('data:image/png;base64,AA==');
  await pending;
  assert.deepEqual(events, [['quick:capture', 'data:image/png;base64,AA==']]);
  assert.deepEqual(windows, ['focus', 'rearm']);
});

test('hiding or re-summoning during capture discards the obsolete image', async () => {
  let resolveImage;
  globalThis.captureImpl = () => new Promise(resolve => { resolveImage = resolve; });
  const { controller, sender, events } = captureFixture();
  const pending = controller.finish({ sender }, { x: 0, y: 0, width: 300, height: 200 });
  await new Promise(resolve => setImmediate(resolve));
  controller.close();
  resolveImage('data:image/png;base64,AA==');
  await pending;
  assert.deepEqual(events, []);
});
