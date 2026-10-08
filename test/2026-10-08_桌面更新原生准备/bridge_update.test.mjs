import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { test } from 'node:test';
import { build } from 'esbuild';
import { fixture } from './fixture.mjs';
import { isUpdaterInstalling, setUpdaterInstalling } from '../../desktop-electron/src/updater-state.js';

let moduleId = 0;
async function setup(t) {
  const f = fixture();
  f.close(); // 控制桥会构造自己的真实安装控制器。
  let handler;
  let checks = 0;
  f.updater.checkForUpdates = async () => { checks += 1; };
  const http = {
    createServer(callback) {
      handler = callback;
      const server = new EventEmitter();
      server.listen = (_port, _host, done) => done();
      server.address = () => ({ port: 12346 });
      return server;
    }
  };
  globalThis.__updateBridgeTest = {
    ...f, http, isUpdaterInstalling, setUpdaterInstalling,
    log: (event, details) => f.logs.push({ event, ...details })
  };
  t.after(() => {
    f.app.emit('will-quit');
    setUpdaterInstalling(false);
    delete globalThis.__updateBridgeTest;
  });
  const mocks = {
    'node:http': 'export default globalThis.__updateBridgeTest.http;',
    electron: 'export const app = globalThis.__updateBridgeTest.app; export const autoUpdater = globalThis.__updateBridgeTest.nativeUpdater; export const dialog = {};',
    'electron-updater': 'export default {autoUpdater: globalThis.__updateBridgeTest.updater};',
    './backend.js': 'export const shutdownBackend = () => {};',
    './updater-log.js': 'export const createUpdaterLogger = () => globalThis.__updateBridgeTest.log;',
    './updater-state.js': 'export const setUpdaterInstalling = globalThis.__updateBridgeTest.setUpdaterInstalling;',
    './migration-window.js': 'export const migrationPageHtml = "";',
    './rundata.js': 'export const applyRunDataRoot = () => {}; export const getMigrationProgress = () => ({}); export const getRunDataInfo = () => ({});'
  };
  const built = await build({
    entryPoints: ['desktop-electron/src/bridge.js'], bundle: true, write: false,
    format: 'esm', platform: 'node',
    define: {
      setTimeout: 'globalThis.__updateBridgeTest.setTimer',
      clearTimeout: 'globalThis.__updateBridgeTest.clearTimer'
    },
    plugins: [{ name: 'update-dependencies', setup(builder) {
      builder.onResolve({ filter: /.*/ }, (args) => Object.hasOwn(mocks, args.path)
        ? { path: args.path, namespace: 'mock' } : undefined);
      builder.onLoad({ filter: /.*/, namespace: 'mock' }, (args) => ({ contents: mocks[args.path] }));
    } }]
  });
  const { startBridge } = await import(`data:text/javascript;base64,${Buffer.from(built.outputFiles[0].text).toString('base64')}#${++moduleId}`);
  await startBridge({ version: 'test', isPackaged: true, getMainView: () => null });
  const request = (method, url) => new Promise((resolve) => {
    let status;
    handler({ method, url }, {
      writeHead(code) { status = code; },
      end(body) { resolve({ status, body: JSON.parse(body) }); }
    });
  });
  return { ...f, request, checks: () => checks };
}

test('控制桥下载后保持 installing；原生就绪后才 restarting 并退出', async (t) => {
  const f = await setup(t);
  assert.equal((await f.request('POST', '/update/install')).status, 202);
  assert.equal(f.updater.autoInstallOnAppQuit, false);
  assert.equal(f.updater.autoRunAppAfterInstall, true);
  await f.download();
  f.tick(30_000);
  assert.equal((await f.request('GET', '/update/progress')).body.state, 'installing');
  assert.equal(f.count('app-quit'), 0);
  assert.equal((await f.request('POST', '/update/install')).status, 409);
  f.nativeUpdater.emit('update-downloaded');
  assert.equal((await f.request('GET', '/update/progress')).body.state, 'restarting');
  assert.equal(f.count('native-install'), 1);
  assert.equal(f.calls.find((call) => call.event === 'gate-at-install').open, true);
  assert.equal(f.pending(), 0);
});

test('控制桥超时上报 error；迟到原生事件不退出，收尾后可重试', async (t) => {
  const f = await setup(t);
  await f.request('POST', '/update/install');
  await f.download();
  f.tick(120_000);
  const result = await f.request('GET', '/update/progress');
  assert.equal(result.body.state, 'error');
  assert.match(result.body.error, /120 秒/);
  assert.equal((await f.request('POST', '/update/install')).status, 409);
  assert.equal(f.checks(), 1);
  f.nativeUpdater.emit('update-downloaded');
  assert.equal(f.count('native-install'), 0);
  assert.equal((await f.request('POST', '/update/install')).status, 202);
  assert.equal(f.checks(), 2);
});

test('控制桥检查同步失败后解除运行锁，下一次仍可发起检查', async (t) => {
  const f = await setup(t);
  f.updater.checkForUpdates = () => { throw new Error('check failed'); };
  const first = await f.request('POST', '/update/install');
  assert.equal(first.status, 409);
  assert.equal((await f.request('GET', '/update/progress')).body.state, 'error');
  f.updater.checkForUpdates = async () => {};
  assert.equal((await f.request('POST', '/update/install')).status, 202);
});
