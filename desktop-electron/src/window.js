// 主窗口与双 WebContentsView 管理（对齐 Tauri 壳的双 webview 架构）。
//
// 布局：BaseWindow（无标题栏，红绿灯悬浮于 chrome 条上）
//   ├─ chrome 视图：顶部固定 46px 独立对话标签条（加载 /chrome，常驻——
//   │   主页面导航/刷新/进设置页都不会让它重载或闪动，这是独立视图结构的
//   │   存在理由，用户明确要求保留）
//   └─ main 视图：加载 http://127.0.0.1:<port>/（前后端同源，cookie/CSRF 零改动）
//
// 导航规则（移植自 Tauri on_navigation）：
//   - 回环地址（127.0.0.1/localhost/[::1]，任意端口）一律放行——预览面板 iframe
//     （独立预览服务器/dev server）就是回环非同源，拦截会把预览顶去系统浏览器；
//   - 其余 http(s) 导航拦下转交系统默认浏览器；
//   - window.open 一律拦截：http(s) 转系统浏览器（预览面板「外部打开」按钮由此
//     在桌面端真正可用，Tauri 壳下是静默失败的）。

import { app, BaseWindow, shell, WebContentsView } from 'electron';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { quickEnabled } from './quick/controller.js';
import { isUpdaterInstalling } from './updater-state.js';

let appQuitting = false;
app.on('before-quit', () => { appQuitting = true; });
const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** 顶部 chrome 标签条高度（CSS px）。前端 _tab-strip.scss 的 46px 与主页面
 *  预留空间都对齐这个值；改动需三处同步。 */
export const CHROME_STRIP_HEIGHT = 46;

/** @type {BaseWindow | null} */
let mainWindow = null;
/** @type {WebContentsView | null} */
let mainView = null;
/** @type {WebContentsView | null} */
let chromeView = null;
let backendPort = null;
let pendingMainRoute = null;

export function setMainWindowBackend(port) {
  backendPort = port;
  if (pendingMainRoute !== null) {
    const route = pendingMainRoute;
    pendingMainRoute = null;
    focusMainWindow(route);
  }
}

export function getMainView() {
  return mainView;
}

export function focusMainWindow(route = '') {
  // 安装更新期间不再建窗、不再显示：此刻应用正在退出，窗口期要让给 ShipIt；
  // Dock 点击（activate）/ 快捷对话的「打开桌面端」如果在这里把窗口拉回来，
  // ShipIt 就会判定仍有实例在运行并放弃本次安装。
  if (isUpdaterInstalling()) return;
  const target = route === '/settings/quick-chat' ? route : '';
  if (!backendPort) { pendingMainRoute = target; return; }
  if (!mainWindow || mainWindow.isDestroyed()) createMainWindow(backendPort, target || '/');
  else if (target && mainView && !mainView.webContents.isDestroyed()) {
    void mainView.webContents.loadURL(`http://127.0.0.1:${backendPort}${target}`);
  }
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

const isLoopbackHost = (host) => host === '127.0.0.1' || host === 'localhost' || host === '::1' || host === '[::1]';

function openInSystemBrowser(url) {
  shell.openExternal(url).catch((err) => {
    console.error('[astrion-desktop] 打开系统浏览器失败:', err);
  });
}

/** 视图导航守卫：回环放行，其余 http(s) 转系统浏览器。 */
function installNavigationGuards(view) {
  view.webContents.on('will-navigate', (event, url) => {
    let parsed;
    try {
      parsed = new URL(url);
    } catch {
      return; // 解析失败放行（about:blank / data: 等）
    }
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return;
    if (isLoopbackHost(parsed.hostname)) return;
    event.preventDefault();
    openInSystemBrowser(url);
  });
  // window.open / target=_blank：一律不开新窗口，http(s) 转系统浏览器
  view.webContents.setWindowOpenHandler(({ url }) => {
    try {
      const parsed = new URL(url);
      if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
        openInSystemBrowser(url);
      }
    } catch {
      /* 非 URL 忽略 */
    }
    return { action: 'deny' };
  });
}

/** 双视图布局：chrome 条钉顶部（全宽 × 46px），main 视图占剩余区域。 */
function layoutViews() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  const { width, height } = mainWindow.getContentBounds();
  chromeView?.setBounds({ x: 0, y: 0, width, height: CHROME_STRIP_HEIGHT });
  mainView?.setBounds({
    x: 0,
    y: CHROME_STRIP_HEIGHT,
    width,
    height: Math.max(0, height - CHROME_STRIP_HEIGHT)
  });
}

export function createMainWindow(port, route = '/') {
  backendPort = port;
  const url = `http://127.0.0.1:${port}${route}`;
  const chromeUrl = `http://127.0.0.1:${port}/chrome`;

  mainWindow = new BaseWindow({
    width: 1280,
    height: 800,
    minWidth: 960,
    minHeight: 600,
    // macOS：隐藏标题栏、红绿灯悬浮于 chrome 条上（替代 Tauri Overlay + objc2 手动定位）
    ...(process.platform === 'darwin'
      ? {
          titleBarStyle: 'hidden',
          // 对齐 Tauri  objc2 版实测值：按钮高 16 → spacing = (46-16)/2 = 15
          trafficLightPosition: { x: 15, y: 15 }
        }
      : {})
  });

  const baseWebPreferences = {
    contextIsolation: true,
    sandbox: true,
    nodeIntegration: false,
    spellcheck: true
  };

  mainView = new WebContentsView({
    webPreferences: {
      ...baseWebPreferences,
      preload: path.join(__dirname, 'preload-main.cjs')
    }
  });
  chromeView = new WebContentsView({
    webPreferences: {
      ...baseWebPreferences,
      preload: path.join(__dirname, 'preload-chrome.cjs')
    }
  });

  // 添加顺序 = 视觉层级：chrome 在后添加，位于 main 之上（顶部区域不重叠，仅保险）
  mainWindow.contentView.addChildView(mainView);
  mainWindow.contentView.addChildView(chromeView);
  layoutViews();

  installNavigationGuards(mainView);
  installNavigationGuards(chromeView);

  mainView.webContents.loadURL(url);
  chromeView.webContents.loadURL(chromeUrl);

  // 键盘输入主权始终在主页面
  mainView.webContents.once('did-finish-load', () => {
    mainView?.webContents.focus();
  });

  mainWindow.on('resize', layoutViews);
  mainWindow.on('enter-full-screen', layoutViews);
  mainWindow.on('leave-full-screen', layoutViews);
  mainWindow.on('maximize', layoutViews);
  mainWindow.on('unmaximize', layoutViews);
  mainWindow.on('close', (event) => {
    // 安装更新时必须放行关闭：quitAndInstall() 先关窗口、之后才发 before-quit，
    // 此刻 appQuitting 仍为 false；若这里 preventDefault，应用永远退不出去，
    // ShipIt 等不到「无运行实例」而放弃安装。
    if (!appQuitting && !isUpdaterInstalling() && quickEnabled()) { event.preventDefault(); mainWindow.hide(); }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
    mainView = null;
    chromeView = null;
    if (!appQuitting && !quickEnabled()) app.quit();
  });

  return mainWindow;
}
