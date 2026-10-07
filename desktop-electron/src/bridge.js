// 桌面控制桥：127.0.0.1 上的最小 HTTP 服务，供内嵌 Python 后端代理调用。
// 端点契约与 Tauri 壳（desktop/src-tauri/src/bridge.rs）完全一致，后端零改动：
//   GET  /version           应用版本
//   POST /update/install    触发检查 → 下载 → 安装 → 重启（无感更新）
//   GET  /update/progress   查询更新进度（前端经后端代理轮询）
//   POST /window/drag       Electron 下窗口拖拽走 app-region CSS，此端点兼容空操作
//   POST /chrome/dispatch   chrome 标签条意图 → executeJavaScript 注入 main 视图
// 仅绑定 loopback，不鉴权：同机进程本就在同一威胁域内。

import http from 'node:http';
import { app, dialog } from 'electron';
import { shutdownBackend } from './backend.js';
import { setUpdaterInstalling } from './updater-state.js';
import { migrationPageHtml } from './migration-window.js';
import { applyRunDataRoot, getMigrationProgress, getRunDataInfo } from './rundata.js';

/** 更新状态机（字符串与 Tauri 版一致，后端/前端按此渲染） */
const UpdateState = {
  Idle: 'idle',
  Checking: 'checking',
  Downloading: 'downloading',
  Installing: 'installing',
  Restarting: 'restarting',
  NoUpdate: 'no_update',
  Error: 'error'
};

const progress = {
  state: UpdateState.Idle,
  downloaded: 0,
  total: null,
  error: null
};

let installRunning = false;
let autoUpdater = null;

function setProgress(state, error = null) {
  progress.state = state;
  progress.error = error;
  if (state === UpdateState.Checking) {
    progress.downloaded = 0;
    progress.total = null;
  }
}

/**
 * 安装期兜底退出阶梯：quitAndInstall() 正常路径不返回；万一仍有路径把退出挡回来，
 * 先重试 app.quit()，再关掉内嵌 Python 后端强制退出——绝不停在「窗口没了、Dock 图标
 * 还在」的状态（该状态既让用户以为卡死，又会让 ShipIt 放弃本次安装）。
 * 注意 app.exit() 不触发 will-quit，后端必须显式收掉，否则会留下孤儿 Python 进程。
 */
const UPDATER_QUIT_RETRY_MS = 8000;
const UPDATER_FORCE_EXIT_MS = 11000;
let updaterExitFallbackArmed = false;

function armUpdaterExitFallback() {
  if (updaterExitFallbackArmed) return;
  updaterExitFallbackArmed = true;
  setTimeout(() => {
    console.warn('[astrion-desktop] quitAndInstall() 后仍未退出，改用 app.quit() 重试');
    setUpdaterInstalling(true);
    app.quit();
  }, UPDATER_QUIT_RETRY_MS);
  setTimeout(() => {
    console.warn('[astrion-desktop] quitAndInstall() 后仍未退出，兜底强制退出');
    try {
      shutdownBackend();
    } catch (err) {
      console.error('[astrion-desktop] 兜底退出时后端清理失败:', err);
    }
    app.exit(0);
  }, UPDATER_FORCE_EXIT_MS);
}

/** 惰性加载 electron-updater（仅打包环境可用；dev 下更新不可用）。 */
async function getAutoUpdater() {
  if (autoUpdater) return autoUpdater;
  const mod = await import('electron-updater');
  // electron-updater 是纯 CJS 包（out/main.js，无 ESM 入口）：Node 的 ESM 互操作
  // 只把 module.exports 挂在 default 上，命名导出拿不到（实测 6.8.9 的
  // mod.autoUpdater === undefined），必须从 default 取。两种形状都兼容，
  // 取不到时明确报错——否则后续 autoDownload 赋值会抛出误导性的
  // "Cannot set properties of undefined"，掩盖真实原因。
  const resolved = mod?.autoUpdater ?? mod?.default?.autoUpdater;
  if (!resolved) {
    throw new Error('electron-updater: autoUpdater export not found (module shape changed?)');
  }
  autoUpdater = resolved;
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = false;
  autoUpdater.on('update-not-available', () => {
    setProgress(UpdateState.NoUpdate);
    installRunning = false;
  });
  autoUpdater.on('download-progress', (p) => {
    progress.state = UpdateState.Downloading;
    progress.downloaded = p.transferred ?? 0;
    progress.total = p.total ?? null;
  });
  autoUpdater.on('update-downloaded', () => {
    setProgress(UpdateState.Installing);
    // 先开闸再安装：quitAndInstall() 是「先关闭所有窗口、之后才发 before-quit」，
    // 主窗口/快捷窗的 close 拦截若照旧生效，应用就退不出去（历史故障：窗口消失、
    // Dock 图标还在、点一下又弹回来，ShipIt 始终看到「有实例在运行」而放弃安装）。
    setUpdaterInstalling(true);
    // 重启进入新版本（quitAndInstall 正常路径不返回）
    setTimeout(() => {
      setProgress(UpdateState.Restarting);
      autoUpdater.quitAndInstall();
      armUpdaterExitFallback();
    }, 300);
  });
  autoUpdater.on('error', (err) => {
    setProgress(UpdateState.Error, String(err?.message || err));
    installRunning = false;
    // 安装没开始（检查/下载/校验失败）：撤销开闸，恢复正常关窗行为
    setUpdaterInstalling(false);
  });
  return autoUpdater;
}

