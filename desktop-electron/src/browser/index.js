import path from 'node:path';
import fs from 'node:fs';
import { randomUUID } from 'node:crypto';
import { app, ipcMain, dialog } from 'electron';
import { BrowserProfile } from './profile.js';
import { BrowserFeatures } from './features.js';
import { BrowserTabs } from './tabs.js';
import { BrowserRetention } from './retention.js';
import { internalSection, internalSnapshot } from './internal-pages.js';
import { BrowserCredentials } from './passwords/service.js';
import { BrowserContextMenu, contextMenuLabels } from './context-menu.js';
import { relayBrowserInput } from './input.js';
import { browserBounds, browserViewport, setBrowserSurfaceColor } from './geometry.js';
import { scopeKey, scopeAlias, safeFilename, manualNavigationURL } from './policy.js';
import { BrowserHost } from './host.js';
import { followAiTarget } from './follow.js';
import { createPopoutWindow, focusPopoutWindow } from './popout-window.js';
import { BrowserAI } from './ai/controller.js';
import { setActiveBrowser } from './ai/endpoint.js';

// 主窗口当前对话已弹出时，主窗口仍可执行的资料级命令（不涉及该对话的标签）。
const PROFILE_ACTIONS = new Set(['search-engine', 'follow-ai', 'shortcuts', 'history-remove', 'clear', 'download-cancel', 'download-reveal', 'download-remove']);
const WINDOW_ACTIONS = new Set(['popout', 'dock', 'show-popout']);

