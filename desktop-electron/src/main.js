// Astrion 桌面壳入口（Electron/Chromium，替代 desktop/ 的 Tauri/WKWebView 壳）。
//
// 架构（与 Tauri 壳一致，后端契约零改动）：
//   1. 选取空闲端口 → 启动 Rust 后端（生产包内嵌 astrion-headless）
//   2. 轮询后端就绪（/api/host-mode-enabled，无需鉴权）
//   3. 创建主窗口：BaseWindow + 双 WebContentsView——
//      chrome 视图（顶部 46px 独立对话标签条，加载 /chrome，常驻不随主页面导航重载）
//      + main 视图（加载 http://127.0.0.1:<port>/，前后端同源）
//   4. 主窗口关闭 → 后台驻留；显式退出 → will-quit 时停止后端
//
// 换引擎动机：WKWebView 反复出现引擎级兼容问题（折叠动画瞬消、rAF 样式延迟、
// virtua 首项不挂载、拖放被吞、上滚估值补偿可见抖动），统一 Chromium 后与
// Windows（WebView2）/ Chrome web 同一引擎，测试矩阵塌缩。

import { app, protocol } from 'electron';
import fs from 'node:fs';
import path from 'node:path';
import { startBackendAndCreateWindow, shutdownBackend } from './lifecycle.js';
import { installAppMenu } from './menu.js';
import { startQuickEntry, quickEnabled, showQuickEntry } from './quick/controller.js';
import { focusMainWindow } from './window.js';
import { isUpdaterInstalling } from './updater-state.js';
import { focusUpdatePolicyWindow, updatePolicyTransitioning } from './update-policy-window.js';
import { resolveDesktopBackend } from './rust-backend.js';

const quickDebug = !app.isPackaged && process.argv.includes('--quick-debug');
const rust = quickDebug ? null : resolveDesktopBackend({ resourcesPath: app.isPackaged ? process.resourcesPath : undefined });
app.setName(rust?.experimental ? 'Astrion Rust Experiment' : 'Astrion');
const isolatedUserData = rust?.profile || process.env.ASTRION_ELECTRON_USER_DATA;
if (isolatedUserData) {
  const directory = path.resolve(isolatedUserData);
  fs.mkdirSync(directory, { recursive: true });
  app.setPath('userData', directory);
  app.setPath('sessionData', directory);
}
if (quickDebug) app.setPath('userData', app.getPath('userData') + '-quick-debug');
protocol.registerSchemesAsPrivileged([{ scheme: 'astrion-quick', privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true } }]);

// 单实例锁：第二实例直接退出，焦点交给已运行窗口（Tauri 侧曾是待办项）
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    // 安装更新期间忽略二次启动/激活意图：应用正在退出，任何建窗都会让 ShipIt
    // 判定“仍有实例在运行”并放弃本次安装。
    if (isUpdaterInstalling() || focusUpdatePolicyWindow()) return;
    if (quickDebug) showQuickEntry();
    else focusMainWindow();
  });

  app.on('activate', () => {
    // focusMainWindow queues the intent until the backend is ready.
    if (isUpdaterInstalling() || focusUpdatePolicyWindow()) return;
    if (!quickDebug) focusMainWindow();
  });

  app.whenReady().then(async () => {
    if (process.platform === 'darwin' && !quickDebug) app.setActivationPolicy('regular');
    installAppMenu();
    try {
      if (quickDebug) {
        await startQuickEntry({ debug: true, port: process.env.ASTRION_API_PORT ? Number(process.env.ASTRION_API_PORT) : undefined, dataRoot: process.env.ASTRION_DATA_ROOT, workspacePath: process.env.ASTRION_QUICK_WORKSPACE || process.cwd() });
      } else {
        await startBackendAndCreateWindow(rust);
      }
    } catch (err) {
      await shutdownBackend();
      // 后端起不来属致命错误：写日志 + 落盘临时文件（GUI 进程无控制台可见性），退出
      console.error('[astrion-desktop] 启动失败:', err);
      const fs = await import('node:fs');
      const os = await import('node:os');
      const logPath = path.join(rust?.profile || os.tmpdir(), 'astrion-desktop-startup.log');
      try {
        fs.writeFileSync(logPath, `[astrion-desktop] 启动失败\n${err?.stack || err}\n`);
      } catch {
        /* 落盘失败不阻断退出 */
      }
      app.exit(1);
    }
  });

  app.on('window-all-closed', () => {
    // Rechecking can release the update gate before the normal window exists.
    if (updatePolicyTransitioning()) return;
    // 关窗保留调度；托盘菜单或应用菜单的显式退出仍走 will-quit。
    // 更新策略窗口关闭流程由 updatePolicyTransitioning 管理。
  });

  let backendShutdown;
  app.on('will-quit', (event) => {
    if (!rust) {
      shutdownBackend();
      return;
    }
    event.preventDefault();
    backendShutdown ??= Promise.resolve(shutdownBackend()).then(result => {
      if (result === false) console.error('[astrion-desktop] Rust 后端未在退出期限内关闭');
    }).catch(error => {
      console.error('[astrion-desktop] Rust 后端退出失败:', error);
    }).finally(() => app.exit(0));
  });
}
