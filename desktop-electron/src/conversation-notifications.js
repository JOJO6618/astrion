import { app, BrowserWindow, ipcMain, screen, session } from 'electron';
import { fileURLToPath } from 'node:url';
import { createConversationActivityTracker, notificationBounds, shouldShowConversationNotification } from './conversation-activity.js';
import { configureWorkspaceVisibility } from './quick/workspace-visibility.js';

const preload = fileURLToPath(new URL('./preload-conversation-notifications.cjs', import.meta.url));
const POLL_MS = 1500;
let updateBannerTheme;

export function broadcastConversationNotificationTheme(theme) {
  updateBannerTheme?.(theme);
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

  function stopAttention() {
    clearTimeout(attentionTimer);
    attentionTimer = undefined;
    if (bounceId >= 0) app.dock?.cancelBounce(bounceId);
    bounceId = undefined;
    if (flashingWindow && !flashingWindow.isDestroyed()) flashingWindow.flashFrame(false);
    flashingWindow = undefined;
  }

  function requestAttention(row) {
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
  app.on('browser-window-focus', stopAttention);
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
    stopAttention();
    app.off('browser-window-focus', stopAttention);
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
