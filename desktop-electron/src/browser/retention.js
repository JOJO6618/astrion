// Native residency: metadata survives suspension, Chromium views do not.
import { profileKey } from './policy.js';

export class BrowserRetention {
  constructor(manager) {
    this.manager = manager;
    this.timer = setInterval(() => { void this.poll(); }, 2000);
    this.timer.unref?.();
    this.pending = false;
    this.visits = new Map();
  }
  visit(owner) { if (owner) this.visits.set(this.scope(owner), Date.now()); }
  dispose() { clearInterval(this.timer); }
  scope(owner) { return owner.slice(owner.indexOf(':') + 1); }
  async poll() {
    const manager = this.manager;
    if (this.pending || manager.disposed || !manager.profiles.size || manager.mainView.webContents.isDestroyed()) return;
    this.pending = true;
    const sequence = manager.connectSequence;
    try {
      const owners = new Set();
      for (const profile of manager.profiles.values()) for (const owner of Object.keys(profile.data.owners)) owners.add(this.scope(owner));
      const tabs = [...manager.tabs.values()].map(tab => {
        owners.add(this.scope(tab.owner));
        return { id: tab.id, owner: this.scope(tab.owner), selected: manager.selected.get(tab.owner) === tab.id,
          lastActive: tab.lastActive || Date.now(), lastAi: tab.lastAi || 0, busy: !!tab.aiBusy };
      });
      // 已弹出到独立窗口的对话正在被查看，与主窗口当前对话同样算前台。
      const presented = [...manager.popouts.keys()].map(owner => this.scope(owner)).filter(scope => owners.has(scope));
      const response = await manager.mainView.webContents.session.fetch(`${manager.origin}/api/browser-retention`, {
        headers: { 'X-Astrion-Browser-State': JSON.stringify({ owners: [...owners].map(key => JSON.parse(key)), tabs, visits: Object.fromEntries(this.visits), presented: presented.map(key => JSON.parse(key)) }) },
        credentials: 'include', cache: 'no-store', signal: AbortSignal.timeout(5000)
      });
      if (!response.ok) return;
      const result = await response.json();
      if (manager.disposed || sequence !== manager.connectSequence || !result.success) return;
      const key = profileKey(result.username);
      this.apply(result, key, tabs);
    } catch { /* A failed status query never turns running work into idle work. */ }
    finally { this.pending = false; }
  }
  apply(result, profile, requested = []) {
    const manager = this.manager;
    const deleted = new Set(result.deleted.map(scope => `${profile}:${scope}`));
    for (const owner of deleted) {
      // 对话已删除：先关掉它的独立窗口（视图挪回主窗口），再移除标签。
      const popout = manager.popouts.get(owner);
      if (popout && !popout.window.isDestroyed()) popout.window.close();
      manager.ai.deleteOwner(owner);
      for (const tab of [...manager.tabs.values()]) if (tab.owner === owner) this.remove(tab);
      for (const value of manager.profiles.values()) if (value.key === profile) { delete value.data.owners[owner]; value.save(); }
      manager.selected.delete(owner);
    }
    const before = new Map(requested.map(tab => [tab.id, tab]));
    for (const id of result.release) {
      const tab = manager.tabs.get(id), previous = before.get(id);
      if (!tab || tab.profile.key !== profile || tab.section || tab.aiBusy) continue;
      if (previous && ((tab.lastActive || 0) > previous.lastActive || (tab.lastAi || 0) > previous.lastAi)) continue;
      this.suspend(tab);
    }
    manager.layout(); manager.publish();
  }
  suspend(tab) {
    if (!tab.view || tab.view.webContents.isDestroyed()) return;
    const manager = this.manager, view = tab.view;
    tab.zoom = view.webContents.getZoomFactor();
    tab.view = null; tab.suspended = true; tab.pendingURL = ''; tab.find = null; tab.ai = undefined;
    manager.credentials.remove(tab); manager.ai.release(tab);
    manager.detach(tab, view);
    view.webContents.close({ waitForBeforeUnload: false });
    manager.profileFeatures(tab.profile).denyStale();
  }
  resume(tab) {
    if (!tab?.suspended) return tab;
    const manager = this.manager, selected = manager.selected.get(tab.owner);
    const fresh = manager.registry.create(tab.url, { id: tab.id, owner: tab.owner, profile: tab.profile, select: false });
    Object.assign(fresh, { title: tab.title, favicon: tab.favicon, lastActive: Date.now(), lastAi: tab.lastAi,
      restored: true, zoom: tab.zoom || 1 });
    fresh.view.webContents.setZoomFactor(fresh.zoom);
    if (selected) manager.selected.set(tab.owner, selected);
    return fresh;
  }
  remove(tab) {
    const manager = this.manager;
    manager.tabs.delete(tab.id);
    manager.credentials.remove(tab); manager.ai.release(tab);
    if (tab.view && !tab.view.webContents.isDestroyed()) {
      manager.detach(tab);
      tab.view.webContents.close({ waitForBeforeUnload: false });
    }
  }
}
