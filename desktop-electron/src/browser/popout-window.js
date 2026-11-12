// 对话浏览器的独立窗口：普通 BaseWindow + 一个可信 renderer（/browser-window，复用共享
// BrowserPanel）。网页视图由 NativeBrowser 在弹出/收回时挪入挪出；本模块只管窗口本身、
// 标题（renderer 的 document.title = 对话标题）与大小位置记忆。
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { app, BaseWindow, WebContentsView, screen } from 'electron';
import { BrowserHost, POPOUT_ROUTE } from './host.js';

const preload = fileURLToPath(new URL('../preload-browser-window.cjs', import.meta.url));
const DEFAULT_SIZE = { width: 1100, height: 780 };
const MIN_SIZE = { width: 640, height: 420 };
const CASCADE = 28;

const boundsFile = () => path.join(app.getPath('userData'), 'browser-window.json');
function savedBounds() {
  try {
    const value = JSON.parse(fs.readFileSync(boundsFile(), 'utf8'));
    if (['x', 'y', 'width', 'height'].every(key => Number.isFinite(value?.[key]))) return value;
  } catch { /* 首次使用或文件损坏时按默认位置 */ }
  return null;
}
function writeBounds(bounds) {
  try { fs.writeFileSync(boundsFile(), JSON.stringify(bounds)); } catch { /* 记忆失败不影响窗口 */ }
}

/** 所有独立窗口共用一份记忆；与已打开的窗口重叠时错开，并保证落在某个屏幕的工作区内。 */
function placement(occupied) {
  const saved = savedBounds();
  const area = (saved ? screen.getDisplayMatching(saved) : screen.getDisplayNearestPoint(screen.getCursorScreenPoint())).workArea;
  const bounds = saved ? { ...saved } : {
    width: DEFAULT_SIZE.width, height: DEFAULT_SIZE.height,
    x: area.x + Math.round((area.width - DEFAULT_SIZE.width) / 2), y: area.y + Math.round((area.height - DEFAULT_SIZE.height) / 2)
  };
  for (let i = 0; i < 20 && occupied.some(row => Math.abs(row.x - bounds.x) < 8 && Math.abs(row.y - bounds.y) < 8); i++) {
    bounds.x += CASCADE; bounds.y += CASCADE;
  }
  bounds.width = Math.round(Math.max(MIN_SIZE.width, Math.min(bounds.width, area.width)));
  bounds.height = Math.round(Math.max(MIN_SIZE.height, Math.min(bounds.height, area.height)));
  bounds.x = Math.round(Math.max(area.x, Math.min(bounds.x, area.x + area.width - bounds.width)));
  bounds.y = Math.round(Math.max(area.y, Math.min(bounds.y, area.y + area.height - bounds.height)));
  return bounds;
}

export function focusPopoutWindow(host) {
  const window = host.window;
  if (window.isDestroyed()) return;
  if (window.isMinimized()) window.restore();
  window.show(); window.focus();
  if (!host.view.webContents.isDestroyed()) host.view.webContents.focus();
}

export function createPopoutWindow(manager, { owner, profile }) {
  const [mode, workspace, conversation] = JSON.parse(owner.slice(owner.indexOf(':') + 1));
  const bounds = placement([...manager.popouts.values()].filter(row => !row.window.isDestroyed()).map(row => row.window.getBounds()));
  const window = new BaseWindow({
    ...bounds, minWidth: MIN_SIZE.width, minHeight: MIN_SIZE.height, show: false,
    backgroundColor: manager.surfaceColor,
    // 红绿灯悬浮在 46px 标签栏左侧，垂直居中（按钮高 16）。
    titleBarStyle: 'hidden', trafficLightPosition: { x: 15, y: 15 }
  });
  window.contentView.setBackgroundColor(manager.surfaceColor);
  const view = new WebContentsView({ webPreferences: { contextIsolation: true, sandbox: true, nodeIntegration: false, spellcheck: true, preload } });
  view.setBackgroundColor(manager.surfaceColor);
  window.contentView.addChildView(view);
  const host = new BrowserHost(manager, { kind: 'popout', window, view, owner, profile });

  const fit = () => {
    if (window.isDestroyed()) return;
    const { width, height } = window.getContentBounds();
    view.setBounds({ x: 0, y: 0, width, height });
    if (manager.popouts.get(host.owner) === host) manager.resize(host);
  };
  const remember = () => {
    if (window.isDestroyed() || window.isFullScreen() || window.isMinimized()) return;
    writeBounds(window.getNormalBounds());
  };
  fit();
  for (const event of ['resize', 'enter-full-screen', 'leave-full-screen', 'maximize', 'unmaximize']) window.on(event, fit);
  // 全屏时系统隐藏红绿灯，标签栏左侧不再为它留白（与 Chrome 一致）。
  const windowState = () => host.send('browser:window-state', { fullScreen: !window.isDestroyed() && window.isFullScreen() });
  window.on('enter-full-screen', windowState);
  window.on('leave-full-screen', windowState);
  window.on('moved', remember);
  window.on('resized', remember);
  window.on('close', () => { remember(); manager.undock(host); });
  window.on('closed', () => { if (!view.webContents.isDestroyed()) view.webContents.close(); });

  const wc = view.webContents;
  // 可信界面页不导航、不开新窗口；对话标题由页面 document.title 提供。
  wc.on('will-navigate', event => event.preventDefault());
  wc.setWindowOpenHandler(() => ({ action: 'deny' }));
  wc.on('page-title-updated', (_event, title) => { if (!window.isDestroyed()) window.setTitle(title); });
  wc.on('did-finish-load', () => { windowState(); if (!window.isDestroyed() && window.isFocused()) wc.focus(); });
  const query = new URLSearchParams({ mode, workspace, conversation });
  void wc.loadURL(`${manager.origin}${POPOUT_ROUTE}?${query}`);
  window.show(); window.focus();
  return host;
}