let browserDataRoot;
export function setBrowserDataRoot(root) { browserDataRoot = root; }
export class NativeBrowser {
  constructor(window, mainView, origin) {
    Object.assign(this, { window, mainView, origin, tabs: new Map(), selected: new Map(), profiles: new Map(), features: new Map(), popouts: new Map(), disposed: false, revision: 0, connectSequence: 0 });
    this.main = new BrowserHost(this, { kind: 'main', window, view: mainView });
    this.registry = new BrowserTabs(this);
    this.credentials = new BrowserCredentials(this);
    this.contextMenu = new BrowserContextMenu(this);
    this.downloadViews = new Set();
    this.ai = new BrowserAI(this);
    this.retention = new BrowserRetention(this);
    this.surfaceColor = window.getBackgroundColor();
    // 纯文本类文档（text-documents.js）的文字色，由前端主题上报；空值按背景明暗兜底。
    this.textColor = '';
    mainView.setBackgroundColor(this.surfaceColor);
    this.watchHost(this.main);
    this.register();
    setActiveBrowser(this);
  }
  /** 宿主 renderer 全量导航、离开允许路由或崩溃时，退役它的连接。 */
  watchHost(host) {
    const wc = host.view.webContents;
    host.invalidate = () => this.invalidate(host);
    host.onNavigation = (_e, url, sameDocument, isMainFrame) => {
      if (isMainFrame && (!sameDocument || !host.route(new URL(url).pathname))) host.invalidate();
    };
    wc.on('did-start-navigation', host.onNavigation);
    wc.on('render-process-gone', host.invalidate);
  }
  unwatchHost(host) {
    const wc = host.view.webContents;
    if (wc.isDestroyed()) return;
    wc.removeListener('did-start-navigation', host.onNavigation);
    wc.removeListener('render-process-gone', host.invalidate);
  }
  invalidate(host) {
    this.contextMenu.close(); this.credentials.invalidate(host);
    host.connectSequence++; this.connectSequence++;
    host.lease = ''; host.visible = false; host.overlayRaised = false;
    this.layout(); this.features.forEach(feature => feature.denyStale());
  }
  hosts() { return [this.main, ...this.popouts.values()]; }
  /** 正在显示该对话标签的宿主：已弹出则为它的独立窗口，否则是当前停在该对话的主窗口。 */
  hostFor(owner) {
    if (!owner) return null;
    return this.popouts.get(owner) || (this.main.owner === owner ? this.main : null);
  }
  /** 该对话网页视图应挂载的窗口；未弹出的对话（含后台对话）都挂在主窗口。 */
  windowFor(owner) { return this.popouts.get(owner)?.window || this.window; }
  boundsFor(owner) { return browserBounds(this.hostFor(owner) || this.main); }
  attach(tab) {
    if (!tab.view) return;
    const window = this.windowFor(tab.owner);
    if (tab.window === window) return;
    this.detach(tab);
    window.contentView.addChildView(tab.view);
    // 挂载后再藏：未挂载时的 setVisible 对独立视图不生效，不藏就会在 layout 前露出一帧。
    tab.view.setVisible(false);
    tab.window = window;
  }
  detach(tab, view = tab.view) {
    const window = tab.window;
    tab.window = null;
    if (view && window && !window.isDestroyed()) window.contentView.removeChildView(view);
  }
  ensureProfile(username) {
    const key = String(username).toLowerCase();
    let profile = this.profiles.get(key);
    if (!profile) {
      profile = new BrowserProfile(browserDataRoot || app.getPath('userData'), key);
      profile.onError = message => this.message(message);
      this.profiles.set(key, profile); this.features.set(profile.key, new BrowserFeatures(this, profile));
    }
    return profile;
  }
  /** /new 阶段以稳定标签 key 为 owner；对话创建后把标签、恢复记录与独立窗口迁移到对话 owner。 */
  adoptOwner(profile, from, to) {
    if (!from || from === to) return;
    if ([...this.tabs.values()].some(tab => tab.owner === to) || profile.data.owners[to] || this.popouts.has(to)) return;
    const moving = [...this.tabs.values()].filter(tab => tab.owner === from);
    const popout = this.popouts.get(from);
    if (!moving.length && !profile.data.owners[from] && !popout) return;
    for (const tab of moving) tab.owner = to;
    if (this.selected.has(from)) { this.selected.set(to, this.selected.get(from)); this.selected.delete(from); }
    if (profile.data.owners[from]) { profile.data.owners[to] = profile.data.owners[from]; delete profile.data.owners[from]; profile.save(); }
    if (popout) { this.popouts.delete(from); popout.owner = to; this.popouts.set(to, popout); }
    this.ai.migrateOwner(from, to);
  }
  /** 只发给主窗口（AI 活动通知等与对话面板展开相关的消息）。 */
  send(channel, data) { this.main.send(channel, data); }
  /** 指定对话时只通知显示它的宿主；资料级错误通知所有宿主。 */
  message(message, owner) {
    const hosts = owner ? [this.hostFor(owner)].filter(Boolean) : this.hosts();
    for (const host of hosts) host.message(message);
  }
  byContents(wc) { return [...this.tabs.values()].find(tab => tab.view?.webContents === wc); }
  profileFeatures(profile) { return this.features.get(profile.key); }
  layout() { this.registry.layout(); }
  /** 跟随智能体：AI 每操作一个标签就把用户视图交给它。跟随关闭时整条链路都不写 selected。 */
  follow(owner, tab) {
    if (this.disposed || tab?.owner !== owner) return;
    const target = followAiTarget(tab, this.selected.get(owner));
    if (!target) return;
    this.selected.set(owner, target);
    this.registry.persist(owner);
    this.layout(); this.publish();
  }
  resize(host = this.main) { this.layout(); host.send('browser:geometry-request', {}); }
  snapshot(host) {
    const active = host.active(), profile = host.profile, detached = host.detached;
    return { lease: host.lease, revision: ++this.revision, activeId: active?.id || '', window: host.kind, detached,
      tabs: detached ? [] : [...this.tabs.values()].filter(tab => tab.owner === host.owner).map(tab => {
        if (tab.section) return internalSnapshot(tab);
        const wc = tab.view?.webContents;
        return { id: tab.id, url: tab.url, title: tab.title, favicon: tab.favicon || '', loading: (wc?.isLoading() || false) || !!tab.pendingURL, pending: !!tab.pendingURL, error: tab.error,
          canGoBack: wc?.navigationHistory.canGoBack() || false, canGoForward: wc?.navigationHistory.canGoForward() || false, zoom: Math.round((wc?.getZoomFactor() || tab.zoom || 1) * 100), find: tab.find };
      }),
      history: profile?.data.history || [],
      settings: { searchEngine: profile?.data.settings.searchEngine || '', followAi: profile?.data.settings.followAi !== false },
      shortcuts: profile?.data.shortcuts || { pinned: [], hidden: [] },
      downloads: (profile?.data.downloads || []).map(({ path: _path, ...row }) => row),
      credentials: this.credentials.snapshot(host),
      permissions: profile && !detached ? this.profileFeatures(profile).snapshot(host.owner) : [] };
  }
  publish() { for (const host of this.hosts()) if (host.lease) host.send('browser:state', this.snapshot(host)); }
  /** 返回发送方对应的宿主；只接受各宿主自身 webContents 顶层可信来源及其路由。 */
  check(event, lease) {
    const host = this.disposed ? null : this.hosts().find(row => row.trusted(event));
    if (!host) throw new Error('untrusted_browser_sender');
    if (lease !== undefined && (!host.lease || lease !== host.lease)) throw new Error('stale_browser_view');
    return host;
  }
  async connect(event, scope, labels) {
    const host = this.check(event);
    // 独立窗口的对话由主进程在弹出时固定，忽略 renderer 自报的 scope。
    const scopeId = host.popout ? '' : scopeKey(scope), sequence = ++host.connectSequence;
    this.connectSequence++;
    this.contextMenu.close();
    host.visible = false; host.overlayRaised = false; this.layout();
    const response = await host.view.webContents.session.fetch(`${this.origin}/api/session-status`, { credentials: 'include', cache: 'no-store' });
    if (!response.ok) throw new Error('browser_identity_unavailable');
    const data = await response.json();
    if (this.check(event) !== host) throw new Error('untrusted_browser_sender');
    if (sequence !== host.connectSequence) throw new Error('stale_browser_connect');
    if (!data.success || !data.session?.logged_in || !data.session.username) throw new Error('browser_desktop_login_required');
    const profile = this.ensureProfile(String(data.session.username));
    if (host.popout) {
      if (profile !== host.profile) throw new Error('browser_identity_changed');
    } else {
      this.credentials.invalidate(host);
      const alias = scopeAlias(scope);
      if (alias) this.adoptOwner(profile, `${profile.key}:${alias}`, `${profile.key}:${scopeId}`);
      this.retention.visit(host.owner);
      host.profile = profile; host.owner = `${profile.key}:${scopeId}`;
    }
    host.lease = randomUUID(); host.layoutSequence = 0;
    this.retention.visit(host.owner);
    host.menuLabels = contextMenuLabels(labels);
    this.features.forEach(feature => feature.denyStale());
    if (!host.detached) { this.registry.restore(host); this.retention.resume(host.active()); }
    this.layout(); void this.retention.poll(); return this.snapshot(host);
  }
  tab(host, id) {
    const tab = this.tabs.get(id || this.selected.get(host.owner));
    if (!tab || host.detached || tab.owner !== host.owner || tab.profile !== host.profile || (!tab.section && !tab.suspended && tab.view.webContents.isDestroyed())) throw new Error('unknown_browser_tab');
    return tab;
  }
  async command(host, payload) {
    const { action, id, value } = payload;
    if (WINDOW_ACTIONS.has(action)) { this.windowCommand(host, action); return this.snapshot(host); }
    if (host.detached && !PROFILE_ACTIONS.has(action)) throw new Error('browser_detached');
    const owner = host.owner, profile = host.profile;
    const previous = host.active();
    if (previous) previous.lastActive = Date.now();
    if (!['close', 'reorder', 'duplicate'].includes(action)) {
      const target = this.tabs.get(id || this.selected.get(owner));
      if (target?.owner === owner) this.retention.resume(target);
    }
    const features = this.profileFeatures(profile);
    if (action === 'new') this.registry.create(value || 'about:blank', { owner, profile });
    else if (action === 'internal') this.registry.createInternal(value, { owner, profile });
    else if (action === 'select') { this.selected.set(owner, this.tab(host, id).id); this.registry.persist(owner); }
    else if (action === 'close') this.registry.close(this.tab(host, id));
    else if (action === 'navigate') {
      const section = internalSection(value);
      if (section) this.registry.createInternal(section, { owner, profile });
      else if (!id && !host.active()) this.registry.create(manualNavigationURL(value, profile.searchEngine()), { owner, profile });
      else this.registry.load(this.tab(host, id), value);
    }
    else if (action === 'duplicate') this.registry.create(this.tab(host, id).url, { duplicate: true, owner, profile });
    else if (action === 'reorder') {
      if (!Array.isArray(value)) throw new Error('invalid_browser_order');
      const current = [...this.tabs.values()].filter(tab => tab.owner === owner);
      if (value.length !== current.length || new Set(value).size !== current.length || value.some(id => !current.some(tab => tab.id === id))) throw new Error('invalid_browser_order');
      const others = [...this.tabs.values()].filter(tab => tab.owner !== owner);
      this.tabs = new Map([...others, ...value.map(id => this.tab(host, id))].map(tab => [tab.id, tab])); this.registry.persist(owner);
    } else if (action === 'permission') features.resolve(id, value === true);
    else if (action.startsWith('download-')) {
      if (!['download-cancel', 'download-reveal', 'download-remove'].includes(action)) throw new Error('unsupported_browser_action');
      await features.command(action, id);
    } else if (action === 'search-engine') profile.setSearchEngine(value);
    else if (action === 'follow-ai') profile.setFollowAi(value === true);
    else if (action === 'shortcuts') profile.setShortcuts(value);
    else if (action === 'history-remove') { profile.data.history = profile.data.history.filter(row => row.id !== id); profile.save(); }
    else if (action === 'clear') {
      if (!value || !['history', 'cookies', 'cache'].every(key => typeof value[key] === 'boolean')) throw new Error('invalid_browser_clear');
      if (value.cookies) { await profile.session.clearStorageData({ storages: ['cookies', 'localstorage', 'indexdb', 'serviceworkers', 'cachestorage'] }); features.grants.clear(); }
      if (value.cache) await profile.session.clearCache();
      if (value.history) { profile.data.history = []; profile.save(); }
    } else {
      const tab = this.tab(host, id);
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
        const result = await dialog.showSaveDialog(host.window, { defaultPath: path.join(app.getPath('downloads'), `${safeFilename(tab.title || 'webpage')}.png`), filters: [{ name: 'PNG', extensions: ['png'] }] });
        if (!result.canceled && result.filePath) await fs.promises.writeFile(result.filePath, image.toPNG());
      } else if (['copy', 'paste', 'selectAll'].includes(action)) wc[action]();
      else throw new Error('unsupported_browser_action');
    }
    this.layout(); return this.snapshot(host);
  }
  /** popout：主窗口当前对话弹出到独立窗口（已弹出则前置）；dock：收回；show-popout：前置独立窗口。 */
  windowCommand(host, action) {
    const owner = host.owner;
    if (!host.lease || !owner) throw new Error('browser_not_connected');
    const popout = this.popouts.get(owner);
    if (action === 'dock') { if (popout && !popout.window.isDestroyed()) popout.window.close(); return; }
    if (host.popout) throw new Error('unsupported_browser_action');
    if (popout) { focusPopoutWindow(popout); return; }
    if (action === 'show-popout') throw new Error('browser_not_detached');
    this.popout(host);
  }
  popout(host) {
    const owner = host.owner;
    const popout = createPopoutWindow(this, { owner, profile: host.profile });
    this.popouts.set(owner, popout);
    this.watchHost(popout);
    this.contextMenu.close();
    // 主窗口的连接退役到占位态：凭据提示等随标签转给独立窗口，主窗口不再显示该对话的网页。
    host.visible = false; host.overlayRaised = false;
    for (const tab of this.tabs.values()) if (tab.owner === owner) this.attach(tab);
    this.retention.visit(owner);
    this.features.forEach(feature => feature.denyStale());
    this.layout(); this.publish();
  }
  /** 独立窗口关闭（含用户点红绿灯、收回命令、删除对话）：网页视图挪回主窗口，标签保留。 */
  undock(popout) {
    if (this.popouts.get(popout.owner) !== popout) return;
    this.popouts.delete(popout.owner);
    this.unwatchHost(popout);
    popout.lease = ''; popout.visible = false;
    this.contextMenu.close();
    if (this.disposed) return;
    for (const tab of this.tabs.values()) if (tab.owner === popout.owner) this.attach(tab);
    this.retention.visit(popout.owner);
    this.features.forEach(feature => feature.denyStale());
    this.layout(); this.publish();
  }
  register() {
    ipcMain.handle('browser:connect', async (event, scope, labels) => this.connect(event, scope, labels));
    ipcMain.on('browser:theme', this.themeHandler = (event, theme) => {
      try { const host = this.check(event); setBrowserSurfaceColor(this, theme, host.window); this.layout(); } catch { /* Only trusted host renderers own the theme. */ }
    });
    ipcMain.on('browser:input', this.inputHandler = (event, payload) => {
      try { relayBrowserInput(this.check(event, payload?.lease), payload); } catch { /* Retired input cannot reach another owner. */ }
    });
    ipcMain.handle('browser:command', async (event, payload) => {
      const host = this.check(event, payload?.lease), lease = host.lease;
      // 收回会关闭发起命令的独立窗口本身，结果无人接收，不再复核 lease。
      if (WINDOW_ACTIONS.has(payload?.action)) return this.command(host, payload);
      const result = await this.command(host, payload);
      if (this.check(event, lease) !== host) throw new Error('stale_browser_view');
      return result;
    });
    ipcMain.on('browser:layout', this.layoutHandler = (event, payload) => {
      try {
        const host = this.check(event, payload?.lease);
        if (!Number.isSafeInteger(payload.sequence) || payload.sequence <= host.layoutSequence) return;
        host.layoutSequence = payload.sequence;
        const viewport = browserViewport(payload.viewport), bounds = host.view.getBounds(), zoom = host.view.webContents.getZoomFactor();
        if (viewport && payload.visible && (Math.abs(viewport.width - bounds.width / zoom) > 2 || Math.abs(viewport.height - bounds.height / zoom) > 2)) {
          host.send('browser:geometry-request', {});
          return;
        }
        host.geometry = payload.rect; host.visible = payload.visible === true;
        host.geometryViewport = viewport;
        setBrowserSurfaceColor(this, { surfaceColor: payload.surfaceColor, textColor: payload.textColor }, host.window);
        host.overlayRaised = payload.elevated === true;
        host.overlayCapturesInput = payload.capturesInput !== false;
        host.focusFind = payload.focusFind === true;
        this.layout();
      } catch { /* Late layouts from a previous renderer are retired. */ }
    });
  }
  dispose() {
    if (this.disposed) return;
    this.retention.dispose(); this.ai.dispose(); setActiveBrowser(null, this);
    for (const owner of this.selected.keys()) this.registry.persist(owner);
    this.contextMenu.close();
    this.disposed = true; this.connectSequence++; this.main.lease = '';
    ipcMain.removeListener('browser:input', this.inputHandler);
    ipcMain.removeListener('browser:theme', this.themeHandler);
    ipcMain.removeHandler('browser:connect'); ipcMain.removeHandler('browser:command'); ipcMain.removeListener('browser:layout', this.layoutHandler);
    this.unwatchHost(this.main);
    this.features.forEach(feature => feature.dispose());
    this.credentials.dispose();
    for (const tab of this.tabs.values()) { if (tab.view && !tab.view.webContents.isDestroyed()) tab.view.webContents.close({ waitForBeforeUnload: false }); }
    for (const view of this.downloadViews) { if (!view.webContents.isDestroyed()) view.webContents.close({ waitForBeforeUnload: false }); }
    this.downloadViews.clear();
    // 关闭 BaseWindow 不会销毁子视图，网页已在上面逐个关闭；独立窗口随主窗口一起销毁。
    for (const popout of [...this.popouts.values()]) {
      this.popouts.delete(popout.owner); this.unwatchHost(popout); popout.lease = '';
      if (!popout.window.isDestroyed()) popout.window.destroy();
    }
    if (!this.mainView.webContents.isDestroyed()) this.mainView.setBackgroundColor(this.surfaceColor);
    this.tabs.clear(); this.profiles.forEach(profile => { void profile.close().catch(() => {}); });
  }
}
