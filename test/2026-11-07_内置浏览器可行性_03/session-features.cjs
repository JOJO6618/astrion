const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { ownerKey } = require('./tab-registry.cjs');
class SessionFeatures {
  constructor(output, registry, changed) {
    Object.assign(this, { output, registry, changed });
    this.downloads = new Map(); this.pending = new Map();
    fs.mkdirSync(path.join(output, 'downloads'), { recursive: true });
  }
  attach(ses, owner) {
    ses.setPermissionCheckHandler(() => false);
    ses.setPermissionRequestHandler((wc, permission, callback, details) => {
      const registry = this.registry();
      const tab = [...registry.tabs.values()].find(item => item.view.webContents === wc);
      if (!tab || tab.owner !== owner || owner !== ownerKey(registry.owner) || permission !== 'geolocation') return callback(false);
      const id = randomUUID();
      this.pending.set(id, { id, owner, tabId: tab.id, permission,
        origin: new URL(details.requestingUrl || wc.getURL()).origin, callback });
      this.changed('网页请求位置权限');
    });
    ses.on('will-download', (_event, item, wc) => {
      const tab = [...this.registry().tabs.values()].find(tab => tab.view.webContents === wc);
      if (!tab || tab.owner !== owner) { item.cancel(); return; }
      const id = randomUUID(); const filename = path.basename(item.getFilename());
      const target = path.join(this.output, 'downloads', `${id}-${filename}`);
      item.setSavePath(target);
      const entry = { id, owner, tabId: tab.id, filename, target, received: 0, total: item.getTotalBytes(),
        state: 'progressing', item, lastNotify: 0 };
      this.downloads.set(id, entry); this.changed('开始下载');
      item.on('updated', (_event, state) => {
        entry.received = item.getReceivedBytes(); entry.state = state;
        if (Date.now() - entry.lastNotify > 150) { entry.lastNotify = Date.now(); this.changed(); }
      });
      item.once('done', (_event, state) => {
        entry.received = item.getReceivedBytes(); entry.state = state; this.changed(`下载${state === 'completed' ? '完成' : '已结束'}`);
      });
    });
  }
  resolvePermission(id, allowed) {
    const request = this.pending.get(id);
    if (!request || request.owner !== ownerKey(this.registry().owner)) throw Error('unknown_permission');
    this.pending.delete(id); request.callback(allowed === true); this.changed('已处理网页权限请求');
  }
  denyInvalid() {
    const registry = this.registry();
    for (const [id, request] of this.pending) {
      if (request.owner !== ownerKey(registry.owner) || !registry.tabs.has(request.tabId)) {
        this.pending.delete(id); request.callback(false);
      }
    }
  }
  cancelDownload(id) {
    const entry = this.downloads.get(id);
    if (!entry || entry.owner !== ownerKey(this.registry().owner)) throw Error('unknown_download');
    entry.item.cancel();
  }
  state() {
    const owner = ownerKey(this.registry().owner);
    return { permissions: [...this.pending.values()].filter(item => item.owner === owner).map(({ id, tabId, permission, origin }) => ({ id, tabId, permission, origin })),
      downloads: [...this.downloads.values()].filter(item => item.owner === owner).map(({ id, tabId, filename, received, total, state }) => ({ id, tabId, filename, received, total, state })) };
  }
  destroy() {
    for (const request of this.pending.values()) request.callback(false);
    this.pending.clear();
    for (const entry of this.downloads.values()) if (entry.state === 'progressing') entry.item.cancel();
  }
}
module.exports = { SessionFeatures };
