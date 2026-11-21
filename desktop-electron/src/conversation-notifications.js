import { app, BrowserWindow, ipcMain, screen, session } from 'electron';
import { fileURLToPath } from 'node:url';
import { createConversationActivityTracker, notificationBounds, shouldShowConversationNotification } from './conversation-activity.js';
import { getNotificationSettings, onNotificationSettingsChange } from './notification-settings.js';
import { configureWorkspaceVisibility } from './quick/workspace-visibility.js';

const preload = fileURLToPath(new URL('./preload-conversation-notifications.cjs', import.meta.url));
const POLL_MS = 1500;
// 聚焦后撤横幅前留一个点击手势的宽限：点击横幅本身会先激活应用（mousedown 即可能触发
// 主窗口 focus），若立刻卸载横幅，这次点击的 click 事件会随按钮卸载而丢失。
const FOCUS_CLEAR_MS = 300;
let updateBannerTheme;
let windowFocused;

export function broadcastConversationNotificationTheme(theme) {
  updateBannerTheme?.(theme);
}

/**
 * 任一 Astrion 窗口获得焦点即视为用户回到应用（撤掉残留横幅、停止提醒）。
 * 主窗口、独立对话窗口、独立浏览器窗口都是 BaseWindow，而 Electron 的 app 级
 * `browser-window-focus` 只覆盖 BrowserWindow，所以这几类窗口在各自创建处登记一次。
 * 这里只转发事件，不反向依赖 views/browser，避免循环初始化。
 */
export function watchConversationNotificationFocus(window) {
  if (!window || window.isDestroyed?.()) return;
  window.on('focus', () => windowFocused?.(window));
}

