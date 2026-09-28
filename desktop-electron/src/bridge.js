// 桌面控制桥：127.0.0.1 上的最小 HTTP 服务，供内嵌 Python 后端代理调用。
// 端点契约与 Tauri 壳（desktop/src-tauri/src/bridge.rs）完全一致，后端零改动：
//   GET  /version           应用版本
//   POST /update/install    触发检查 → 下载 → 安装 → 重启（无感更新）
//   GET  /update/progress   查询更新进度（前端经后端代理轮询）
//   POST /window/drag       Electron 下窗口拖拽走 app-region CSS，此端点兼容空操作
//   POST /chrome/dispatch   chrome 标签条意图 → executeJavaScript 注入 main 视图
// 仅绑定 loopback，不鉴权：同机进程本就在同一威胁域内。

import http from 'node:http';

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

/** 惰性加载 electron-updater（仅打包环境可用；dev 下更新不可用）。 */
async function getAutoUpdater() {
  if (autoUpdater) return autoUpdater;
  const mod = await import('electron-updater');
  autoUpdater = mod.autoUpdater;
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
    // 重启进入新版本（quitAndInstall 不返回）
    setTimeout(() => {
      setProgress(UpdateState.Restarting);
      autoUpdater.quitAndInstall();
    }, 300);
  });
  autoUpdater.on('error', (err) => {
    setProgress(UpdateState.Error, String(err?.message || err));
    installRunning = false;
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
