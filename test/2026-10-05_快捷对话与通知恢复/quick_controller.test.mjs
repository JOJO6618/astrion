import { test } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { EventEmitter } from 'node:events';

const ipc = new EventEmitter();
const handlers = new Map();
ipc.handle = (name, handler) => handlers.set(name, handler);
const frame = {};
const mainContents = { mainFrame: frame, getURL: () => 'http://127.0.0.1:12345/settings/quick-chat' };
const mainEvent = { sender: mainContents, senderFrame: frame };
let quickWindow;
let signalReady;
let shown = 0, permissionChecks = 0, nativeStarts = 0;
class Window extends EventEmitter {
  constructor(options) {
    super(); this.options = options; quickWindow = this;
    this.webContents = new EventEmitter();
    this.webContents.mainFrame = {};
    this.webContents.setWindowOpenHandler = () => {};
    this.webContents.send = () => {};
  }
  setVisibleOnAllWorkspaces() {}
  setAlwaysOnTop() {}
  setBounds() {}
  isDestroyed() { return false; }
  show() { shown++; }
  focus() {}
  hide() {}
  async loadURL() {
    signalReady = () => ipc.emit('quick:ready', { sender: this.webContents, senderFrame: this.webContents.mainFrame });
  }
}
class Tray extends EventEmitter {
  isDestroyed() { return false; }
  setContextMenu() {}
  setTitle() {}
  setToolTip() {}
}
globalThis.__controllerMocks = {
  app: Object.assign(new EventEmitter(), { isPackaged: true, quit() {} }),
  BrowserWindow: Window, Tray, ipcMain: ipc,
  globalShortcut: { register() {}, unregister() {}, unregisterAll() {} },
  Menu: { buildFromTemplate: value => value }, nativeImage: { createFromDataURL: value => value },
  screen: Object.assign(new EventEmitter(), {
    getCursorScreenPoint: () => ({ x: 100, y: 100 }),
    getDisplayNearestPoint: () => ({ id: 1, workArea: { x: 0, y: 0, width: 1200, height: 800 } })
  }),
  shell: { openExternal: async () => {} },
  spawn() {
    nativeStarts++;
    const child = new EventEmitter(); child.stdout = new EventEmitter(); child.kill = () => child.emit('exit');
    return child;
  },
  fs: { readFile: async () => JSON.stringify({ enabled: true, modifier: 'option', workspace: 'desktop-workspace', model: 'desktop-model' }), mkdir: async () => {}, writeFile: async () => {}, rename: async () => {} },
  permissionCheck() { permissionChecks++; return 'granted'; }
};
const modules = {
  electron: 'export const { app, BrowserWindow, globalShortcut, ipcMain, Menu, nativeImage, screen, shell, Tray } = globalThis.__controllerMocks;',
  'node:child_process': 'export const spawn = (...args) => globalThis.__controllerMocks.spawn(...args);',
  'node:fs/promises': 'export default globalThis.__controllerMocks.fs;',
  './gateway.js': 'export class QuickGateway { constructor({port, dataRoot}) { this.port = port; this.root = dataRoot; this.base = `http://127.0.0.1:${port}`; } }',
  './assets.js': 'export const startQuickAssets = async () => ({url: "astrion-quick://app/quick", close() {}});',
  './capture.js': 'export class CaptureController { close() {} async prepare() {} updateExclusion() {} setPresentation() {} permission() { return globalThis.__controllerMocks.permissionCheck(); } }',
  './discovery.js': 'export const advertiseGateway = async () => {}; export const discoverGateway = async () => ({});',
  './input-regions.js': 'export class InputRegions { syncCursor() {} dispose() {} update() {return false;} }',
  './listener.js': 'export const inputPermission = async () => "granted"; export const resolveListenerBinary = () => "native-listener";'
};
const built = await build({ entryPoints: ['desktop-electron/src/quick/controller.js'], bundle: true, write: false, format: 'esm', platform: 'node',
  define: { 'import.meta.url': JSON.stringify(new URL('../../desktop-electron/src/quick/controller.js', import.meta.url).href) },
  plugins: [{ name: 'isolated-controller', setup(builder) {
    builder.onResolve({ filter: /^electron$|^node:child_process$|^node:fs\/promises$|^\.\/(gateway|assets|capture|discovery|input-regions|listener)\.js$/ }, args => ({path: args.path, namespace: 'mock'}));
    builder.onLoad({filter: /.*/, namespace: 'mock'}, args => ({contents: modules[args.path]}));
  } }]
});
const controller = await import(`data:text/javascript;base64,${Buffer.from(built.outputFiles[0].text).toString('base64')}`);
await controller.startQuickEntry({ port: 12345, dataRoot: '/desktop-data', getMainView: () => ({webContents: mainContents}) });

test('settings configuration does not wait for or infer screen permission', async () => {
  const result = await handlers.get('quick:info')(mainEvent);
  assert.equal(result.workspace, 'desktop-workspace');
  assert.equal(result.model, 'desktop-model');
  assert.equal(permissionChecks, 0);
  assert.equal('screenPermission' in result, false);
});

test('the Open button waits for renderer readiness and then shows the current desktop window', async () => {
  const pending = handlers.get('quick:open')(mainEvent);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(shown, 0);
  signalReady();
  await pending;
  assert.equal(shown, 1);
  assert.equal(quickWindow.options.webPreferences.backgroundThrottling, false);
});

test('disabling and re-enabling preserves custom options and restarts the native listener', async () => {
  const disabled = await handlers.get('quick:configure')(mainEvent, {enabled: false});
  assert.equal(disabled.model, 'desktop-model');
  await assert.rejects(handlers.get('quick:open')(mainEvent), /disabled/);
  const before = nativeStarts;
  const enabled = await handlers.get('quick:configure')(mainEvent, {enabled: true});
  assert.equal(enabled.workspace, 'desktop-workspace');
  assert.equal(nativeStarts, before + 1);
  await handlers.get('quick:open')(mainEvent);
  assert.equal(shown, 2);
});

test('permissions are queried separately and unknown origins cannot open the quick window', async () => {
  const result = await handlers.get('quick:permissions')(mainEvent);
  assert.equal(result.screenPermission, 'granted');
  assert.equal(result.inputPermission, 'granted');
  assert.equal(permissionChecks, 1);
  await assert.rejects(handlers.get('quick:open')({sender: mainContents, senderFrame: {}}), /Untrusted sender/);
});
