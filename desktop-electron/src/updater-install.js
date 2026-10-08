// macOS 有两次下载完成：electron-updater 的 ZIP 就绪，以及原生 Squirrel 的
// 解压/验签就绪。只有后者才允许请求退出；退出兜底必须等原生安装退出事件。
import { setUpdaterInstalling } from './updater-state.js';

const PREPARE_TIMEOUT_MS = 120_000;
const INSTALL_DELAY_MS = 300;
const QUIT_RETRY_MS = 8000;
const FORCE_EXIT_MS = 11000;

export function createUpdateInstaller({
  updater,
  nativeUpdater,
  app,
  stopBackend,
  onState,
  onError,
  log,
  setTimer = setTimeout,
  clearTimer = clearTimeout
}) {
  let phase = 'idle';
  let nativePreparing = false;
  let installDelay = null;
  let prepareTimeout = null;
  let quitRetry = null;
  let forceExit = null;

  function clearTimers() {
    for (const timer of [installDelay, prepareTimeout, quitRetry, forceExit]) {
      if (timer !== null) clearTimer(timer);
    }
    installDelay = prepareTimeout = quitRetry = forceExit = null;
  }

  function fail(error) {
    if (phase === 'failed' || phase === 'done') return;
    clearTimers();
    phase = 'failed';
    setUpdaterInstalling(false);
    const message = String(error?.message || error);
    log('update-failed', { error: message, nativePreparing });
    onError(message);
  }

  function onNativeReady() {
    nativePreparing = false;
    if (phase !== 'preparing') {
      // 超时后到达的事件不能再关窗/退出。没有排队的 quitAndInstall 监听器。
      log('native-ready-ignored', { phase });
      return;
    }
    if (installDelay !== null) clearTimer(installDelay);
    installDelay = null;
    phase = 'quitting';
    log('native-update-ready');
    setUpdaterInstalling(true);
    onState('restarting');
    try {
      // MacUpdater 构造时已监听同一原生事件并设置就绪标记。此时调用不再
      // 排队等待准备，而是立即进入原生 quitAndInstall 的安装退出流程。
      updater.quitAndInstall();
    } catch (error) {
      fail(error);
    }
  }

  function onNativeQuit() {
    if (phase !== 'quitting' || quitRetry !== null) return;
    if (prepareTimeout !== null) clearTimer(prepareTimeout);
    prepareTimeout = null;
    log('native-before-quit-for-update');
    quitRetry = setTimer(() => {
      log('quit-retry');
      app.quit();
    }, QUIT_RETRY_MS);
    forceExit = setTimer(() => {
      log('force-exit');
      try {
        stopBackend();
      } catch (error) {
        log('backend-cleanup-failed', { error: String(error?.message || error) });
      }
      app.exit(0);
    }, FORCE_EXIT_MS);
  }

  function onNativeError(error) {
    nativePreparing = false;
    fail(error);
  }

  function onWillQuit() {
    log('app-will-quit', { phase });
    clearTimers();
    phase = 'done';
  }

  nativeUpdater.on('update-downloaded', onNativeReady);
  nativeUpdater.on('before-quit-for-update', onNativeQuit);
  nativeUpdater.on('error', onNativeError);
  app.on('will-quit', onWillQuit);

  return {
    assertCanStart() {
      // 原生检查没有取消 API。超时后允许其在后台收尾，但收尾前不发起
      // 重叠检查，防止上一轮的就绪事件被当成本轮安装完成。
      if (nativePreparing) {
        const error = new Error('上一轮更新准备尚未结束，请稍后重试。');
        error.statusCode = 409;
        throw error;
      }
      clearTimers();
      phase = 'idle';
    },
    prepare() {
      if (phase === 'preparing' || phase === 'quitting' || phase === 'done') return;
      phase = 'preparing';
      setUpdaterInstalling(false);
      onState('installing');
      log('native-preparation-start');
      prepareTimeout = setTimer(() => {
        fail(new Error('更新安装准备超过 120 秒，应用未退出，请稍后重试。'));
      }, PREPARE_TIMEOUT_MS);
      installDelay = setTimer(() => {
        installDelay = null;
        nativePreparing = true;
        try {
          // autoInstallOnAppQuit=false 时，ZIP 就绪不会自动触发原生下载。
          // 使用 Electron 的公开 API 显式准备，不提前排队安装退出请求。
          nativeUpdater.checkForUpdates();
        } catch (error) {
          nativePreparing = false;
          fail(error);
        }
      }, INSTALL_DELAY_MS);
    },
    fail,
    dispose() {
      clearTimers();
      nativeUpdater.removeListener('update-downloaded', onNativeReady);
      nativeUpdater.removeListener('before-quit-for-update', onNativeQuit);
      nativeUpdater.removeListener('error', onNativeError);
      app.removeListener('will-quit', onWillQuit);
      phase = 'done';
      setUpdaterInstalling(false);
    }
  };
}
