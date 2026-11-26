// 启动编排：控制桥 → （必要时启动前迁移）→ 解析运行时 → spawn 后端 → 等就绪 → 建窗。
// 迁移必须在后端启动前完成，避免运行中的日志与任务数据在复制/校验期间继续变化。

import { app, dialog } from 'electron';
import { randomBytes } from 'node:crypto';
import {
  loadPreferredPort,
  pickFreePort,
  resolveBackendPort,
  resolveRuntime,
  savePreferredPort,
  shutdownBackend,
  spawnBackend,
  spawnRustBackend,
  waitBackendReady
} from './backend.js';
import { primeDesktopSession } from './session-priming.js';
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
import { setMainWindowBackend, focusMainWindow, isManagedView, conversationNotificationWindows } from './window.js';
import { startConversationNotifications } from './conversation-notifications.js';
import { startQuickEntry } from './quick/controller.js';
import { startScheduleNotifications } from './schedule-notifications.js';
import { setBrowserDataRoot } from './browser/index.js';
import { desktopSupported } from './platform.js';
import { enforceStartupUpdatePolicy, finishUpdatePolicyTransition } from './update-policy-window.js';
import { productionRustPaths } from './rust-backend.js';

export { shutdownBackend };

/**
 * 启动后端并等到就绪，返回实际使用的端口。
 *
 * 端口优先复用上次成功使用的那个：session cookie 名（agents_session_<port>）与
 * localStorage 都按 origin 隔离，每次换端口等于丢掉登录态、用户主题和本地缓存。
 * 复用端口在「探测」与「spawn」之间仍可能被其它进程抢占，后端会立刻退出，
 * 这种情况下换随机端口重试一次。
 */
async function startBackend(launch) {
  const preferred = loadPreferredPort();
  const port = await resolveBackendPort();
  try {
    await waitBackendReady(port, spawnBackend({ ...launch, port }));
    savePreferredPort(port);
    return port;
  } catch (error) {
    if (!preferred || port !== preferred) throw error;
    console.warn(`[astrion-desktop] 端口 ${port} 启动失败，改用随机端口重试:`, error?.message || error);
    if (await shutdownBackend() === false) throw error;
    const fallbackPort = await pickFreePort();
    await waitBackendReady(fallbackPort, spawnBackend({ ...launch, port: fallbackPort }));
    savePreferredPort(fallbackPort);
    return fallbackPort;
  }
}

async function startRustDesktop(runtime) {
  const port = await resolveBackendPort();
  const child = spawnRustBackend(runtime, { port, version: app.getVersion(), browserToken: runtime.browserToken });
  try {
    await waitBackendReady(port, child, 'rust');
    if (!await primeDesktopSession(port)) throw new Error('Rust desktop session unavailable');
  } catch (error) {
    await shutdownBackend();
    throw error;
  }
  savePreferredPort(port);
  return port;
}

export async function startBackendAndCreateWindow(rust = null) {
  if (rust?.experimental) {
    const port = await startRustDesktop(rust);
    setBrowserDataRoot(rust.dataRoot);
    setMainWindowBackend(port);
    focusMainWindow();
    return;
  }
  app.setName('Astrion');

  let migrationWindow = null;
  let migrationWindowClosed = false;
  let backendReady = false;
  let bridgePort = null;
  // AI 浏览器端点的随机凭据：只经专用管道交给本次启动的后端，不落盘。
  const browserToken = randomBytes(32).toString('hex');
  try {
    bridgePort = await startBridge({
      browserToken,
      version: app.getVersion(),
      isPackaged: app.isPackaged,
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

  // Handle required updates before migration, backend startup and Quick Chat.
  if (!await enforceStartupUpdatePolicy(bridgePort)) return;

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
    finishUpdatePolicyTransition();
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

  const shellEnvironment = await loadLoginShellPath();
  if (shellEnvironment.status === 'loaded') {
    console.info('[astrion-desktop] 已从用户登录 shell 加载 PATH');
  } else if (shellEnvironment.status !== 'unsupported-platform') {
    console.warn(`[astrion-desktop] 登录 shell PATH 加载失败 (${shellEnvironment.status})，使用原始 PATH`);
  }

  const rustPaths = rust && productionRustPaths(desktopDataRoot);
  const port = rust
    ? await startRustDesktop({ ...rust, ...rustPaths, bridgePort, browserToken: desktopSupported() ? browserToken : null, shellPath: shellEnvironment.path })
    : await startBackend({
      ...resolveRuntime(),
      bridgePort,
      browserToken: desktopSupported() ? browserToken : null,
      version: app.getVersion(),
      shellPath: shellEnvironment.path,
      desktopDataRoot
    });

  // 建窗之前先备好桌面免密会话：否则 chrome 标签条的首个 /api/conversation-tabs
  // 会在前端登录完成前拿到 401，界面显示「同步失败，重试」且没有标签。
  // 预登录失败只告警，前端原有的登录流程仍然兜底。
  if (!rust) {
    try {
      await primeDesktopSession(port);
    } catch (error) {
      console.warn('[astrion-desktop] 启动预登录失败（交由前端登录流程处理）:', error?.message || error);
    }
  }
  setBrowserDataRoot(desktopDataRoot);
  await startQuickEntry({
    port, dataRoot: desktopDataRoot, dataDir: rustPaths?.dataDir, staticRoot: rust?.staticRoot,
    isManagedView, openMain: route => focusMainWindow(route),
  });
  setMainWindowBackend(port);
  startScheduleNotifications(port, route => focusMainWindow(route));
  // An explicit desktop launch always opens its main window, including when
  // Quick Chat is enabled. Closing it can still keep Quick Chat running.
  focusMainWindow();
  if (desktopSupported()) startConversationNotifications(port, conversationNotificationWindows);
  finishUpdatePolicyTransition();
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
