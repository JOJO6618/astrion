// Native residency: metadata survives suspension, Chromium views do not.
import { ownerScopeId, popoutScopeId, profileKey } from './policy.js';

export class BrowserRetention {
  constructor(manager) {
    this.manager = manager;
    this.timer = setInterval(() => this.requestPoll(), 2000);
    this.timer.unref?.();
    this.pending = false;
    this.queued = false;
    this.visits = new Map();
    this.closed = new Set();
    this.clock = 0;
    this.generation = 0;
  }
  visit(owner) { if (owner) this.visits.set(owner, ++this.clock); }
  dispose() { clearInterval(this.timer); }
  navigationChanged(previous, state) {
    const keys = new Set(state.tabs.map(tab => tab.key));
    let changed = false;
    for (const tab of previous?.tabs || []) {
      const scope = popoutScopeId(tab);
      if (!scope || keys.has(tab.key)) continue;
      this.closed.add(scope);
      changed = true;
    }
    for (const tab of state.tabs) {
      if (this.closed.delete(popoutScopeId(tab))) changed = true;
    }
    if (changed) this.residencyChanged();
  }
  residencyChanged() {
    this.generation++;
    this.requestPoll();
  }
  requestPoll() {
    if (this.queued) return;
    this.queued = true;
    if (!this.pending) queueMicrotask(() => { if (this.queued) void this.poll(); });
  }
  async poll() {
    if (this.pending) return;
    this.queued = false;
    const manager = this.manager, session = manager.anySession();
    if (manager.disposed || !manager.profiles.size || !session) return;
    const requested = new Map();
    for (const profile of manager.profiles.values()) {
      for (const owner of Object.keys(profile.data.owners)) requested.set(owner, null);
    }
    for (const tab of manager.tabs.values()) {
      if (tab.view && !tab.view.webContents.isDestroyed()) requested.set(tab.owner, this.visits.get(tab.owner) || 0);
    }
    if (!requested.size) return;
    const owners = [...requested].map(([owner, visit]) => {
      const key = ownerScopeId(owner);
      return { scope: JSON.parse(key), visit, closed: this.closed.has(key) };
    });
    const generation = this.generation;
    const sequence = manager.connectSequence;
    this.pending = true;
    try {
      const state = JSON.stringify({ owners }).replace(/[^\x20-\x7e]/g, char => `\\u${char.charCodeAt(0).toString(16).padStart(4, '0')}`);
      const response = await session.fetch(`${manager.origin}/api/browser-retention`, {
        headers: { 'X-Astrion-Browser-State': state },
        credentials: 'include', cache: 'no-store', signal: AbortSignal.timeout(5000)
      });
      if (!response.ok) return;
      const result = await response.json();
      if (manager.disposed || generation !== this.generation || sequence !== manager.connectSequence || !result.success) return;
      this.apply(result, profileKey(result.username), requested);
    } catch { /* A failed status query never turns running work into idle work. */ }
    finally {
      this.pending = false;
      if (this.queued && !manager.disposed) queueMicrotask(() => { if (this.queued) void this.poll(); });
    }
  }
  apply(result, profile, requested) {
    const manager = this.manager;
    let changed = false;
    for (const scope of result.deleted) {
      const owner = `${profile}:${scope}`;
      if (!requested.has(owner)) continue;
      manager.ai.deleteOwner(owner);
      for (const tab of manager.tabs.values()) if (tab.owner === owner) this.remove(tab);
      this.closePopout(owner);
      for (const value of manager.profiles.values()) {
        if (value.key === profile && Object.hasOwn(value.data.owners, owner)) {
          delete value.data.owners[owner];
          value.save();
        }
      }
      manager.selected.delete(owner);
      this.visits.delete(owner);
      this.closed.delete(scope);
      changed = true;
    }
    for (const scope of result.release) {
      const owner = `${profile}:${scope}`;
      if (requested.get(owner) !== (this.visits.get(owner) || 0)) continue;
      // A new browser call can start after the backend took its main-task snapshot.
      if (manager.ai.isActive(owner)) continue;
      if (this.suspendOwner(owner)) changed = true;
      if (this.closed.has(scope)) this.closePopout(owner);
    }
    if (changed) { manager.layout(); manager.publish(); }
  }
  closePopout(owner) {
    const popout = this.manager.popouts.get(owner);
    if (popout && !popout.window.isDestroyed()) popout.window.close();
  }
  releaseOwner(owner) {
    this.manager.ai.cancelOwner(owner);
    if (!this.suspendOwner(owner)) return;
    this.residencyChanged();
    this.manager.layout(); this.manager.publish();
  }
  suspendOwner(owner) {
    let changed = false;
    for (const tab of this.manager.tabs.values()) {
      if (tab.owner === owner && this.suspend(tab)) changed = true;
    }
    return changed;
  }
  suspend(tab) {
    if (!tab.view || tab.view.webContents.isDestroyed()) return false;
    const manager = this.manager, view = tab.view;
    tab.zoom = view.webContents.getZoomFactor();
    tab.view = null; tab.suspended = true; tab.pendingURL = ''; tab.find = null; tab.ai = undefined;
    manager.credentials.remove(tab); manager.ai.release(tab);
    manager.detach(tab, view);
    view.webContents.close({ waitForBeforeUnload: false });
    manager.profileFeatures(tab.profile).denyStale();
    return true;
  }
  resume(tab) {
    if (!tab?.suspended) return tab;
    const fresh = this.manager.registry.create(tab.url, { id: tab.id, owner: tab.owner, profile: tab.profile, select: false });
    Object.assign(fresh, { title: tab.title, favicon: tab.favicon, restored: true, zoom: tab.zoom || 1 });
    fresh.view.webContents.setZoomFactor(fresh.zoom);
    return fresh;
  }
  remove(tab) {
    const manager = this.manager, view = tab.view;
    manager.tabs.delete(tab.id);
    manager.credentials.remove(tab); manager.ai.release(tab);
    if (view && !view.webContents.isDestroyed()) {
      manager.detach(tab);
      tab.view = null;
      view.webContents.close({ waitForBeforeUnload: false });
    }
  }
}
