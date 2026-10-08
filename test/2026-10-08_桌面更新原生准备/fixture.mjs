import { EventEmitter } from 'node:events';
import fs from 'node:fs';
import vm from 'node:vm';
import { createRequire } from 'node:module';
import { createUpdateInstaller } from '../../desktop-electron/src/updater-install.js';
import { isUpdaterInstalling, setUpdaterInstalling } from '../../desktop-electron/src/updater-state.js';

const updaterFile = new URL('../../desktop-electron/node_modules/electron-updater/out/MacUpdater.js', import.meta.url);
const updaterRequire = createRequire(updaterFile);
const updaterSource = fs.readFileSync(updaterFile, 'utf8');

export function fixture({ blockQuit = false, cleanupError = false } = {}) {
  setUpdaterInstalling(false);
  let now = 0;
  let nextId = 0;
  const timers = new Map();
  const calls = [];
  const states = [];
  const errors = [];
  const logs = [];
  const setTimer = (callback, delay) => {
    const id = ++nextId;
    timers.set(id, { callback, time: now + delay });
    return id;
  };
  const clearTimer = (id) => timers.delete(id);
  const app = new EventEmitter();
  const nativeUpdater = new EventEmitter();
  const record = (event) => calls.push({ event, time: now });
  app.quit = () => {
    record('app-quit');
    if (!blockQuit) app.emit('will-quit');
  };
  app.exit = (code) => record(`app-exit-${code}`);
  nativeUpdater.setFeedURL = () => record('native-feed');
  nativeUpdater.checkForUpdates = () => record('native-check');
  nativeUpdater.quitAndInstall = () => {
    record('native-install');
    calls.push({ event: 'gate-at-install', open: isUpdaterInstalling() });
    nativeUpdater.emit('before-quit-for-update');
    app.quit();
  };

  class AppUpdater extends EventEmitter {
    constructor() {
      super();
      this.app = app;
      this._logger = { info() {}, warn() {}, debug() {} };
    }
    dispatchUpdateDownloaded(event) { this.emit('update-downloaded', event); }
  }
  class ProxyServer extends EventEmitter {
    listen(_port, _host, callback) { callback(); }
    address() { return { port: 12345 }; }
    close(callback) { this.emit('close'); callback?.(); }
  }
  const context = {
    exports: {},
    require(name) {
      if (name === './AppUpdater') return { AppUpdater };
      if (name === 'electron') return { autoUpdater: nativeUpdater };
      if (name === 'http') return { createServer: () => new ProxyServer() };
      return updaterRequire(name);
    },
    Buffer
  };
  vm.runInNewContext(updaterSource, context, { filename: updaterFile.pathname });
  const updater = new context.exports.MacUpdater();
  updater.autoDownload = true;
  updater.autoInstallOnAppQuit = false;
  updater.autoRunAppAfterInstall = true;
  const installer = createUpdateInstaller({
    updater, nativeUpdater, app,
    stopBackend() {
      record('backend-cleanup');
      if (cleanupError) throw new Error('cleanup failure');
    },
    onState: (state) => states.push(state),
    onError: (error) => errors.push(error),
    log: (event, details = {}) => logs.push({ event, ...details, time: now }),
    setTimer,
    clearTimer
  });
  updater.on('update-downloaded', () => installer.prepare());
  updater.on('error', (error) => installer.fail(error));

  return {
    installer, updater, nativeUpdater, app, calls, states, errors, logs,
    setTimer, clearTimer,
    async download() {
      // 真正执行已安装依赖的本地代理交接方法；仅 HTTP 传输和系统进程用桩。
      await updater.updateDownloaded(
        { url: new URL('https://example.invalid/update.zip'), info: { size: 1 } },
        { version: '0.5.6', downloadedFile: '/fake/update.zip' }
      );
    },
    tick(milliseconds) {
      const end = now + milliseconds;
      while (true) {
        const next = [...timers.entries()]
          .filter(([, timer]) => timer.time <= end)
          .sort((a, b) => a[1].time - b[1].time)[0];
        if (!next) break;
        const [id, timer] = next;
        timers.delete(id);
        now = timer.time;
        timer.callback();
      }
      now = end;
    },
    count: (event) => calls.filter((call) => call.event === event).length,
    pending: () => timers.size,
    close: () => installer.dispose()
  };
}
