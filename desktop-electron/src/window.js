// 主窗口与多内容视图管理（对齐 Tauri 壳的双 webview 架构，扩展为每标签一个内容视图）。
//
// 布局：BaseWindow（无标题栏，红绿灯悬浮于 chrome 条上）
//   ├─ chrome 视图：顶部固定 46px 独立对话标签条（加载 /chrome，常驻——
//   │   内容视图切换/刷新/进设置页都不会让它重载或闪动，这是独立视图结构的
//   │   存在理由，用户明确要求保留）
//   └─ 内容视图 × N：每个标签一个 WebContentsView，加载 http://127.0.0.1:<port>/…?tab=<key>
//       （前后端同源，cookie/CSRF 零改动）。显示/隐藏/LRU/预热由 views/manager.js 的
//       ViewManager 按后端导航快照收敛（设计见 docs/desktop_multi_view.md）。
//
// 导航规则（移植自 Tauri on_navigation）：
//   - 回环地址（127.0.0.1/localhost/[::1]，任意端口）一律放行——预览面板 iframe
//     （独立预览服务器/dev server）就是回环非同源，拦截会把预览顶去系统浏览器；
//   - 其余 http(s) 导航拦下转交系统默认浏览器；
//   - window.open 一律拦截：http(s) 转系统浏览器（预览面板「外部打开」按钮由此
//     在桌面端真正可用，Tauri 壳下是静默失败的）。

import { app, BaseWindow, ipcMain, screen, shell, WebContentsView } from 'electron';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { quickEnabled } from './quick/controller.js';
import { isUpdaterInstalling } from './updater-state.js';
import { NativeBrowser } from './browser/index.js';
import { setBrowserSurfaceColor } from './browser/geometry.js';
import { desktopSupported, windowChromeOptions } from './platform.js';
import { ViewManager } from './views/manager.js';
import { createDetachedWindow, applyDetachedChromeTheme } from './views/detached-window.js';
import { CHROME_STRIP_HEIGHT } from './views/layout.js';

export { CHROME_STRIP_HEIGHT };

let appQuitting = false;
app.on('before-quit', () => { appQuitting = true; });
const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** @type {BaseWindow | null} */
let mainWindow = null;
/** @type {ViewManager | null} */
let views = null;
/** @type {WebContentsView | null} */
let chromeView = null;
let nativeBrowser = null;
let backendPort = null;

// 窗口底色跟随前端主题（--surface-base），并记住上次的值：
// 启动/拉伸窗口时页面还没画出来的区域显示主题底色，而不是透出系统默认色。
const DEFAULT_SURFACE = '#faf9f5';
const SURFACE_PATTERN = /^#[a-f\d]{6}$/i;
const surfaceFile = () => path.join(app.getPath('userData'), 'window-surface.json');
const recencyFile = () => path.join(app.getPath('userData'), 'view-recency.json');
let surfaceColor = DEFAULT_SURFACE;
function loadSurfaceColor() {
  try {
    const saved = JSON.parse(fs.readFileSync(surfaceFile(), 'utf8')).color;
    if (typeof saved === 'string' && SURFACE_PATTERN.test(saved)) return saved.toLowerCase();
  } catch { /* First launch or unreadable file: use the classic theme color. */ }
  return DEFAULT_SURFACE;
}

// 主题键的记忆：后端端口可能变，localStorage 按 origin（含端口）隔离，
// 新 origin 读不到用户主题，首帧只能先按 classic 画（顶部条与对话区会短暂不一致）。
// 壳层记住上次主题，preload 在页面脚本执行前同步取走，供首屏内联脚本使用。
const THEME_KEYS = new Set(['classic', 'light', 'dark']);
const themeFile = () => path.join(app.getPath('userData'), 'theme.json');
let themeKey = '';
function currentThemeKey() {
  if (!themeKey) {
    try {
      const saved = JSON.parse(fs.readFileSync(themeFile(), 'utf8')).theme;
      themeKey = THEME_KEYS.has(saved) ? saved : '';
    } catch { /* 首次启动或文件损坏：交给页面默认值 */ }
  }
  return themeKey;
}
function saveThemeKey(next) {
  if (!THEME_KEYS.has(next) || next === themeKey) return;
  themeKey = next;
  fs.promises.writeFile(themeFile(), JSON.stringify({ theme: next }), { mode: 0o600 }).catch(() => {});
}

