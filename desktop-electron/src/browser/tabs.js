import { WebContentsView } from 'electron';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { SECURE_WEB_PREFERENCES, navigationURL, manualNavigationURL, isLocalFile } from './policy.js';
import { browserBounds } from './geometry.js';
import { applyTextDocumentTheme } from './text-documents.js';
import { internalSection, internalURL } from './internal-pages.js';
const passwordPreload = fileURLToPath(new URL('./passwords/page-preload.cjs', import.meta.url));

export class BrowserTabs {
  constructor(manager) { this.manager = manager; }
  createInternal(section, options = {}) {
    const manager = this.manager, url = internalURL(section);
    const owner = options.owner || manager.owner, profile = options.profile || manager.profile;
    let tab = !options.duplicate && [...manager.tabs.values()].find(row => row.owner === owner && row.section === section);
    if (!tab) {
      if ([...manager.tabs.values()].filter(tab => tab.owner === owner).length >= 48) throw new Error('browser_tab_limit');
      tab = { id: randomUUID(), owner, profile, section, url, title: '', lastActive: Date.now(), previousId: manager.selected.get(owner) };
      manager.tabs.set(tab.id, tab);
    }
    manager.selected.set(owner, tab.id);
    this.persist(owner); manager.layout(); manager.publish();
    return tab;
  }
  close(tab) {
    if (tab.section || tab.suspended) this.remove(tab);
    else tab.view.webContents.close({ waitForBeforeUnload: false });
  }
  remove(tab) {
    const manager = this.manager;
    if (manager.tabs.get(tab.id) !== tab) return;
    const siblings = [...manager.tabs.values()].filter(row => row.owner === tab.owner);
    const index = siblings.indexOf(tab);
    manager.tabs.delete(tab.id); manager.credentials?.remove(tab);
    if (manager.selected.get(tab.owner) === tab.id) {
      const previous = manager.tabs.get(tab.previousId);
      const next = previous?.owner === tab.owner ? previous : siblings[index - 1] || siblings[index + 1];
      if (next) manager.selected.set(tab.owner, next.id); else manager.selected.delete(tab.owner);
    }
    if (!manager.disposed && !manager.window.isDestroyed()) {
      if (tab.view) manager.window.contentView.removeChildView(tab.view);
      this.persist(tab.owner, tab.profile); manager.profileFeatures(tab.profile).denyStale(); manager.layout(); manager.publish();
    }
  }
  create(url = 'about:blank', options = {}) {
    const manager = this.manager;
    const section = internalSection(url);
    if (section) return this.createInternal(section, options);
    const owner = options.owner || manager.owner, profile = options.profile || manager.profile;
    if ([...manager.tabs.values()].filter(tab => tab.owner === owner).length >= 48 && !options.id) throw new Error('browser_tab_limit');
    const view = new WebContentsView({
      ...(options.nativeOptions?.webContents ? { webContents: options.nativeOptions.webContents } : {}),
      webPreferences: { ...SECURE_WEB_PREFERENCES, session: profile.session, preload: passwordPreload }
    });
    // 先给出真实尺寸再隐藏：隐藏后再 setBounds 不会传到渲染进程，后台标签（AI 操作）的视口会是 0×0。
    view.setBounds(browserBounds(manager) || { x: 0, y: 0, width: 1280, height: 800 });
    view.setVisible(false);
    view.setBackgroundColor(manager.surfaceColor);
    const tab = { id: options.id || randomUUID(), owner, profile, view, title: '', favicon: '', url: url || options.targetURL || 'about:blank', error: '', find: null,
      lastActive: Date.now(), lastAi: 0, suspended: false, textDocument: false,
      pendingURL: options.targetURL || '', committedDocument: false, retiredDownload: false,
      closeOnDownload: !!options.nativeOptions || (!!url && url !== 'about:blank'),
      previousId: manager.selected.get(owner) };
    const wc = view.webContents;
    manager.tabs.set(tab.id, tab); manager.window.contentView.addChildView(view);
    view.setBounds(browserBounds(manager) || { x: 0, y: 0, width: 1280, height: 800 });
    if (options.select !== false) manager.selected.set(owner, tab.id);
    const update = () => {
      if (wc.isDestroyed() || manager.disposed || tab.retiredDownload) return;
      tab.url = tab.pendingURL || wc.getURL() || tab.url; tab.title = wc.getTitle() || '';
      manager.profileFeatures(tab.profile).denyStale();
      this.persist(owner); manager.layout(); manager.publish();
    };
    for (const event of ['page-title-updated', 'did-navigate-in-page', 'did-start-loading', 'did-stop-loading']) wc.on(event, update);
    wc.on('page-favicon-updated', (_event, icons) => {
      if (tab.retiredDownload || wc.isDestroyed() || tab.pendingURL) return;
      tab.favicon = icons.find(icon => /^https?:\/\//i.test(icon) || /^data:image\//i.test(icon)) || '';
      const history = profile.data.history.find(row => row.url === wc.getURL());
      if (history) { history.favicon = tab.favicon; profile.save(); }
      update();
    });
    const navigating = (_event, destination, inPlace, isMain) => {
      if (!isMain || inPlace || tab.retiredDownload) return;
      manager.credentials?.navigating(tab, destination);
      tab.pendingURL = destination; tab.url = destination; tab.error = ''; tab.textDocument = false;
      update();
    };
    wc.on('did-start-navigation', navigating);
    wc.on('did-redirect-navigation', navigating);
    wc.on('did-navigate', (_event, destination) => {
      if (tab.retiredDownload) return;
      tab.error = ''; tab.pendingURL = ''; tab.url = destination; tab.favicon = '';
      if (destination !== 'about:blank') tab.committedDocument = true;
      profile.record(destination, wc.getTitle()); update();
    });
    wc.on('page-title-updated', (_event, title) => {
      const row = profile.data.history.find(row => row.url === wc.getURL());
      if (row) { row.title = title; profile.save(); }
    });
    wc.on('did-fail-load', (_event, code, text, failedURL, isMain) => {
      if (!isMain || code === -3 || tab.retiredDownload || (tab.pendingURL && tab.pendingURL !== failedURL)) return;
      tab.pendingURL = ''; tab.error = `${text} (${code})`;
      manager.layout(); manager.publish();
    });
    wc.on('render-process-gone', (_event, details) => { tab.pendingURL = ''; tab.error = details.reason; manager.layout(); manager.publish(); });
    // 纯文本类响应（text/plain、json、源码等）背景透明且默认黑字，深色主题下需要补样式。
    wc.on('dom-ready', () => { void applyTextDocumentTheme(manager, tab); });
    wc.on('found-in-page', (_event, result) => { tab.find = { matches: result.matches, active: result.activeMatchOrdinal }; manager.publish(); });
    wc.on('will-navigate', (event, destination) => { try {
      if (isLocalFile(wc.getURL())) manualNavigationURL(destination); else navigationURL(destination);
    } catch { event.preventDefault(); manager.message('unsupported_browser_url'); } });
    wc.on('will-redirect', (event, destination) => { try { navigationURL(destination); } catch { event.preventDefault(); } });
    wc.on('will-attach-webview', event => event.preventDefault());
    wc.on('before-input-event', (event, input) => {
      if (input.type !== 'keyDown' || !(input.meta || input.control) || tab.owner !== manager.owner) return;
      const key = input.key.toLowerCase();
      if (['l', 'f', 't', 'w', '+', '=', '-', '0'].includes(key)) {
        event.preventDefault();
        manager.send('browser:shortcut', { lease: manager.lease, key });
      }
    });
    wc.on('context-menu', (_event, params) => {
      manager.contextMenu.show(tab, params);
    });
    wc.setWindowOpenHandler(({ url: destination }) => {
      try { if (isLocalFile(wc.getURL())) manualNavigationURL(destination); else navigationURL(destination); } catch { return { action: 'deny' }; }
      if ((tab.owner !== manager.owner && !manager.ai?.drivers.has(tab.id)) || [...manager.tabs.values()].filter(row => row.owner === owner).length >= 48) return { action: 'deny' };
      // Adopted popups are browser tabs: keep their lifetime independent of the opener.
      return { action: 'allow', outlivesOpener: true, overrideBrowserWindowOptions: { webPreferences: { ...SECURE_WEB_PREFERENCES, session: profile.session, preload: passwordPreload } },
        createWindow: nativeOptions => this.create(null, { owner, profile, nativeOptions, targetURL: destination }).view.webContents };
    });
    wc.once('destroyed', () => { if (tab.view !== view) return; manager.ai?.release(tab); this.remove(tab); });
    this.persist(owner); manager.layout(); manager.publish();
    if (url && url !== 'about:blank') this.load(tab, url);
    return tab;
  }
  load(tab, value) {
    const url = manualNavigationURL(value, tab.profile.searchEngine());
    if (tab.section) return this.create(url, { id: tab.id, owner: tab.owner, profile: tab.profile });
    if (tab.suspended) { tab.url = url; return this.manager.retention.resume(tab); }
    tab.error = ''; tab.url = url; tab.pendingURL = url;
    const sequence = tab.loadSequence = (tab.loadSequence || 0) + 1;
    tab.view.webContents.loadURL(url).catch(error => {
      if (!tab.view || tab.view.webContents.isDestroyed() || tab.retiredDownload || tab.loadSequence !== sequence) return;
      if (error.code === 'ERR_ABORTED' && (tab.view.webContents.isLoading() || tab.pendingURL !== url)) return;
      tab.pendingURL = '';
      if (error.code === 'ERR_ABORTED') tab.url = tab.view.webContents.getURL() || 'about:blank';
      else tab.error = error.message;
      this.manager.layout(); this.manager.publish();
    });
    this.persist(tab.owner); this.manager.layout(); this.manager.publish();
  }
  retireDownload(tab, item) {
    if (tab.retiredDownload) return;
    const manager = this.manager;
    if (!tab.closeOnDownload || tab.committedDocument) {
      if (tab.pendingURL && item.getURLChain().includes(tab.pendingURL)) {
        tab.pendingURL = ''; tab.url = tab.view.webContents.getURL() || 'about:blank';
        this.persist(tab.owner); manager.layout(); manager.publish();
      }
      return;
    }
    tab.retiredDownload = true;
    manager.tabs.delete(tab.id);
    tab.view.setVisible(false);
    manager.window.contentView.removeChildView(tab.view);
    manager.downloadViews.add(tab.view);
    if (manager.selected.get(tab.owner) === tab.id) {
      const previous = manager.tabs.get(tab.previousId);
      const next = previous?.owner === tab.owner ? previous : [...manager.tabs.values()].filter(row => row.owner === tab.owner).at(-1);
      if (next) manager.selected.set(tab.owner, next.id);
      else {
        manager.selected.delete(tab.owner);
        this.create('about:blank', { owner: tab.owner, profile: tab.profile });
      }
    }
    // Retain the originating contents until completion so detaching the placeholder
    // cannot cancel a Chromium download that still depends on its frame.
    const release = () => {
      manager.downloadViews.delete(tab.view);
      if (!tab.view.webContents.isDestroyed()) tab.view.webContents.close({ waitForBeforeUnload: false });
    };
    item.once('done', release);
    this.persist(tab.owner); manager.layout(); manager.publish();
  }
  persist(owner, fallbackProfile = this.manager.profile) {
    const manager = this.manager;
    if (manager.disposed) return;
    const tabs = [...manager.tabs.values()].filter(tab => tab.owner === owner);
    if (!tabs.length) { if (fallbackProfile) { delete fallbackProfile.data.owners[owner]; fallbackProfile.save(); } return; }
    const profile = tabs[0].profile;
    profile.data.owners[owner] = { urls: tabs.map(tab => tab.url), active: tabs.findIndex(tab => tab.id === manager.selected.get(owner)), at: Date.now() };
    const owners = Object.keys(profile.data.owners).sort((a, b) => profile.data.owners[b].at - profile.data.owners[a].at);
    owners.slice(100).forEach(key => { delete profile.data.owners[key]; }); profile.save();
  }
  restore() {
    const manager = this.manager;
    if ([...manager.tabs.values()].some(tab => tab.owner === manager.owner)) return;
    const saved = manager.profile.data.owners[manager.owner];
    const urls = Array.isArray(saved?.urls) ? saved.urls.slice(0, Math.min(12, 48 - manager.tabs.size)) : [];
    if (!urls.length) { this.create(); return; }
    const created = [];
    for (const url of urls) { try { created.push(this.create(internalSection(url) ? url : manualNavigationURL(url))); } catch { /* Invalid saved URLs never become navigable. */ } }
    if (!created.length) this.create();
    else { manager.selected.set(manager.owner, created[Math.max(0, Math.min(saved.active || 0, created.length - 1))].id); this.persist(manager.owner); }
  }
  layout() {
    const manager = this.manager;
    if (manager.disposed || manager.window.isDestroyed()) return;
    const bounds = browserBounds(manager);
    const active = manager.active();
    for (const tab of manager.tabs.values()) {
      if (!tab.view || tab.view.webContents.isDestroyed()) continue;
      const visible = tab === active && manager.visible && !!bounds && tab.url !== 'about:blank' && !tab.pendingURL && !tab.error;
      if (!visible && tab.view.webContents.isFocused()) manager.mainView.webContents.focus();
      tab.view.setVisible(visible);
      if (visible) {
        tab.view.setBounds(bounds);
        // AI 在后台操作时可能设置了视口尺寸模拟；显示后必须立即交还真实尺寸。
        void manager.ai?.drivers.get(tab.id)?.clearViewport();
      }
    }
    manager.stacking.apply(active);
  }
}
