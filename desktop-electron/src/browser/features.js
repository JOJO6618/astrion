import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { app, shell } from 'electron';
import { safeFilename } from './policy.js';

export class BrowserFeatures {
  constructor(manager, profile) {
    this.manager = manager; this.profile = profile;
    this.pending = new Map(); this.items = new Map(); this.grants = new Set();
    const ses = profile.session;
    ses.setPermissionCheckHandler((wc, permission, origin) => {
      const tab = manager.byContents(wc);
      return !!tab && tab.profile === profile && this.grants.has(`${origin}:${permission}`);
    });
    ses.setPermissionRequestHandler((wc, permission, callback, details) => {
      const tab = manager.byContents(wc);
      if (!tab || tab.profile !== profile || tab.owner !== manager.owner || !['geolocation', 'notifications', 'media', 'clipboard-read'].includes(permission)) { callback(false); return; }
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
    // 模型操作触发的下载进入工作区目录（设置可改）；用户手动下载仍进 ~/Downloads/Astrion。
    const ai = tab.aiDownload && Date.now() < tab.aiDownload.until ? tab.aiDownload : null;
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
    } catch (error) { item.cancel(); this.manager.message(error.message); return; }
    const row = { id, name: path.basename(target), path: target, received: 0, total: item.getTotalBytes(), state: 'progressing', at: Date.now() };
    if (ai) ai.items.push({ name: row.name, path: target });
    this.items.set(id, item); this.profile.data.downloads.unshift(row);
    this.profile.data.downloads = this.profile.data.downloads.slice(0, 100); this.profile.save(); this.manager.publish();
    let lastUpdate = 0;
    item.on('updated', (_event, status) => {
      row.received = item.getReceivedBytes(); row.total = item.getTotalBytes(); row.state = status;
      if (Date.now() - lastUpdate > 200) { lastUpdate = Date.now(); this.manager.publish(); }
    });
    item.once('done', (_event, status) => {
      row.received = item.getReceivedBytes(); row.state = status; this.items.delete(id);
      this.profile.save(); this.manager.publish();
    });
    this.manager.registry.retireDownload(tab, item);
  }
  resolve(id, allowed) {
    const request = this.pending.get(id); if (!request) return;
    this.pending.delete(id); clearTimeout(request.timer);
    const tab = this.manager.tabs.get(request.tabId);
    const valid = tab && tab.owner === this.manager.owner && tab.profile === this.profile;
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
      if (!tab || tab.owner !== this.manager.owner || !sameOrigin) this.resolve(request.id, false);
    }
  }
  snapshot() {
    return [...this.pending.values()].filter(row => row.owner === this.manager.owner).map(({ id, tabId, permission, origin }) => ({ id, tabId, permission, origin }));
  }
  async command(action, id) {
    const row = this.profile.data.downloads.find(item => item.id === id);
    if (!row) throw new Error('unknown_browser_download');
    if (action === 'download-cancel') this.items.get(id)?.cancel();
    if (action === 'download-reveal') shell.showItemInFolder(row.path);
    if (action === 'download-remove') {
      if (this.items.has(id)) throw new Error('download_still_running');
      this.profile.data.downloads = this.profile.data.downloads.filter(item => item.id !== id); this.profile.save();
    }
  }
  dispose() {
    for (const request of [...this.pending.values()]) this.resolve(request.id, false);
    for (const item of this.items.values()) item.cancel();
    this.profile.session.removeListener('will-download', this.downloadHandler);
    this.profile.session.setPermissionCheckHandler(null);
    this.profile.session.setPermissionRequestHandler(null);
    this.grants.clear();
  }
}