export function startConversationNotifications(port, { focusedWindow, openConversation, targetWindow }) {
  const origin = `http://127.0.0.1:${port}`;
  const notices = new Map();
  const updateActivity = createConversationActivityTracker();
  let noticeSequence = 0;
  let panel;
  let panelReady = false;
  let displayId;
  let timer;
  let stopped = false;
  let attentionTimer;
  let flashingWindow;
  let bounceId;
  let pollFailed = false;
  // Astrion 弹窗通知开关（设置页「通用 → 通知」）：关闭后只停横幅与 Dock/任务栏提醒。
  let notificationsEnabled = getNotificationSettings().conversationNotifications !== false;

  function stopAttention() {
    clearTimeout(attentionTimer);
    attentionTimer = undefined;
    if (bounceId >= 0) app.dock?.cancelBounce(bounceId);
    bounceId = undefined;
    if (flashingWindow && !flashingWindow.isDestroyed()) flashingWindow.flashFrame(false);
    flashingWindow = undefined;
  }

  // 用户回到应用（任意 Astrion 窗口获得焦点）：停止提醒，并撤掉已显示的横幅。
  // 撤除略延一个点击宽限（FOCUS_CLEAR_MS）：点击横幅本身会先激活应用（mousedown 即可能
  // 触发主窗口 focus），若立刻卸载横幅，这次点击的 click 事件会随按钮卸载而丢失。
  // 宽限内只清理聚焦时已存在的横幅，期间新到的通知不受影响。
  let focusClearTimer;
  const focusExpired = new Set();
  function onWindowFocused(window) {
    if (stopped || window === panel) return;
    stopAttention();
    if (!notices.size) return;
    for (const id of notices.keys()) focusExpired.add(id);
    if (focusClearTimer) return;
    focusClearTimer = setTimeout(() => {
      focusClearTimer = undefined;
      if (stopped) { focusExpired.clear(); return; }
      let dismissed = false;
      for (const id of focusExpired) {
        if (notices.delete(id)) dismissed = true;
      }
      focusExpired.clear();
      if (dismissed) render();
    }, FOCUS_CLEAR_MS);
  }
  windowFocused = onWindowFocused;

  function requestAttention(row) {
    if (!notificationsEnabled) return;
    if (attentionTimer) return;
    if (process.platform === 'darwin') {
      bounceId = app.dock?.bounce('informational');
    } else if (process.platform === 'win32') {
      const window = targetWindow(row);
      if (!window || window.isDestroyed()) return;
      flashingWindow = window;
      window.flashFrame(true);
    } else {
      return;
    }
    attentionTimer = setTimeout(stopAttention, 1800);
  }

  function createPanel(bounds) {
    const win = new BrowserWindow({
      ...bounds, show: false, frame: false, transparent: true, hasShadow: false,
      alwaysOnTop: true, focusable: false, resizable: false, movable: false,
      fullscreenable: false, skipTaskbar: process.platform === 'win32',
      acceptFirstMouse: true,
      webPreferences: { preload, contextIsolation: true, sandbox: true, nodeIntegration: false },
    });
    panel = win;
    configureWorkspaceVisibility(win);
    win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    win.webContents.on('will-navigate', (event) => event.preventDefault());
    win.webContents.on('render-process-gone', () => { if (!win.isDestroyed()) win.destroy(); });
    win.on('closed', () => {
      if (panel !== win) return;
      panel = undefined;
      panelReady = false;
    });
    void win.loadURL(`${origin}/chrome`).catch((error) => {
      if (!win.isDestroyed()) {
        console.warn('[astrion-desktop] Conversation notification window:', error.message);
        win.destroy();
      }
    });
  }

  // 关闭时立即收起现有横幅并停止 Dock/任务栏提醒；轮询仍在跑 updateActivity，
  // 关闭期间的事件被视为已跟踪，重新开启后不会补弹。
  function applyNotificationsEnabled(enabled) {
    if (enabled === notificationsEnabled) return;
    notificationsEnabled = enabled;
    if (enabled) return;
    stopAttention();
    if (notices.size) {
      notices.clear();
      render();
    }
  }
  const stopNotificationSettings = onNotificationSettingsChange((settings) => {
    if (!stopped) applyNotificationsEnabled(settings.conversationNotifications !== false);
  });
  app.once('before-quit', stopNotificationSettings);

  function render() {
    if (stopped) return;
    if (!notices.size) {
      panel?.hide();
      displayId = undefined;
      return;
    }
    const display = screen.getAllDisplays().find((candidate) => candidate.id === displayId)
      ?? screen.getDisplayNearestPoint(screen.getCursorScreenPoint());
    displayId = display.id;
    const { bounds, visible } = notificationBounds(display.workArea, notices.size);
    if (!panel) createPanel(bounds);
    if (!panelReady) return;
    const banners = [...notices.values()].reverse().slice(0, visible).map(({ id, type, row }) => ({
      id, type, title: row.title, workspaceLabel: row.workspace_label,
    }));
    panel.setBounds(bounds);
    panel.webContents.send('conversation-notification:banners', banners);
    if (!panel.isVisible()) panel.showInactive();
  }

  function fromPanel(event) {
    return panel?.webContents === event.sender && event.senderFrame === event.sender.mainFrame;
  }
  function onReady(event) {
    if (!fromPanel(event)) return;
    panelReady = true;
    render();
  }
  function onOpen(event, id) {
    if (!fromPanel(event)) return;
    const notice = notices.get(id);
    if (notice) openConversation(notice.row);
  }
  function onDismiss(event, id) {
    if (fromPanel(event) && notices.delete(id)) render();
  }
  updateBannerTheme = (theme) => {
    if (panelReady) panel.webContents.send('shell:theme-changed', theme);
  };
  ipcMain.on('conversation-notification:ready', onReady);
  ipcMain.on('conversation-notification:open', onOpen);
  ipcMain.on('conversation-notification:dismiss', onDismiss);
  // BrowserWindow 类窗口（快捷对话、迁移/更新策略窗口等）由 app 事件覆盖；面板 focusable:false
  // 不会被自己聚焦。BaseWindow 类窗口见 watchConversationNotificationFocus。
  const onAppWindowFocused = (_event, window) => onWindowFocused(window);
  app.on('browser-window-focus', onAppWindowFocused);
  screen.on('display-removed', render);
  screen.on('display-metrics-changed', render);

  async function poll() {
    try {
      const response = await session.defaultSession.fetch(`${origin}/api/conversation-tabs/activity`, {
        signal: AbortSignal.timeout(8000),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const { success, data } = await response.json();
      if (!success || !Array.isArray(data)) throw new Error('Invalid conversation activity');
      pollFailed = false;
      if (stopped) return;
      let attentionTarget;
      for (const event of updateActivity(data)) {
        // 关闭期间也执行 updateActivity（在上方），只是不展示、不提醒。
        if (!notificationsEnabled) continue;
        if (!shouldShowConversationNotification(focusedWindow(), event.row)) continue;
        const id = String(++noticeSequence);
        notices.set(id, { ...event, id });
        attentionTarget ??= event.row;
      }
      if (attentionTarget || (!panel && notices.size)) render();
      if (attentionTarget) requestAttention(attentionTarget);
    } catch (error) {
      if (!stopped && !pollFailed) console.warn('[astrion-desktop] Conversation notification poll:', error.message);
      pollFailed = true;
    } finally {
      if (!stopped) timer = setTimeout(poll, POLL_MS);
    }
  }

  app.once('before-quit', () => {
    stopped = true;
    clearTimeout(timer);
    clearTimeout(focusClearTimer);
    stopAttention();
    windowFocused = undefined;
    app.off('browser-window-focus', onAppWindowFocused);
    screen.off('display-removed', render);
    screen.off('display-metrics-changed', render);
    ipcMain.off('conversation-notification:ready', onReady);
    ipcMain.off('conversation-notification:open', onOpen);
    ipcMain.off('conversation-notification:dismiss', onDismiss);
    updateBannerTheme = undefined;
    panel?.destroy();
  });
  void poll();
}
