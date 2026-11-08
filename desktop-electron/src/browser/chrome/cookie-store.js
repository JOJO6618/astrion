import { WebContentsView } from 'electron';
import { SECURE_WEB_PREFERENCES } from '../policy.js';

export const cookieIdentity = row => JSON.stringify([
  row.domain, row.path || '/', row.name,
  row.partitionKey?.topLevelSite || '', row.partitionKey?.hasCrossSiteAncestor ?? null
]);

// A private target in the destination session supplies the Chromium cookie API.
// Electron 35's public cookies.set omits CHIPS partition keys; never flatten them.
export class ImportCookieStore {
  constructor(profile) {
    this.profile = profile;
    this.view = new WebContentsView({ webPreferences: { ...SECURE_WEB_PREFERENCES, session: profile.session } });
    this.contents = this.view.webContents;
  }
  async open() {
    try {
      await this.contents.loadURL('about:blank');
      this.contents.debugger.attach('1.3');
      const { cookies } = await this.contents.debugger.sendCommand('Network.getAllCookies');
      return cookies;
    } catch { this.close(); throw new Error('cookie_store_unavailable'); }
  }
  async set(cookie) {
    const response = await this.contents.debugger.sendCommand('Network.setCookie', cookie);
    if (!response.success) throw new Error('cookie_rejected');
  }
  async flush() { await this.profile.session.cookies.flushStore(); }
  close() {
    if (!this.contents.isDestroyed()) {
      if (this.contents.debugger.isAttached()) this.contents.debugger.detach();
      this.contents.close({ waitForBeforeUnload: false });
    }
  }
}

export function cookieDetails(row, chromeTime, decrypt, key, version) {
  if (row.encrypted && row.value) throw new Error('conflicting_cookie_storage');
  const value = row.encrypted ? decrypt(row.encrypted, key, { version, domain: row.domain }) : row.value;
  const domain = row.domain.replace(/^\./, '');
  const url = new URL(`${row.secure ? 'https' : 'http'}://${domain}${row.path || '/'}`);
  if (!row.name || typeof value !== 'string' || url.hostname !== domain || url.username || url.password) throw new Error('unsupported_record');
  const expiration = chromeTime(row.expires) / 1000;
  if (row.hasExpires && expiration && expiration <= Date.now() / 1000) throw new Error('expired');
  const cookie = { url: url.href, name: row.name, value, path: row.path || '/', secure: Number(row.secure) !== 0, httpOnly: Number(row.httpOnly) !== 0 };
  const sourceScheme = { 0: 'Unset', 1: 'NonSecure', 2: 'Secure' }[row.sourceScheme];
  if (sourceScheme) cookie.sourceScheme = sourceScheme;
  if (Number.isInteger(row.sourcePort) && row.sourcePort >= -1 && row.sourcePort <= 65535) cookie.sourcePort = row.sourcePort;
  const priority = { 0: 'Low', 1: 'Medium', 2: 'High' }[row.priority];
  if (priority) cookie.priority = priority;
  if (row.domain.startsWith('.')) cookie.domain = row.domain;
  if (row.hasExpires && expiration) cookie.expires = expiration;
  const sameSite = { 0: 'None', 1: 'Lax', 2: 'Strict' }[row.sameSite];
  if (sameSite) cookie.sameSite = sameSite;
  if (row.partitionSite) {
    const site = new URL(row.partitionSite);
    if (!['http:', 'https:'].includes(site.protocol) || site.origin !== row.partitionSite.replace(/\/$/, '') || !row.secure) throw new Error('unsupported_partition');
    cookie.partitionKey = { topLevelSite: site.origin, hasCrossSiteAncestor: !!row.crossSiteAncestor };
  }
  return { cookie, identity: cookieIdentity({ ...cookie, domain: row.domain }) };
}
