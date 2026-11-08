import path from 'node:path';
import fs from 'node:fs';
import { randomUUID } from 'node:crypto';
import { app, ipcMain, dialog } from 'electron';
import { BrowserProfile } from './profile.js';
import { BrowserFeatures } from './features.js';
import { BrowserTabs } from './tabs.js';
import { internalSection, internalSnapshot } from './internal-pages.js';
import { BrowserCredentials } from './passwords/service.js';
import { BrowserContextMenu, contextMenuLabels } from './context-menu.js';
import { relayBrowserInput } from './input.js';
import { BrowserStacking } from './stacking.js';
import { browserViewport, setBrowserSurfaceColor } from './geometry.js';
import { scopeKey, trustedFrame, safeFilename, browserRoute, navigationURL } from './policy.js';

let browserDataRoot;
export function setBrowserDataRoot(root) { browserDataRoot = root; }
export class NativeBrowser {
  constructor(window, mainView, origin) {
    Object.assign(this, { window, mainView, origin, tabs: new Map(), selected: new Map(), profiles: new Map(), features: new Map(), owner: '', lease: '', visible: false, geometry: null, disposed: false, revision: 0, layoutSequence: 0, connectSequence: 0 });
    this.registry = new BrowserTabs(this);
    this.credentials = new BrowserCredentials(this);
    this.contextMenu = new BrowserContextMenu(this);
    this.overlayRaised = false;
    this.stacking = new BrowserStacking(this);
    this.downloadViews = new Set();
    this.surfaceColor = window.getBackgroundColor();
    mainView.setBackgroundColor(this.surfaceColor);
    this.invalidate = () => { this.contextMenu.close(); this.credentials.invalidate(); this.connectSequence++; this.lease = ''; this.visible = false; this.overlayRaised = false; this.layout(); this.features.forEach(feature => feature.denyStale()); };
    mainView.webContents.on('did-start-navigation', this.onNavigation = (_e, url, sameDocument, isMainFrame) => {
      if (isMainFrame && (!sameDocument || !browserRoute(new URL(url).pathname))) this.invalidate();
    });
    mainView.webContents.on('render-process-gone', this.invalidate);
    this.register();
  }
  send(channel, data) {
    if (!this.disposed && !this.mainView.webContents.isDestroyed()) this.mainView.webContents.send(channel, data);
  }
  message(message) { this.send('browser:message', { lease: this.lease, message }); }
  byContents(wc) { return [...this.tabs.values()].find(tab => tab.view?.webContents === wc); }
  active() { return this.tabs.get(this.selected.get(this.owner)); }
  profileFeatures(profile) { return this.features.get(profile.key); }
  layout() { this.registry.layout(); }
  resize() { this.layout(); this.send('browser:geometry-request', {}); }
  snapshot() {
    const active = this.active();
    return { lease: this.lease, revision: ++this.revision, activeId: active?.id || '',
      tabs: [...this.tabs.values()].filter(tab => tab.owner === this.owner).map(tab => {
        if (tab.section) return internalSnapshot(tab);
        const wc = tab.view.webContents;
        return { id: tab.id, url: tab.url, title: tab.title, favicon: tab.favicon || '', loading: wc.isLoading() || !!tab.pendingURL, pending: !!tab.pendingURL, error: tab.error,
          canGoBack: wc.navigationHistory.canGoBack(), canGoForward: wc.navigationHistory.canGoForward(), zoom: Math.round(wc.getZoomFactor() * 100), find: tab.find };
      }),
      history: this.profile?.data.history || [],
      downloads: (this.profile?.data.downloads || []).map(({ path: _path, ...row }) => row),
      credentials: this.credentials.snapshot(),
      permissions: this.profile ? this.profileFeatures(this.profile).snapshot() : [] };
  }
  publish() { if (this.lease) this.send('browser:state', this.snapshot()); }
  check(event, lease) {
    if (this.disposed || !trustedFrame(event, this.mainView, this.origin)) throw new Error('untrusted_browser_sender');
    if (lease !== undefined && (!this.lease || lease !== this.lease)) throw new Error('stale_browser_view');
  }
  async connect(event, scope, labels) {
    this.check(event); const scopeId = scopeKey(scope), sequence = ++this.connectSequence;
    this.contextMenu.close();
    this.visible = false; this.overlayRaised = false; this.layout();
    const response = await this.mainView.webContents.session.fetch(`${this.origin}/api/session-status`, { credentials: 'include', cache: 'no-store' });
    if (!response.ok) throw new Error('browser_identity_unavailable');
    const data = await response.json(); this.check(event);
    if (sequence !== this.connectSequence) throw new Error('stale_browser_connect');
    if (!data.success || !data.session?.logged_in || !data.session.username) throw new Error('browser_desktop_login_required');
    const username = String(data.session.username).toLowerCase();
    let profile = this.profiles.get(username);
    if (!profile) {
      profile = new BrowserProfile(browserDataRoot || app.getPath('userData'), username);
      profile.onError = message => this.message(message);
      this.profiles.set(username, profile); this.features.set(profile.key, new BrowserFeatures(this, profile));
    }
    this.credentials.invalidate();
    this.profile = profile; this.owner = `${profile.key}:${scopeId}`; this.lease = randomUUID(); this.layoutSequence = 0;
    this.menuLabels = contextMenuLabels(labels);
    this.features.forEach(feature => feature.denyStale());
    this.registry.restore(); this.layout(); return this.snapshot();
  }
  tab(id) {
    const tab = this.tabs.get(id || this.selected.get(this.owner));
    if (!tab || tab.owner !== this.owner || tab.profile !== this.profile || (!tab.section && tab.view.webContents.isDestroyed())) throw new Error('unknown_browser_tab');
    return tab;
  }
  async command(payload) {
    const { action, id, value } = payload;
    const features = this.profileFeatures(this.profile);
    if (action === 'new') this.registry.create(value || 'about:blank');
    else if (action === 'internal') this.registry.createInternal(value);
    else if (action === 'select') { this.selected.set(this.owner, this.tab(id).id); this.registry.persist(this.owner); }
    else if (action === 'close') this.registry.close(this.tab(id));
    else if (action === 'navigate') {
      const section = internalSection(value);
      if (section) this.registry.createInternal(section);
      else if (!id && !this.active()) this.registry.create(navigationURL(value, true));
      else this.registry.load(this.tab(id), value);
    }
    else if (action === 'duplicate') this.registry.create(this.tab(id).url, { duplicate: true });
    else if (action === 'reorder') {
      if (!Array.isArray(value)) throw new Error('invalid_browser_order');
      const current = [...this.tabs.values()].filter(tab => tab.owner === this.owner);
      if (value.length !== current.length || new Set(value).size !== current.length || value.some(id => !current.some(tab => tab.id === id))) throw new Error('invalid_browser_order');
      const others = [...this.tabs.values()].filter(tab => tab.owner !== this.owner);
      this.tabs = new Map([...others, ...value.map(id => this.tab(id))].map(tab => [tab.id, tab])); this.registry.persist(this.owner);
    } else if (action === 'permission') features.resolve(id, value === true);
    else if (action.startsWith('download-')) {
      if (!['download-cancel', 'download-reveal', 'download-remove'].includes(action)) throw new Error('unsupported_browser_action');
      await features.command(action, id);
    } else if (action === 'history-remove') { this.profile.data.history = this.profile.data.history.filter(row => row.id !== id); this.profile.save(); }
    else if (action === 'clear') {
      const profile = this.profile;
      if (!value || !['history', 'cookies', 'cache'].every(key => typeof value[key] === 'boolean')) throw new Error('invalid_browser_clear');
      if (value.cookies) { await profile.session.clearStorageData({ storages: ['cookies', 'localstorage', 'indexdb', 'serviceworkers', 'cachestorage'] }); features.grants.clear(); }
      if (value.cache) await profile.session.clearCache();
      if (value.history) { profile.data.history = []; profile.save(); }
    } else {
      const tab = this.tab(id);
      if (tab.section) throw new Error('browser_action_requires_webpage');
      const wc = tab.view.webContents;
      if (action === 'back') { if (wc.navigationHistory.canGoBack()) wc.navigationHistory.goBack(); }
      else if (action === 'forward') { if (wc.navigationHistory.canGoForward()) wc.navigationHistory.goForward(); }
      else if (action === 'reload') { tab.error = ''; wc.reload(); }
      else if (action === 'stop') { wc.stop(); tab.pendingURL = ''; tab.url = wc.getURL() || 'about:blank'; }
      else if (action === 'zoom') { if (!Number.isFinite(value)) throw new Error('invalid_browser_zoom'); wc.setZoomFactor(Math.max(50, Math.min(200, value)) / 100); }
      else if (action === 'find') {
        if (typeof value?.text !== 'string' || value.text.length > 1000) throw new Error('invalid_browser_find');
        // Electron 35: findNext=true begins a new search; false continues an existing search.
        if (value.text) wc.findInPage(value.text, { forward: value.forward !== false, findNext: value.next !== true });
        else { wc.stopFindInPage('clearSelection'); tab.find = null; }
      } else if (action === 'device') {
        if (value === null) wc.disableDeviceEmulation();
        else {
          if (!Number.isFinite(value?.width) || !Number.isFinite(value?.height)) throw new Error('invalid_browser_device');
          const width = Math.max(280, Math.min(1920, value.width)), height = Math.max(320, Math.min(1800, value.height));
          wc.enableDeviceEmulation({ screenPosition: 'mobile', screenSize: { width, height }, viewSize: { width, height }, deviceScaleFactor: 0, scale: 1, viewPosition: { x: 0, y: 0 } });
        }
      } else if (action === 'print') {
        await new Promise((resolve, reject) => wc.print({}, (success, reason) => success ? resolve() : reject(new Error(reason || 'print_cancelled'))));
      } else if (action === 'capture') {
        const image = await wc.capturePage();
        const result = await dialog.showSaveDialog(this.window, { defaultPath: path.join(app.getPath('downloads'), `${safeFilename(tab.title || 'webpage')}.png`), filters: [{ name: 'PNG', extensions: ['png'] }] });
        if (!result.canceled && result.filePath) await fs.promises.writeFile(result.filePath, image.toPNG());
      } else if (['copy', 'paste', 'selectAll'].includes(action)) wc[action]();
      else throw new Error('unsupported_browser_action');
    }
    this.layout(); return this.snapshot();
  }
  register() {
    ipcMain.handle('browser:connect', async (event, scope, labels) => this.connect(event, scope, labels));
    ipcMain.on('browser:theme', this.themeHandler = (event, color) => {
      try { this.check(event); setBrowserSurfaceColor(this, color); this.layout(); } catch { /* Only the trusted main renderer owns the theme. */ }
    });
    ipcMain.on('browser:input', this.inputHandler = (event, payload) => {
      try { this.check(event, payload?.lease); relayBrowserInput(this, payload); } catch { /* Retired input cannot reach another owner. */ }
    });
    ipcMain.handle('browser:command', async (event, payload) => {
      this.check(event, payload?.lease); const lease = this.lease;
      const result = await this.command(payload);
      this.check(event, lease); return result;
    });
    ipcMain.on('browser:layout', this.layoutHandler = (event, payload) => {
      try {
        this.check(event, payload?.lease);
        if (!Number.isSafeInteger(payload.sequence) || payload.sequence <= this.layoutSequence) return;
        this.layoutSequence = payload.sequence;
        const viewport = browserViewport(payload.viewport), main = this.mainView.getBounds(), zoom = this.mainView.webContents.getZoomFactor();
        if (viewport && payload.visible && (Math.abs(viewport.width - main.width / zoom) > 2 || Math.abs(viewport.height - main.height / zoom) > 2)) {
          this.send('browser:geometry-request', {});
          return;
        }
        this.geometry = payload.rect; this.visible = payload.visible === true;
        this.geometryViewport = viewport;
        setBrowserSurfaceColor(this, payload.surfaceColor);
        this.overlayRaised = payload.elevated === true;
        this.overlayCapturesInput = payload.capturesInput !== false;
        this.focusFind = payload.focusFind === true;
        this.layout();
      } catch { /* Late layouts from a previous renderer are retired. */ }
    });
  }
  dispose() {
    if (this.disposed) return;
    for (const owner of this.selected.keys()) this.registry.persist(owner);
    this.contextMenu.close();
    this.disposed = true; this.connectSequence++; this.lease = '';
    ipcMain.removeListener('browser:input', this.inputHandler);
    ipcMain.removeListener('browser:theme', this.themeHandler);
    ipcMain.removeHandler('browser:connect'); ipcMain.removeHandler('browser:command'); ipcMain.removeListener('browser:layout', this.layoutHandler);
    this.mainView.webContents.removeListener('did-start-navigation', this.onNavigation);
    this.mainView.webContents.removeListener('render-process-gone', this.invalidate);
    this.features.forEach(feature => feature.dispose());
    this.credentials.dispose();
    for (const tab of this.tabs.values()) { if (tab.view && !tab.view.webContents.isDestroyed()) tab.view.webContents.close({ waitForBeforeUnload: false }); }
    for (const view of this.downloadViews) { if (!view.webContents.isDestroyed()) view.webContents.close({ waitForBeforeUnload: false }); }
    this.downloadViews.clear();
    this.mainView.setBackgroundColor(this.surfaceColor);
    this.tabs.clear(); this.profiles.forEach(profile => { void profile.close().catch(() => {}); });
  }
}
