import { app, BrowserWindow, globalShortcut, ipcMain, Menu, nativeImage, screen, shell, Tray } from 'electron';
import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { QuickGateway } from './gateway.js';
import { startQuickAssets } from './assets.js';
import { CaptureController } from './capture.js';
import { advertiseGateway, discoverGateway } from './discovery.js';
import { quickCanvasBounds } from './geometry.js';
import { InputRegions } from './input-regions.js';
import { QuickVisibility } from './visibility.js';
import { inputPermission, resolveListenerBinary } from './listener.js';
import { configureWorkspaceVisibility } from './workspace-visibility.js';
import { isUpdaterInstalling } from '../updater-state.js';

const here = path.dirname(fileURLToPath(import.meta.url));
let quickWindow, tray, listener, capture;
let quitting = false;
let anchorDisplayId, inputRegions, visibility;
let config = { enabled: false, modifier: 'option', workspace: '', model: '' };
let gateway;
let quickRendererReady;
const settingsPath = path.join(os.homedir(), '.astrion/quick-entry.json');

export function quickEnabled() { return config.enabled; }
export async function startQuickEntry({ debug = false, port, dataRoot, workspacePath, openMain, getMainView } = {}) {
  try { config = { ...config, ...JSON.parse(await fs.readFile(settingsPath, 'utf8')) }; } catch (error) { if (error.code !== 'ENOENT') throw error; }
  if (debug) config.enabled = true;
  if (debug && port === undefined && dataRoot === undefined) {
    const existing = await discoverGateway();
    port = existing.port; dataRoot = existing.dataRoot;
  }
  gateway = new QuickGateway({ port, dataRoot, workspacePath });
  if (!debug) await advertiseGateway(gateway.port, gateway.root);
  const openDesktop = route => { hideQuickEntry(); openMain?.(route); };
  const refreshTray = () => {
    if (!tray || tray.isDestroyed()) return;
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: 'Astrion 快捷对话', enabled: config.enabled, click: () => void showQuickEntry() },
      ...(openMain ? [
        { label: '打开主窗口', click: () => openDesktop() },
        { label: '快捷对话设置', click: () => openDesktop('/settings/quick-chat') }
      ] : []),
      { type: 'separator' }, { label: '退出 Astrion', click: () => app.quit() }
    ]));
  };
  const assets = await startQuickAssets();
  let resolveRendererReady;
  let rejectRendererReady;
  const rendererReady = new Promise((resolve, reject) => {
    resolveRendererReady = resolve;
    rejectRendererReady = reject;
  });
  quickRendererReady = rendererReady;
  rendererReady.catch(error => console.error('[astrion-quick] 快捷页面初始化失败:', error.message));
  quickWindow = new BrowserWindow({ width: 620, height: 440, show: false, frame: false, transparent: true,
    resizable: false, hasShadow: false, alwaysOnTop: true, skipTaskbar: process.platform !== 'darwin',
    webPreferences: { preload: path.join(here, 'preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true, backgroundThrottling: false } });
  const readyTimeout = setTimeout(() => rejectRendererReady(new Error('Quick Chat renderer did not become ready')), 15000);
  rendererReady.then(() => clearTimeout(readyTimeout), () => clearTimeout(readyTimeout));
  quickWindow.webContents.on('did-fail-load', (_event, code, description, _url, isMainFrame) => {
    if (isMainFrame) rejectRendererReady(new Error(`Quick Chat load failed (${code}): ${description}`));
  });
  quickWindow.webContents.on('preload-error', (_event, _path, error) => rejectRendererReady(error));
  quickWindow.webContents.on('render-process-gone', (_event, details) => {
    rejectRendererReady(new Error(`Quick Chat renderer exited: ${details.reason}`));
    console.error('[astrion-quick] 快捷页面进程退出:', details.reason);
    hideQuickEntry();
  });
  quickWindow.webContents.on('console-message', (event) => {
    if (event.level === 'error') console.error('[astrion-quick] 快捷页面错误:', event.message);
  });
  configureWorkspaceVisibility(quickWindow);
  // Stay below system input-method candidates, above the selection overlays.
  quickWindow.setAlwaysOnTop(true, 'floating', 1);
  inputRegions = new InputRegions(quickWindow, screen);
  screen.on('display-metrics-changed', (event, display) => {
    if (display.id !== anchorDisplayId || quickWindow.isDestroyed()) return;
    quickWindow.setBounds(quickCanvasBounds(display.workArea));
    capture?.updateExclusion();
  });
  quickWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  quickWindow.webContents.on('will-navigate', (event, url) => { if (url !== assets.url) event.preventDefault(); });
  // 同主窗口：安装更新时必须放行关闭。quitting 只在 before-quit 里置位，而
  // quitAndInstall() 是先关窗口、后发 before-quit，只靠 quitting 会拦出一个退不掉的
  // 快捷窗，导致 ShipIt 判定“仍有实例在运行”而放弃安装。
  quickWindow.on('close', (event) => { if (!quitting && !isUpdaterInstalling()) { event.preventDefault(); hideQuickEntry(); } });
  capture = new CaptureController(quickWindow, hideQuickEntry);
  visibility = new QuickVisibility(quickWindow, () => capture.dismissOverlays());
  const trusted = (event) => event.senderFrame === event.sender.mainFrame && (
    event.sender === quickWindow.webContents ||
    (event.sender === getMainView?.()?.webContents && event.sender.getURL().startsWith(gateway.base + '/'))
  );
  ipcMain.handle('quick:open', async (event) => {
    if (!trusted(event)) throw new Error('Untrusted sender');
    if (!config.enabled) throw new Error('Quick Chat is disabled');
    await rendererReady;
    await showQuickEntry();
  });
  ipcMain.on('quick:ready', (event) => {
    if (trusted(event) && event.sender === quickWindow.webContents) resolveRendererReady();
  });
  let connection;
  ipcMain.handle('quick:request', async (event, request) => {
    if (!trusted(event) || event.sender !== quickWindow.webContents) throw new Error('Untrusted sender');
    connection ||= gateway.ensureRunning().catch(error => { connection = null; throw error; });
    await connection;
    return gateway.request(request.route, request.method, request.body, request.workspace);
  });
  ipcMain.handle('quick:info', async (event) => {
    if (!trusted(event)) throw new Error('Untrusted sender');
    return { ...config, debug };
  });
  ipcMain.handle('quick:permissions', async (event) => {
    if (!trusted(event)) throw new Error('Untrusted sender');
    const [screenStatus, inputStatus] = await Promise.allSettled([capture.permission(), inputPermission()]);
    if (inputStatus.status === 'fulfilled' && inputStatus.value === 'granted' && !listener && config.enabled) installListener();
    return {
      screenPermission: screenStatus.status === 'fulfilled' ? screenStatus.value : 'unknown',
      inputPermission: inputStatus.status === 'fulfilled' ? inputStatus.value : 'unknown',
      error: [screenStatus, inputStatus].filter(result => result.status === 'rejected').map(result => String(result.reason)).join('\n')
    };
  });
  ipcMain.handle('quick:input-permission', async (event) => {
    if (!trusted(event)) throw new Error('Untrusted sender');
    hideQuickEntry();
    const status = await inputPermission(true);
    if (status === 'granted') installListener();
    else await shell.openExternal('x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent');
    return status;
  });
  ipcMain.handle('quick:configure', async (event, patch) => {
    if (!trusted(event)) throw new Error('Untrusted sender');
    const previous = { ...config };
    for (const key of ['enabled', 'modifier', 'workspace', 'model']) {
      if (key in patch && (key === 'enabled' ? typeof patch[key] === 'boolean' : typeof patch[key] === 'string')) config[key] = patch[key];
    }
    if (!['option', 'command', 'control'].includes(config.modifier)) config.modifier = 'option';
    await fs.mkdir(path.dirname(settingsPath), { recursive: true });
    const temporary = `${settingsPath}.${process.pid}.tmp`;
    await fs.writeFile(temporary, JSON.stringify(config, null, 2), { mode: 0o600 });
    await fs.rename(temporary, settingsPath);
    if (previous.enabled !== config.enabled || previous.modifier !== config.modifier) installListener();
    if (config.enabled) globalShortcut.register('CommandOrControl+Shift+Space', showQuickEntry);
    else { globalShortcut.unregister('CommandOrControl+Shift+Space'); hideQuickEntry(); }
    refreshTray();
    return config;
  });
  ipcMain.on('quick:hide', (event) => { if (trusted(event)) hideQuickEntry(); });
  ipcMain.on('quick:hidden', (event) => {
    if (trusted(event) && event.sender === quickWindow.webContents) visibility.finish();
  });
  ipcMain.on('quick:layout', (event, regions, presentation) => {
    if (!trusted(event) || event.sender !== quickWindow.webContents) return;
    capture.setPresentation(presentation);
    if (inputRegions.update(regions)) {
      capture.regions = inputRegions.regions;
      capture.updateExclusion();
    }
  });
  ipcMain.on('quick:pointer', (event) => {
    if (trusted(event) && event.sender === quickWindow.webContents) inputRegions.syncCursor();
  });
  ipcMain.handle('quick:capture-permission', async (event) => {
    if (!trusted(event)) throw new Error('Untrusted sender');
    hideQuickEntry();
    const status = await capture.permission(true);
    if (status !== 'granted') await shell.openExternal('x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture');
    return status;
  });
  await quickWindow.loadURL(assets.url);
  const trayImage = nativeImage.createFromDataURL('data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=');
  tray = new Tray(trayImage);
  tray.setTitle('⬡');
  tray.setToolTip('Astrion');
  refreshTray();
  tray.on('click', () => config.enabled ? void showQuickEntry() : openDesktop('/settings/quick-chat'));
  if (!app.isPackaged) await fs.mkdir(path.resolve(here, '../../.cache'), { recursive: true });
  installListener();
  if (config.enabled) globalShortcut.register('CommandOrControl+Shift+Space', showQuickEntry);
  app.on('before-quit', () => { quitting = true; visibility.dispose(); inputRegions.dispose(); capture.close(); listener?.kill(); globalShortcut.unregisterAll(); assets.close(); });
  const modifierLabel = { option: 'Option', command: 'Command', control: 'Control' }[config.modifier];
  console.info(`[astrion-quick] ${debug ? '源码调试入口' : '快捷模式客户端'}已就绪；双击 ${modifierLabel} 唤起，备用快捷键 Cmd+Shift+Space（启用快捷模式后生效）。`);
  if (debug) void showQuickEntry();
}

function installListener() {
  listener?.kill(); listener = null;
  if (!config.enabled || process.platform !== 'darwin') return;
  let binary;
  try { binary = resolveListenerBinary(); }
  catch (error) { console.error('[astrion-quick] 原生快捷键监听编译失败:', error.message); return; }
  listener = spawn(binary, [config.modifier], { stdio: ['ignore', 'pipe', 'pipe'] });
  const activeListener = listener;
  activeListener.on('exit', () => { if (listener === activeListener) listener = null; });
  let buffer = '';
  listener.stdout.on('data', (chunk) => {
    buffer += chunk;
    const lines = buffer.split('\n'); buffer = lines.pop();
    for (const line of lines) {
      if (line === 'toggle') visibility.requested ? hideQuickEntry() : void showQuickEntry();
      if (line === 'permission-required') console.warn('[astrion-quick] 请在系统设置→隐私与安全→输入监控中允许调试进程（Terminal/Electron/监听程序），重启调试后双击键生效。');
    }
  });
  listener.on('error', (error) => console.error('[astrion-quick] 原生监听不可用:', error.message));
}

export async function showQuickEntry() {
  // 安装更新期间不再唤起：应用正在退出，界面出现只会干扰 ShipIt 的“无运行实例”判定。
  if (!config.enabled || !quickWindow || quickWindow.isDestroyed() || quitting || isUpdaterInstalling()) return;
  try { await quickRendererReady; }
  catch (error) { console.error('[astrion-quick] 无法唤起快捷对话:', error.message); return; }
  if (!config.enabled || quickWindow.isDestroyed() || quitting || isUpdaterInstalling()) return;
  if (!visibility.requested) {
    const display = screen.getDisplayNearestPoint(screen.getCursorScreenPoint());
    anchorDisplayId = display.id;
    quickWindow.setBounds(quickCanvasBounds(display.workArea));
    void capture.prepare();
  }
  visibility.show(); inputRegions.syncCursor();
}
export function hideQuickEntry() { visibility?.hide(); }