async function tryStartInstall(isPackaged) {
  if (installRunning) {
    const err = new Error('already_running');
    err.statusCode = 409;
    throw err;
  }
  if (!isPackaged) {
    const err = new Error('dev_mode');
    err.statusCode = 409;
    throw err;
  }
  installRunning = true;
  setProgress(UpdateState.Checking);
  const updater = await getAutoUpdater();
  // 后台跑检查（HTTP 立即返回 202，与 Tauri 版语义一致——后端代理超时只有 5s，
  // 同步等检查完成会误报失败）；后续状态由事件链接管：
  // download-progress → update-downloaded → quitAndInstall
  updater.checkForUpdates().catch((err) => {
    setProgress(UpdateState.Error, String(err?.message || err));
    installRunning = false;
  });
}

/**
 * 启动控制桥。
 * @param {{version: string, isPackaged: boolean, getMainView: () => import('electron').WebContentsView | null}} ctx
 * @returns {Promise<number>} 实际监听端口
 */
export function startBridge(ctx) {
  const server = http.createServer((req, res) => {
    const send = (status, obj) => {
      const body = JSON.stringify(obj);
      res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(body);
    };
    const url = new URL(req.url || '/', 'http://127.0.0.1');
    const { pathname } = url;

    if (req.method === 'GET' && pathname === '/version') {
      send(200, { version: ctx.version });
      return;
    }
    if (req.method === 'POST' && pathname === '/update/install') {
      tryStartInstall(ctx.isPackaged)
        .then(() => send(202, { started: true }))
        .catch((err) => send(err.statusCode || 409, { started: false, error: String(err.message || err) }));
      return;
    }
    if (req.method === 'GET' && pathname === '/update/progress') {
      send(200, {
        state: progress.state,
        downloaded: progress.downloaded,
        total: progress.total,
        error: progress.error
      });
      return;
    }
    if (req.method === 'POST' && pathname === '/window/drag') {
      // Electron 下窗口拖拽由 chrome 条的 -webkit-app-region: drag 原生处理，
      // 此端点仅为兼容旧前端调用保留（空操作成功）
      send(200, { started: true });
      return;
    }
    if (req.method === 'GET' && pathname === '/rundata/info') {
      getRunDataInfo()
        .then((info) => send(200, info))
        .catch((err) => send(500, { success: false, error: String(err?.message || err) }));
      return;
    }
    if (req.method === 'GET' && pathname === '/rundata/migration') {
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
      res.end(migrationPageHtml);
      return;
    }
    if (req.method === 'GET' && pathname === '/rundata/migration/progress') {
      send(200, getMigrationProgress());
      return;
    }
    if (req.method === 'POST' && pathname === '/rundata/migration/continue') {
      send(202, { accepted: true });
      setTimeout(() => ctx.onMigrationContinue?.(), 150);
      return;
    }
    if (req.method === 'POST' && pathname === '/rundata/choose') {
      dialog
        .showOpenDialog({ properties: ['openDirectory', 'createDirectory'] })
        .then((result) => send(200, { success: true, canceled: result.canceled, path: result.filePaths[0] || '' }))
        .catch((err) => send(500, { success: false, error: String(err?.message || err) }));
      return;
    }
    if (req.method === 'POST' && pathname === '/rundata/apply') {
      let body = '';
      req.on('data', (chunk) => {
        body += chunk;
        if (body.length > 64 * 1024) req.destroy();
      });
      req.on('end', () => {
        let payload;
        try {
          payload = JSON.parse(body || '{}');
        } catch {
          send(400, { success: false, error: 'invalid_json' });
          return;
        }
        applyRunDataRoot(payload)
          .then((result) => send(200, result))
          .catch((err) => send(400, { success: false, error: String(err?.message || err) }));
      });
      return;
    }
    if (req.method === 'POST' && pathname === '/rundata/restart') {
      send(202, { success: true, restarting: true });
      setTimeout(() => {
        app.relaunch();
        app.exit(0);
      }, 250);
      return;
    }
    if (req.method === 'POST' && pathname === '/chrome/dispatch') {
      let body = '';
      req.on('data', (chunk) => {
        body += chunk;
        if (body.length > 64 * 1024) req.destroy(); // 意图 JSON 上限 64KB（对齐 Tauri 版）
      });
      req.on('end', () => {
        // body 是 JSON，重序列化后就是合法 JS 字面量，无注入面
        let payload;
        try {
          payload = JSON.parse(body || 'null');
        } catch {
          payload = null;
        }
        const view = ctx.getMainView();
        if (!view || view.webContents.isDestroyed()) {
          send(409, { dispatched: false, error: 'webview_missing' });
          return;
        }
        view.webContents
          .executeJavaScript(
            `window.__astrionChromeDispatch && window.__astrionChromeDispatch(${JSON.stringify(payload)})`
          )
          .then(() => send(200, { dispatched: true }))
          .catch((err) => send(409, { dispatched: false, error: String(err?.message || err) }));
      });
      return;
    }
    send(404, { error: 'not_found' });
  });

  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      resolve(typeof address === 'object' && address ? address.port : 0);
    });
  });
}