function loadRecency() {
  try {
    const value = JSON.parse(fs.readFileSync(recencyFile(), 'utf8'));
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return Object.fromEntries(Object.entries(value).filter(([, at]) => Number.isFinite(at)));
    }
  } catch { /* 首次启动或文件损坏：按标签栏顺序预热 */ }
  return {};
}
function saveRecency(value) {
  fs.promises.writeFile(recencyFile(), JSON.stringify(value), { mode: 0o600 }).catch(() => {});
}
function applySurfaceColor(color) {
  if (typeof color !== 'string' || !SURFACE_PATTERN.test(color)) return;
  color = color.toLowerCase();
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.setBackgroundColor(color);
  chromeView?.setBackgroundColor(color);
  // 内置浏览器管理内容视图的透明叠层（网页显示时宿主视图必须透明），交给它统一设置。
  // 先记录并落盘，再给各视图着色：视图异常不得让窗口底色缓存停在旧值。
  if (color !== surfaceColor) {
    surfaceColor = color;
    fs.promises.writeFile(surfaceFile(), JSON.stringify({ color }), { mode: 0o600 }).catch(() => {});
  }
  // 隐藏的内容视图不参与浏览器叠层（BrowserStacking 跳过），直接着色；没有浏览器时全部着色。
  for (const entry of views?.entries.values() || []) {
    if (!nativeBrowser || !entry.shown) entry.view.setBackgroundColor(color);
  }
  // 独立对话窗口同步着色（Windows 原生三大键走标签栏/文字色）。
  for (const window of views?.detachedWindows.values() || []) {
    if (window.isDestroyed()) continue;
    window.setBackgroundColor(color);
    window.contentView.setBackgroundColor(color);
    applyDetachedChromeTheme(window, nativeBrowser?.chromeColor || color, nativeBrowser?.textColor || '');
  }
  if (nativeBrowser) { setBrowserSurfaceColor(nativeBrowser, color); nativeBrowser.layout(); }
}

/** IPC 来源必须是受管视图的顶层 frame（预览 iframe 等子 frame 不可信）。 */
const fromMainFrame = (event) => event.senderFrame === event.sender.mainFrame;
const fromContentView = (event) => !!views && fromMainFrame(event) && views.isManagedView(event.sender);
const fromChrome = (event) => !!views && fromMainFrame(event) && views.isChrome(event.sender);

// 只接受受管内容视图（应用自身页面）的上报；网页与 chrome 条都没有这个通道。
ipcMain.on('window:surface', (event, color) => {
  if (!fromContentView(event)) return;
  applySurfaceColor(color);
});
// 当前主题键 → 壳层记忆（页面 applyTheme 时上报），供下次启动首帧使用。
ipcMain.on('shell:theme', (event, key) => {
  if (!fromContentView(event) && !fromChrome(event)) return;
  if (typeof key === 'string') saveThemeKey(key);
});
// preload 在页面脚本执行前同步取首帧主题；主题不是凭据，任一顶层 frame 可读。
ipcMain.on('shell:theme-get', (event) => {
  event.returnValue = fromMainFrame(event) ? currentThemeKey() : '';
});
// astrionShell 桥（设计 §4.2）。身份一律按 sender 返回，页面不得自报。
ipcMain.handle('shell:identity', (event) => (fromContentView(event) ? views.identityFor(event.sender) : null));
// 快照由 chrome（500ms 轮询）或任一受管内容视图（命令响应）转交；ViewManager 只接受更高 revision。
ipcMain.on('shell:tabs-forward', (event, state) => {
  if (!fromContentView(event) && !fromChrome(event)) return;
  try { views.handleSnapshot(state, event.sender); } catch (err) { console.error('[astrion-desktop] 快照处理失败:', err); }
});
// 乐观切换：仅 chrome 标签条可用。
ipcMain.on('shell:show', (event, key) => {
  if (!fromChrome(event)) return;
  try { views.show(key); } catch (err) { console.error('[astrion-desktop] show 失败:', err); }
});
ipcMain.on('shell:focus-main', (event) => {
  if (!fromContentView(event)) return;
  focusMainWindow();
});
// 拖出：chrome 决定要拖出时发 drop；壳层建窗迁入视图后经 'shell:request-detach' 请 chrome 发 detach 命令。
ipcMain.on('shell:drag-tab', (event, payload) => {
  if (!fromChrome(event) || !payload || typeof payload !== 'object') return;
  try { views.dragTab(payload); } catch (err) { console.error('[astrion-desktop] 拖拽消息处理失败:', err); }
});
// 拖出前的显示器信息：标签条靠近屏幕上沿时据此计算向上拖出的余量（window.screen 在 macOS 菜单栏/叠放屏下不可靠）。
ipcMain.handle('shell:drag-info', (event) => {
  if (!fromChrome(event)) return null;
  return { displays: screen.getAllDisplays().map(({ bounds }) => ({ x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height })) };
});
// 页面（open/workspace 返回 focus:"detached"）请求前置持有某标签的窗口。
ipcMain.on('shell:focus-tab', (event, key) => {
  if (!fromContentView(event) || typeof key !== 'string') return;
  try { views.focusTab(key); } catch (err) { console.error('[astrion-desktop] focusTab 失败:', err); }
});

