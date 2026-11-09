// Isolated experiment; no Astrion app/backend imports.
const { WebContentsView, session } = require('electron');
const { randomUUID } = require('node:crypto');
const secure = { sandbox: true, contextIsolation: true, nodeIntegration: false, webviewTag: false };
const ownerKey = owner => `${owner.workspace}/${owner.conversation}/${owner.actor}`;
function webUrl(value) {
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw Error('unsupported_navigation');
  return url.href;
}
class TabRegistry {
  constructor(window, origin, changed, attachSession) {
    Object.assign(this, { window, origin, changed, attachSession });
    this.tabs = new Map(); this.sessions = new Map(); this.selected = new Map();
    this.owner = { workspace: 'lab-workspace', conversation: 'A', actor: 'main' };
    this.generation = 1; this.rect = null; this.collapsed = false; this.obscured = false;
  }
  sessionFor(owner) {
    const key = ownerKey(owner);
    if (!this.sessions.has(key)) {
      const ses = session.fromPartition(`persist:experiment-${owner.workspace}-${owner.conversation}-${owner.actor}`);
      this.sessions.set(key, ses); this.attachSession(ses, key);
    }
    return this.sessions.get(key);
  }
  list() { return [...this.tabs.values()].filter(tab => tab.owner === ownerKey(this.owner)); }
  active() { return this.tabs.get(this.selected.get(ownerKey(this.owner))); }
  create(url = `${this.origin}/fixture`, options = {}) {
    const owner = { ...this.owner };
    const view = new WebContentsView({
      ...(options.nativeOptions?.webContents ? { webContents: options.nativeOptions.webContents } : {}),
      webPreferences: { ...options.nativeOptions?.webPreferences,
        ...secure, preload: undefined, session: this.sessionFor(owner) } });
    const wc = view.webContents;
    const tab = { id: randomUUID(), owner: ownerKey(owner), ownerFields: owner, view,
      title: '新标签', url: url || 'about:blank', loading: false, error: null };
    this.tabs.set(tab.id, tab); this.window.contentView.addChildView(view);
    this.selected.set(tab.owner, tab.id);
    const update = () => {
      if (wc.isDestroyed()) return;
      tab.title = wc.getTitle() || '新标签'; tab.url = wc.getURL() || tab.url;
      tab.loading = wc.isLoading(); this.changed();
    };
    for (const event of ['page-title-updated', 'did-navigate', 'did-navigate-in-page', 'did-start-loading', 'did-stop-loading']) wc.on(event, update);
    wc.on('did-fail-load', (_event, code, description, _url, main) => {
      if (main && code !== -3) { tab.error = { code, description }; this.changed(); }
    });
    wc.on('will-navigate', (event, destination) => {
      try { webUrl(destination); } catch { event.preventDefault(); }
    });
    wc.setWindowOpenHandler(({ url: destination }) => {
      try { webUrl(destination); } catch { return { action: 'deny' }; }
      if (tab.owner !== ownerKey(this.owner)) return { action: 'deny' };
      return { action: 'allow', overrideBrowserWindowOptions: { webPreferences: { ...secure, preload: undefined, session: wc.session } },
        createWindow: nativeOptions => {
        const popup = this.create(null, { nativeOptions });
        this.changed('弹窗已加入当前对话的浏览器标签');
        return popup.view.webContents;
      } };
    });
    wc.on('destroyed', () => {
      if (!this.destroying && !this.window.isDestroyed()) this.window.contentView.removeChildView(view);
      this.tabs.delete(tab.id);
      if (this.selected.get(tab.owner) === tab.id) {
        const next = [...this.tabs.values()].filter(item => item.owner === tab.owner).at(-1);
        if (next) this.selected.set(tab.owner, next.id); else this.selected.delete(tab.owner);
      }
      if (!this.destroying && !this.window.isDestroyed()) { this.layout(); this.changed(); }
    });
    this.layout(); this.changed('新建浏览器标签');
    if (url) wc.loadURL(webUrl(url)).catch(error => { tab.error = String(error); this.changed(); });
    return tab;
  }
  tab(id) {
    const tab = this.tabs.get(id);
    if (!tab || tab.owner !== ownerKey(this.owner) || tab.view.webContents.isDestroyed()) throw Error('unknown_or_unowned_tab');
    return tab;
  }
  select(id) { const tab = this.tab(id); this.selected.set(tab.owner, id); this.layout(); this.changed('切换浏览器标签'); }
  close(id) { this.tab(id).view.webContents.close(); }
  switchConversation(conversation) {
    if (!['A', 'B'].includes(conversation)) throw Error('unknown_conversation');
    this.owner = { ...this.owner, conversation }; this.generation++;
    if (!this.active()) this.create();
    this.layout(); this.changed(`切换到对话 ${conversation}`);
  }
  setRect(rect) {
    const window = this.window.getContentBounds();
    if (!rect || !['x', 'y', 'width', 'height'].every(key => Number.isFinite(rect[key]))) throw Error('invalid_geometry');
    const next = Object.fromEntries(Object.entries(rect).map(([key, value]) => [key, Math.round(value)]));
    if (next.x < 0 || next.y < 0 || next.width < 1 || next.height < 1 ||
      next.x + next.width > window.width + 1 || next.y + next.height > window.height + 1) throw Error('geometry_outside_window');
    this.rect = next; this.layout();
  }
  layout() {
    const active = this.active();
    for (const tab of this.tabs.values()) {
      const visible = tab === active && !!this.rect && !this.collapsed && !this.obscured;
      tab.view.setVisible(visible);
      if (visible) tab.view.setBounds(this.rect);
    }
  }
  state() {
    return { owner: this.owner, generation: this.generation, active: this.active()?.id || null,
      collapsed: this.collapsed, obscured: this.obscured, rect: this.rect,
      tabs: this.list().map(tab => ({ id: tab.id, title: tab.title, url: tab.url, loading: tab.loading,
        error: tab.error, webContentsId: tab.view.webContents.id,
        canGoBack: tab.view.webContents.navigationHistory.canGoBack(),
        canGoForward: tab.view.webContents.navigationHistory.canGoForward() })) };
  }
  destroy() {
    this.destroying = true;
    for (const tab of [...this.tabs.values()]) {
      this.window.contentView.removeChildView(tab.view);
      if (!tab.view.webContents.isDestroyed()) tab.view.webContents.close();
    }
  }
}
module.exports = { TabRegistry, ownerKey, webUrl, secure };
