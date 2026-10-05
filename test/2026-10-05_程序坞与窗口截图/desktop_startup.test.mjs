import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { build } from 'esbuild';

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}
const ready = deferred(), backend = deferred();
const app = new EventEmitter();
const calls = [];
Object.assign(app, {
  requestSingleInstanceLock: () => true,
  whenReady: () => ready.promise,
  setActivationPolicy: policy => calls.push(['activation', policy]),
  quit() {}, exit() {}, getPath: () => '/test', setPath() {}
});
globalThis.__desktopStartup = { app, calls, backend };
const mocks = {
  electron: 'export const app = globalThis.__desktopStartup.app; export const protocol = {registerSchemesAsPrivileged() {}};',
  './lifecycle.js': 'export const startBackendAndCreateWindow = async () => { globalThis.__desktopStartup.calls.push(["backend-start"]); await globalThis.__desktopStartup.backend.promise; }; export const shutdownBackend = () => {};',
  './menu.js': 'export const installAppMenu = () => {};',
  './window.js': 'export const focusMainWindow = () => globalThis.__desktopStartup.calls.push(["focus-intent"]);',
  './quick/controller.js': 'export const quickEnabled = () => true; export const showQuickEntry = () => {}; export const startQuickEntry = async () => {};'
};
const bundled = await build({ entryPoints: ['desktop-electron/src/main.js'], bundle: true, write: false, format: 'esm', platform: 'node',
  plugins: [{ name: 'isolated-startup', setup(builder) {
    builder.onResolve({ filter: /^electron$|^\.\/(lifecycle|menu|window)\.js$|^\.\/quick\/controller\.js$/ }, args => ({ path: args.path, namespace: 'mock' }));
    builder.onLoad({ filter: /.*/, namespace: 'mock' }, args => ({ contents: mocks[args.path] }));
  } }]
});
await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString('base64')}`);

test('Dock activation before app readiness retains a main-window intent', () => {
  app.emit('activate');
  assert.equal(calls.filter(item => item[0] === 'focus-intent').length, 1);
});

test('Dock activation during backend startup is not discarded', async () => {
  ready.resolve();
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(calls.some(item => item[0] === 'backend-start'));
  const before = calls.filter(item => item[0] === 'focus-intent').length;
  app.emit('activate');
  assert.equal(calls.filter(item => item[0] === 'focus-intent').length, before + 1);
});

test('a second launch requests the main window while the backend is starting', () => {
  const before = calls.filter(item => item[0] === 'focus-intent').length;
  app.emit('second-instance');
  assert.equal(calls.filter(item => item[0] === 'focus-intent').length, before + 1);
});

test('normal macOS startup keeps a regular application identity', () => {
  if (process.platform === 'darwin') assert.ok(calls.some(item => item[0] === 'activation' && item[1] === 'regular'));
  backend.resolve();
});