let pendingMainRoute = null;

export function setMainWindowBackend(port) {
  backendPort = port;
  if (pendingMainRoute !== null) {
    const route = pendingMainRoute;
    pendingMainRoute = null;
    focusMainWindow(route);
  }
}

/** 主窗口当前显示的内容视图（取代旧 getMainView）。 */
export function getActiveView() {
  return views?.getActiveView() || null;
}

/** webContents 是否是受管内容视图（快捷入口可信判定等）。 */
export function isManagedView(webContents) {
  return !!views && views.isManagedView(webContents);
}

export function focusMainWindow(route = '') {
  // 安装更新期间不再建窗、不再显示：此刻应用正在退出，窗口期要让给 ShipIt；
  // Dock 点击（activate）/ 快捷对话的「打开桌面端」如果在这里把窗口拉回来，
  // ShipIt 就会判定仍有实例在运行并放弃本次安装。
  if (isUpdaterInstalling()) return;
  const target = ['/settings/quick-chat', '/schedules'].includes(route) ? route : '';
  if (!backendPort) { pendingMainRoute = target; return; }
  if (!mainWindow || mainWindow.isDestroyed()) createMainWindow(backendPort, target || '/');
  else if (target) views?.requestPage(target);
  if (mainWindow.isMinimized()) mainWindow.restore();
  views?.showDetached();
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

/** 布局：chrome 条钉顶部，内容视图占剩余区域（几何见 views/layout.js）。 */
function layoutViews() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  views?.layout();
  nativeBrowser?.resize();
}

