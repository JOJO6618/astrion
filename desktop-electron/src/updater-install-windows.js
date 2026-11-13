// NSIS uses electron-updater directly. The macOS Squirrel preparation stage
// must never run on Windows; set the shared close gate BEFORE closing windows.
import { setUpdaterInstalling } from './updater-state.js';

export function createWindowsUpdateInstaller({ updater, app, stopBackend, onState,
  onError, log = () => {}, setTimer = setTimeout, clearTimer = clearTimeout }) {
  let phase = 'idle';
  let timer = null;
  const fail = error => {
    if (phase === 'disposed') return;
    if (timer !== null) clearTimer(timer);
    timer = null;
    phase = 'idle';
    setUpdaterInstalling(false);
    log('windows-update-failed', { error: String(error?.message || error) });
    onError(error);
  };
  return {
    assertCanStart() {
      if (phase === 'disposed' || phase === 'installing') {
        const error = new Error('更新安装正在进行，不能重复开始。');
        error.statusCode = 409;
        throw error;
      }
    },
    prepare() {
      if (phase !== 'idle') return;
      phase = 'installing';
      setUpdaterInstalling(true);
      onState('installing');
      timer = setTimer(() => {
        timer = null;
        if (phase !== 'installing') return;
        try {
          stopBackend();
          onState('restarting');
          log('windows-nsis-install');
          // Assisted NSIS pages append APP_FILENAME to custom folders. A silent
          // update preserves the registered directory and still relaunches.
          updater.quitAndInstall(true, true);
        } catch (error) { fail(error); }
      }, 300);
    },
    fail,
    dispose() {
      if (timer !== null) clearTimer(timer);
      timer = null;
      phase = 'disposed';
      setUpdaterInstalling(false);
    }
  };
}
