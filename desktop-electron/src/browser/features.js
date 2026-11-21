import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { app, shell } from 'electron';
import { safeFilename } from './policy.js';

const SPEED_STALL_MS = 3000;

export class BrowserFeatures {
  constructor(manager, profile) {
    this.manager = manager; this.profile = profile;
    this.pending = new Map(); this.items = new Map(); this.grants = new Set();
    this.downloadTimer = null;
    const ses = profile.session;
    ses.setPermissionCheckHandler((wc, permission, origin) => {
      const tab = manager.byContents(wc);
      return !!tab && tab.profile === profile && this.grants.has(`${origin}:${permission}`);
    });
    ses.setPermissionRequestHandler((wc, permission, callback, details) => {
      const tab = manager.byContents(wc);
      if (!tab || tab.profile !== profile || !manager.hostFor(tab.owner) || !['geolocation', 'notifications', 'media', 'clipboard-read'].includes(permission)) { callback(false); return; }
      let origin;
      try { origin = new URL(details.requestingUrl || wc.getURL()).origin; } catch { callback(false); return; }
      const id = randomUUID();
      const timer = setTimeout(() => this.resolve(id, false), 60000);
      this.pending.set(id, { id, owner: tab.owner, tabId: tab.id, permission, origin, callback, timer });
      manager.publish();
    });
    this.downloadHandler = (_event, item, wc) => this.download(item, wc);
    ses.on('will-download', this.downloadHandler);
  }
  download(item, wc) {
    const tab = this.manager.byContents(wc);
    if (!tab || tab.profile !== this.profile) { item.cancel(); return; }
    const ai = [tab.aiDownload, tab.openingDownload].find(call => call && Date.now() < call.until);
    const directory = ai ? ai.dir : path.join(app.getPath('downloads'), 'Astrion');
    const id = randomUUID(), name = safeFilename(item.getFilename());
    let target;
    try {
      fs.mkdirSync(directory, { recursive: true });
      const base = path.parse(name);
      for (let i = 0; i < 10000; i++) {
        target = path.join(directory, i ? `${base.name} (${i})${base.ext}` : name);
        try { const fd = fs.openSync(target, 'wx', 0o600); fs.closeSync(fd); break; }
        catch (error) { if (error.code !== 'EEXIST') throw error; target = null; }
      }
      if (!target) throw new Error('download_name_exhausted');
      item.setSavePath(target);
    } catch (error) { item.cancel(); this.manager.message(error.message, tab.owner); return; }
    const row = { id, name: path.basename(target), path: target, received: item.getReceivedBytes(),
      total: item.getTotalBytes(), state: 'progressing', at: Date.now() };
    if (ai) {
      row.owner = tab.owner;
      row.url = item.getURL() || null;
      ai.items.push(row);
    }
    const entry = { item, row, lastProgressAt: performance.now(), bytesPerSecond: null, estimatedCompletionAt: null };
    this.items.set(id, entry);
    this.profile.data.downloads.unshift(row);
    this.trimDownloads();
    item.on('updated', (_event, status) => this.updateDownload(entry, status));
    item.once('done', (_event, status) => {
      this.updateDownload(entry, status);
      if (status === 'completed') row.completedAt = Date.now();
      this.items.delete(id);
      if (!this.items.size) {
        clearInterval(this.downloadTimer);
        this.downloadTimer = null;
      }
      this.trimDownloads();
      this.profile.save(); this.manager.publish();
    });
    this.downloadTimer ||= setInterval(() => this.sampleDownloads(), 1000);
    this.profile.save(); this.manager.publish();
    this.manager.registry.retireDownload(tab, item);
  }
  trimDownloads() {
    this.profile.data.downloads = this.profile.data.downloads.filter((row, index) => index < 100 || this.items.has(row.id));
  }
  updateDownload(entry, state) {
    const { item, row } = entry;
    const received = item.getReceivedBytes();
    if (received > row.received) entry.lastProgressAt = performance.now();
    row.received = received;
    row.total = item.getTotalBytes();
    row.state = state;
    if (state !== 'progressing') {
      entry.bytesPerSecond = null;
      entry.estimatedCompletionAt = null;
    }
  }
  sampleDownloads() {
    const wallTime = Date.now();
    for (const entry of this.items.values()) {
      const { row, item } = entry;
      if (row.state !== 'progressing') continue;
      const speed = item.getCurrentBytesPerSecond();
      const remaining = row.total - row.received;
      entry.bytesPerSecond = speed > 0 ? speed : null;
      entry.estimatedCompletionAt = speed > 0 && remaining > 0 ? wallTime + Math.ceil(remaining / speed * 1000) : null;
    }
    this.manager.publish();
  }
  downloadInfo(row, includeSource = false) {
    const { id, name, received, total, state, at, completedAt } = row;
    const entry = this.items.get(id);
    const moving = state === 'progressing' && entry && performance.now() - entry.lastProgressAt < SPEED_STALL_MS;
    return { id, name, received, total: total > 0 ? total : null, state, at,
      bytesPerSecond: moving ? entry.bytesPerSecond : null,
      estimatedCompletionAt: moving && total > received ? entry.estimatedCompletionAt : null,
      completedAt: completedAt ?? null, ...(includeSource ? { path: row.path, url: row.url ?? null } : {}) };
  }
  modelDownloads(owner, id) {
    const rows = this.profile.data.downloads.filter(row => row.owner === owner);
    return (id === undefined ? rows.slice(0, 10) : rows.filter(row => row.id === id))
      .map(row => this.downloadInfo(row, true));
  }
  resolve(id, allowed) {
    const request = this.pending.get(id); if (!request) return;
    this.pending.delete(id); clearTimeout(request.timer);
    const tab = this.manager.tabs.get(request.tabId);
    const valid = tab && !!this.manager.hostFor(tab.owner) && tab.profile === this.profile;
    let sameOrigin = false;
    try { sameOrigin = new URL(tab?.view.webContents.getURL()).origin === request.origin; } catch { /* Navigation invalidates permission requests. */ }
    const grant = allowed === true && valid && sameOrigin;
    if (grant) this.grants.add(`${request.origin}:${request.permission}`);
    request.callback(grant); this.manager.publish();
  }
  denyStale() {
    for (const request of this.pending.values()) {
      const tab = this.manager.tabs.get(request.tabId);
      let sameOrigin = false;
      try { sameOrigin = new URL(tab?.view.webContents.getURL()).origin === request.origin; } catch { /* No active origin. */ }
      if (!tab || !this.manager.hostFor(tab.owner) || !sameOrigin) this.resolve(request.id, false);
    }
  }
  snapshot(owner) {
    return [...this.pending.values()].filter(row => row.owner === owner).map(({ id, tabId, permission, origin }) => ({ id, tabId, permission, origin }));
  }
  async command(action, id) {
    const row = this.profile.data.downloads.find(item => item.id === id);
    if (!row) throw new Error('unknown_browser_download');
    if (action === 'download-cancel') this.items.get(id)?.item.cancel();
    if (action === 'download-reveal') shell.showItemInFolder(row.path);
    if (action === 'download-remove') {
      if (this.items.has(id)) throw new Error('download_still_running');
      this.profile.data.downloads = this.profile.data.downloads.filter(item => item.id !== id); this.profile.save();
    }
  }
  dispose() {
    for (const request of [...this.pending.values()]) this.resolve(request.id, false);
    clearInterval(this.downloadTimer);
    this.downloadTimer = null;
    for (const { item } of this.items.values()) item.cancel();
    this.profile.session.removeListener('will-download', this.downloadHandler);
    this.profile.session.setPermissionCheckHandler(null);
    this.profile.session.setPermissionRequestHandler(null);
    this.grants.clear();
  }
}
