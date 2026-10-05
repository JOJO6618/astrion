import { BrowserWindow, ipcMain, screen } from 'electron';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { capturePermission, captureRegion, captureWindow, listCaptureWindows, prepareNativeCapture } from './screenshot.js';
import { configureWorkspaceVisibility } from './workspace-visibility.js';
const here = path.dirname(fileURLToPath(import.meta.url));

export class CaptureController {
  constructor(quickWindow, dismiss) {
    this.quick = quickWindow;
    this.dismiss = dismiss;
    this.overlays = [];
    this.regions = [];
    this.windows = [];
    this.presentation = {};
    this.generation = 0;
    this.capturing = false;
    this.dismissing = false;
    this.nativeReady = prepareNativeCapture();
    this.nativeReady.catch(error => console.warn('[astrion-quick] 截图初始化失败:', error.message));
    ipcMain.on('quick:selection', (event, rect) => this.finish(event, rect));
    ipcMain.on('quick:window-selection', (event, id) => this.finishWindow(event, id));
    ipcMain.on('quick:selection-cancel', event => { if (this.senderOverlay(event)) this.dismiss(); });
    ipcMain.on('quick:outside-click', event => { if (this.senderOverlay(event)) this.dismiss(); });
    ipcMain.on('quick:overlay-dismissed', (event, ticket) => {
      const item = this.overlays.find(item => !item.window.isDestroyed()
        && item.window.webContents === event.sender && event.senderFrame === event.sender.mainFrame);
      if (item) this.finishDismiss(item, ticket);
    });
  }
  async permission(request = false) {
    return capturePermission(await this.nativeReady, request);
  }
  senderOverlay(event) {
    if (this.dismissing) return;
    return this.overlays.find(item => !item.window.isDestroyed() && item.window.webContents === event.sender
      && event.senderFrame === event.sender.mainFrame);
  }
  async prepare() {
    this.close();
    const ticket = this.generation;
    try {
      // Discovery does not gate the transparent input layers on recording permission.
      for (const display of screen.getAllDisplays()) {
        const overlay = new BrowserWindow({ ...display.bounds, frame: false, transparent: true, show: false,
          hasShadow: false, resizable: false, alwaysOnTop: true, skipTaskbar: process.platform !== 'darwin',
          webPreferences: { preload: path.join(here, 'capture-preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true } });
        configureWorkspaceVisibility(overlay);
        overlay.setAlwaysOnTop(true, 'floating');
        this.overlays.push({ window: overlay, display, ready: false });
        await overlay.loadFile(path.join(here, 'capture.html'));
        if (ticket !== this.generation) { if (!overlay.isDestroyed()) overlay.destroy(); return; }
        this.overlays.find(item => item.window === overlay).ready = true;
        this.updateExclusion();
        overlay.showInactive();
      }
      this.updateExclusion();
      void this.refreshWindows(ticket);
    } catch (error) {
      if (ticket === this.generation) {
        console.warn('[astrion-quick] 截图准备失败:', error.message);
        this.close();
      }
    }
  }
  setPresentation(presentation) {
    if (!presentation || typeof presentation !== 'object') return;
    this.presentation = {
      label: typeof presentation.label === 'string' ? presentation.label.slice(0, 300) : '',
      failure: typeof presentation.failure === 'string' ? presentation.failure.slice(0, 500) : '',
      colors: Object.fromEntries(Object.entries(presentation.colors || {}).filter(([key, value]) =>
        /^--quick-(?:entry|metal)-[a-z0-9-]+$/.test(key) && typeof value === 'string' && value.length < 1000))
    };
    this.updateExclusion();
  }
  updateExclusion() {
    // Keep button geometry and clipping fixed during the prompt's scale exit.
    if (this.dismissing || !this.quick || this.quick.isDestroyed()) return;
    const bounds = this.quick.getBounds();
    for (const item of this.overlays) {
      if (!item.ready || item.window.isDestroyed()) continue;
      const exclude = this.regions.map(rect => ({
        x: bounds.x + rect.x - item.display.bounds.x, y: bounds.y + rect.y - item.display.bounds.y,
        width: rect.width, height: rect.height
      }));
      item.window.webContents.send('quick:exclude', exclude);
      item.window.webContents.send('quick:windows', {
        windows: this.windows, display: item.display.bounds, exclude,
        capturing: this.capturing, ...this.presentation
      });
    }
  }
  async refreshWindows(ticket) {
    try {
      const binary = await this.nativeReady;
      if (ticket !== this.generation) return;
      const windows = await listCaptureWindows(binary);
      if (ticket !== this.generation) return;
      const changed = JSON.stringify(windows) !== JSON.stringify(this.windows);
      this.windows = windows;
      if (changed) this.updateExclusion();
    } catch (error) {
      if (ticket !== this.generation) return;
      if (!this.discoveryFailed) console.warn('[astrion-quick] 窗口列表读取失败:', error.message);
      this.discoveryFailed = true;
      this.windows = [];
      this.updateExclusion();
    } finally {
      if (ticket === this.generation && this.overlays.length) {
        this.windowTimer = setTimeout(() => void this.refreshWindows(ticket), 600);
      }
    }
  }
  async finish(event, rect) {
    const item = this.senderOverlay(event);
    if (!item || !rect || !['x', 'y', 'width', 'height'].every(key => Number.isFinite(rect[key]))) return;
    if (rect.width < 3 || rect.height < 3 || rect.x < 0 || rect.y < 0
      || rect.x + rect.width > item.display.bounds.width || rect.y + rect.height > item.display.bounds.height) return;
    await this.deliverCapture(binary => captureRegion(binary, item.display, rect));
  }
  async finishWindow(event, id) {
    if (!this.senderOverlay(event)) return;
    if (!Number.isSafeInteger(id) || id <= 0
      || !this.windows.some(item => item.id === id && item.candidate)) {
      this.updateExclusion();
      return;
    }
    await this.deliverCapture(binary => captureWindow(binary, id));
  }
  async deliverCapture(capture) {
    if (this.capturing || this.quick.isDestroyed()) return;
    const ticket = this.generation;
    // ScreenCaptureKit excludes our layers in the capture filter. Preserve the
    // existing windows and button nodes rather than flashing them through rearm.
    this.capturing = true;
    this.updateExclusion();
    this.quick.focus();
    try {
      const binary = await this.nativeReady;
      if (ticket !== this.generation || this.quick.isDestroyed()) return;
      const image = await capture(binary);
      if (ticket !== this.generation || this.quick.isDestroyed()) return;
      this.quick.webContents.send('quick:capture', image);
    } catch (error) {
      const detail = (Buffer.isBuffer(error.stderr) ? error.stderr.toString('utf8') : String(error.stderr || error.message)).trim().slice(0, 2500);
      console.warn('[astrion-quick] 截图失败:', detail);
      if (ticket === this.generation && !this.quick.isDestroyed()) {
        this.quick.webContents.send('quick:capture-error', [this.presentation.failure, detail].filter(Boolean).join('\n\n'));
      }
    } finally {
      if (ticket === this.generation) {
        this.capturing = false;
        this.updateExclusion();
      }
    }
  }
  dismissOverlays() {
    if (this.dismissing) return;
    this.dismissing = true;
    const ticket = ++this.generation;
    this.capturing = false;
    clearTimeout(this.windowTimer);
    this.windowTimer = null;
    for (const item of [...this.overlays]) {
      if (item.window.isDestroyed() || !item.ready) { this.finishDismiss(item, ticket); continue; }
      item.window.setIgnoreMouseEvents(true);
      item.window.webContents.send('quick:overlay-dismiss', ticket);
      // Renderer normally acknowledges the last button; clean up stalled layers.
      item.dismissTimer = setTimeout(() => this.finishDismiss(item, ticket), 5000);
    }
  }
  finishDismiss(item, ticket) {
    if (!this.dismissing || ticket !== this.generation || !this.overlays.includes(item)) return;
    clearTimeout(item.dismissTimer);
    if (!item.window.isDestroyed()) item.window.destroy();
    this.overlays = this.overlays.filter(overlay => overlay !== item);
  }
  close() {
    this.generation += 1;
    this.capturing = false;
    this.dismissing = false;
    clearTimeout(this.windowTimer);
    this.windowTimer = null;
    this.windows = [];
    for (const item of this.overlays) {
      clearTimeout(item.dismissTimer);
      if (!item.window.isDestroyed()) item.window.destroy();
    }
    this.overlays = [];
  }
}