export function createMainWindow(port, route = '/') {
  backendPort = port;
  const origin = `http://127.0.0.1:${port}`;
  const chromeUrl = `${origin}/chrome`;

  surfaceColor = loadSurfaceColor();
  mainWindow = new BaseWindow({
    backgroundColor: surfaceColor,
    width: 1280,
    height: 800,
    minWidth: 960,
    minHeight: 600,
    // macOS：隐藏标题栏、红绿灯悬浮于 chrome 条上（替代 Tauri Overlay + objc2 手动定位）
    ...windowChromeOptions()
  });
  // BaseWindow 的背景不等于其根 View 的背景；首帧和拉伸露出的底层也必须显式着色。
  mainWindow.contentView.setBackgroundColor(surfaceColor);

  const baseWebPreferences = {
    contextIsolation: true,
    sandbox: true,
    nodeIntegration: false,
    spellcheck: true
  };

  chromeView = new WebContentsView({
    webPreferences: {
      ...baseWebPreferences,
      preload: path.join(__dirname, 'preload-chrome.cjs')
    }
  });
  chromeView.setBackgroundColor(surfaceColor);
  mainWindow.contentView.addChildView(chromeView);

  // 内容视图工厂：每个标签一个视图，默认 backgroundThrottling（隐藏视图自动降频，P0 实测 1Hz）。
  const createView = ({ url }) => {
    const view = new WebContentsView({
      webPreferences: {
        ...baseWebPreferences,
        preload: path.join(__dirname, 'preload-view.cjs')
      }
    });
    view.setBackgroundColor(surfaceColor);
    installNavigationGuards(view);
    // 窗口标题 = 页面 document.title（独立对话窗口用）。
    view.webContents.on('page-title-updated', (_event, title) => { views?.titleChanged(view.webContents, title); });
    void view.webContents.loadURL(url);
    return view;
  };
  views = new ViewManager({
    origin,
    window: mainWindow,
    chromeView,
    createView,
    getCursor: () => screen.getCursorScreenPoint(),
    // quitAndInstall 先关窗口、后发 before-quit，故两者都要判断。
    isQuitting: () => appQuitting || isUpdaterInstalling(),
    createDetachedWindow: ({ key, point, offset, focus }, callbacks) => createDetachedWindow({
      key, point, offset, focus,
      occupied: [...views.detachedWindows.values()].filter((row) => !row.isDestroyed()).map((row) => row.getBounds()),
      colors: { surface: surfaceColor, chrome: nativeBrowser?.chromeColor || surfaceColor, text: nativeBrowser?.textColor || '' }
    }, callbacks),
    loadRecency,
    saveRecency,
    hooks: {
      onViewCreated: (entry) => { nativeBrowser?.addViewHost(entry.view, entry.window); },
      onViewDestroyed: (entry) => { nativeBrowser?.removeViewHost(entry.view); },
      onPresentationChanged: () => { nativeBrowser?.layout(); },
      onViewMoved: (entry, window) => { nativeBrowser?.viewMoved(entry.view, window); },
      onViewResized: (entry) => { nativeBrowser?.resize(nativeBrowser.viewHosts.get(entry.view)); }
    }
  });
  if (desktopSupported()) nativeBrowser = new NativeBrowser(mainWindow, origin, views);
  layoutViews();

  installNavigationGuards(chromeView);
  chromeView.webContents.loadURL(chromeUrl);
  // chrome 异常退出时若正在拖出：按 drop 处理（不丢标签）。
  chromeView.webContents.on('render-process-gone', () => { views?.endDragAsDrop(); });
  // 打开特定路由（快捷对话设置）：经 chrome 发 `page` 命令；首个快照前排队。
  if (route && route !== '/') views.requestPage(route);

  // 主窗口获得焦点：请 chrome 以当前 active 标签发 focus 命令（CrossPet 最近聚焦标签）。
  mainWindow.on('focus', () => {
    const key = views?.activeKey;
    if (key) views.sendChrome('shell:window-focus', { key });
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
    if (!appQuitting && !isUpdaterInstalling()) { event.preventDefault(); mainWindow.hide(); views?.hideDetached(); }
  });

  mainWindow.on('closed', () => {
    nativeBrowser?.dispose();
    nativeBrowser = null;
    // BaseWindow does not own WebContentsView lifetimes：内容视图与 chrome 由 ViewManager 逐个关闭。
    // 独立对话窗口随 ViewManager.dispose 一并销毁（placement 已持久化，下次启动恢复）。
    views?.dispose();
    mainWindow = null;
    views = null;
    chromeView = null;
    // 真正退出由应用/托盘菜单发起；意外关窗也保留后台调度。
  });

  return mainWindow;
}

export function desktopWindowState() {
  if (!mainWindow || mainWindow.isDestroyed()) throw new Error('window_missing');
  return { maximized: mainWindow.isMaximized() };
}

export function desktopWindowControl(action) {
  if (!mainWindow || mainWindow.isDestroyed()) throw new Error('window_missing');
  switch (action) {
    case 'minimize': mainWindow.minimize(); break;
    case 'maximize-toggle':
      if (mainWindow.isMaximized()) mainWindow.unmaximize(); else mainWindow.maximize();
      break;
    case 'close': mainWindow.close(); break;
    default: throw new Error('invalid_window_action');
  }
  return { success: true };
}
