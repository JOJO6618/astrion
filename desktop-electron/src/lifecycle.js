// 启动编排：控制桥 → （必要时启动前迁移）→ 解析运行时 → spawn 后端 → 等就绪 → 建窗。
// 迁移必须在后端启动前完成，避免运行中的日志与任务数据在复制/校验期间继续变化。

import { app, dialog } from 'electron';
import {
  pickFreePort,
  spawnBackend,
  waitBackendReady,
  shutdownBackend,
  resolveRuntime
} from './backend.js';
import { startBridge } from './bridge.js';
import {
  getMigrationProgress,
  hasPendingMigration,
  markMigrationBackendReady,
  resolveRunDataRoot,
  runPendingMigration
} from './rundata.js';
import { loadLoginShellPath } from './shell_env.js';
import { createMigrationWindow } from './migration-window.js';
import { setMainWindowBackend, focusMainWindow, getMainView } from './window.js';
import { startQuickEntry, quickEnabled } from './quick/controller.js';

export { shutdownBackend };

export async function startBackendAndCreateWindow() {
  app.setName('Astrion');

  let migrationWindow = null;
  let migrationWindowClosed = false;
  let backendReady = false;
  let bridgePort = null;
  try {
    bridgePort = await startBridge({
      version: app.getVersion(),
      isPackaged: app.isPackaged,
      getMainView,
      getMigrationProgress,
      onMigrationContinue: () => {
        if (backendReady) {
          if (migrationWindow && !migrationWindow.isDestroyed()) migrationWindow.close();
          focusMainWindow();
        } else {
          app.quit();
        }
      }
    });
  } catch (err) {
    console.error('[astrion-desktop] 控制桥启动失败（更新功能不可用）:', err);
  }

  let desktopDataRoot;
  if (await hasPendingMigration()) {
    if (!bridgePort) {
      dialog.showErrorBox(
        '运行数据迁移无法开始',
        '迁移进度窗口无法启动，因此本次没有执行迁移。请重新打开应用后重试。'
      );
      throw new Error('migration_progress_unavailable');
    }
    migrationWindow = await createMigrationWindow(bridgePort);
    migrationWindow.on('closed', () => {
      migrationWindowClosed = true;
    });
    try {
      desktopDataRoot = await runPendingMigration();
    } catch (err) {
      // 设置无法安全提交时不启动后端。错误保留在进度窗，用户可退出后处理目录权限。
      console.error('[astrion-desktop] 迁移状态无法安全提交:', err);
      if (migrationWindow && !migrationWindow.isDestroyed()) migrationWindow.setAlwaysOnTop(false);
      if (migrationWindowClosed) return;
      return;
    }
    if (migrationWindowClosed) return;
  } else {
    desktopDataRoot = await resolveRunDataRoot();
  }

  const port = await pickFreePort();
  // 生产优先：.app 内嵌运行时；不存在则回退开发模式（系统 python + 源码树）
  const { python, backendDir } = resolveRuntime();
  const shellEnvironment = await loadLoginShellPath();
  if (shellEnvironment.status === 'loaded') {
    console.info('[astrion-desktop] 已从用户登录 shell 加载 PATH');
  } else if (shellEnvironment.status !== 'unsupported-platform') {
    console.warn(`[astrion-desktop] 登录 shell PATH 加载失败 (${shellEnvironment.status})，使用原始 PATH`);
  }

  spawnBackend({
    python,
    backendDir,
    port,
    bridgePort,
    version: app.getVersion(),
    shellPath: shellEnvironment.path,
    desktopDataRoot
  });

  await waitBackendReady(port);
  await startQuickEntry({ port, dataRoot: desktopDataRoot, getMainView, openMain: route => focusMainWindow(route) });
  setMainWindowBackend(port);
  // A configured quick-chat launch needs no hidden main WebContents. First
  // launch (quick chat disabled) still opens the normal setup/main interface.
  if (!quickEnabled()) focusMainWindow();
  backendReady = true;

  const migration = getMigrationProgress();
  if (migrationWindow && !migrationWindow.isDestroyed()) {
    migrationWindow.setAlwaysOnTop(false);
    if (migration.phase === 'error') {
      markMigrationBackendReady();
    } else if (migration.phase === 'done') {
      const completedAt = migration.completed_at || Date.now();
      const closeDelay = Math.max(0, 3000 - (Date.now() - completedAt));
      setTimeout(() => {
        if (migrationWindow && !migrationWindow.isDestroyed()) migrationWindow.close();
      }, closeDelay);
    }
  }
}
