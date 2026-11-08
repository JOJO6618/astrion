import { randomUUID } from 'node:crypto';
import { chromeProfiles, profileDirectory, readDatabase, tableColumns, existingDatabase, chromeEncryptionKey } from './source.js';
import { deriveChromeKey, decryptChromeValue, chromeTime } from './crypto.js';
import { ImportCookieStore, cookieIdentity, cookieDetails } from './cookie-store.js';
import { passwordOrigin } from '../passwords/vault.js';
const TYPES = ['passwords', 'cookies', 'history'];
const stat = () => ({ read: 0, ready: 0, skipped: 0, failed: 0, conflicts: 0, reasons: {} });
const issue = (stats, reason, failed = true) => { stats[failed ? 'failed' : 'skipped']++; stats.reasons[reason] = (stats.reasons[reason] || 0) + 1; };
const column = (columns, name, fallback = "''") => columns.has(name) ? name : fallback;
const reason = error => ['database_busy', 'keychain_timeout', 'keychain_denied', 'unsupported_encryption', 'unsupported_partition', 'conflicting_cookie_storage'].includes(error.message) ? error.message : 'invalid_record';

export class ChromeImporter {
  constructor(dependencies = {}) { this.dependencies = dependencies; this.jobs = new Map(); }
  sources() { return chromeProfiles(this.dependencies.root); }
  async preview(profile, vault, input, ensure, signal, progress = () => {}) {
    if (!input || !TYPES.every(type => typeof input.selected?.[type] === 'boolean') || !TYPES.some(type => input.selected[type])) throw new Error('请选择至少一项导入内容。');
    const directory = profileDirectory(input.source, this.dependencies.root);
    const stats = Object.fromEntries(TYPES.map(type => [type, stat()]));
    const raw = { passwords: [], cookies: [], history: [] };
    const read = this.dependencies.readDatabase || readDatabase;
    const checked = () => { signal?.throwIfAborted(); ensure(); };
    const readRows = async (type, names, collect) => {
      if (!input.selected[type]) return;
      progress(type); checked();
      const files = existingDatabase(directory, names);
      if (!files.length) issue(stats[type], 'missing_database');
      for (const file of files) {
        try { raw[type].push(...await read(file, collect, signal)); }
        catch (error) { checked(); issue(stats[type], error.message === 'database_busy' ? 'database_busy' : 'database_unavailable'); }
        checked();
      }
      stats[type].read = raw[type].length;
    };
    await readRows('passwords', ['Login Data', 'Login Data For Account'], db => {
      const columns = tableColumns(db, 'logins');
      if (!['origin_url', 'username_value', 'password_value'].every(name => columns.has(name))) throw new Error('schema');
      return db.prepare(`SELECT origin_url AS origin, username_value AS username, hex(password_value) AS encrypted,
        ${column(columns, 'blacklisted_by_user', '0')} AS blocked, ${column(columns, 'federation_url')} AS federation,
        CAST(${column(columns, 'date_password_modified', column(columns, 'date_created', '0'))} AS TEXT) AS modified FROM logins`).all();
    });
    let cookieVersion = 0;
    await readRows('cookies', ['Network/Cookies', 'Cookies'].filter(name => existingDatabase(directory, [name]).length).slice(0, 1), db => {
      const columns = tableColumns(db, 'cookies');
      if (!['host_key', 'name', 'path', 'encrypted_value'].every(name => columns.has(name))) throw new Error('schema');
      const version = db.prepare("SELECT value FROM meta WHERE key='version'").get();
      cookieVersion = Number(version?.value);
      if (!Number.isSafeInteger(cookieVersion) || cookieVersion < 1) throw new Error('schema');
      return db.prepare(`SELECT host_key AS domain, name, path, ${column(columns, 'value')} AS value, hex(encrypted_value) AS encrypted,
        ${column(columns, 'is_secure', '0')} AS secure, ${column(columns, 'is_httponly', '0')} AS httpOnly,
        CAST(${column(columns, 'expires_utc', '0')} AS TEXT) AS expires, ${column(columns, 'has_expires', '1')} AS hasExpires,
        ${column(columns, 'samesite', '-1')} AS sameSite, ${column(columns, 'top_frame_site_key')} AS partitionSite,
        ${column(columns, 'has_cross_site_ancestor', '1')} AS crossSiteAncestor,
        ${column(columns, 'source_scheme', '0')} AS sourceScheme, ${column(columns, 'source_port', '-1')} AS sourcePort,
        ${column(columns, 'priority', '1')} AS priority FROM cookies`).all();
    });
    await readRows('history', ['History'], db => db.prepare('SELECT url, title, CAST(last_visit_time AS TEXT) AS visited FROM urls ORDER BY last_visit_time DESC').all());
    let key, keyError;
    if (raw.passwords.some(row => row.encrypted && !row.blocked && !row.federation) || raw.cookies.some(row => row.encrypted)) {
      progress('keychain'); let secret;
      try { secret = await (this.dependencies.keySecret || chromeEncryptionKey)(signal); checked(); key = deriveChromeKey(secret); }
      catch (error) { checked(); keyError = ['keychain_timeout', 'keychain_denied'].includes(error.message) ? error.message : 'keychain_denied'; }
      finally { secret?.fill(0); }
    }
    const groups = new Map(), cookies = [], history = [], conflicts = [];
    progress('decrypt');
    try {
      for (const row of raw.passwords) {
        if (row.blocked || row.federation || !row.encrypted) { issue(stats.passwords, 'not_password', false); continue; }
        try {
          const origin = passwordOrigin(row.origin), identity = JSON.stringify([origin, row.username]);
          if (keyError) throw new Error(keyError);
          const password = decryptChromeValue(row.encrypted, key);
          if (!password) { issue(stats.passwords, 'not_password', false); continue; }
          const current = vault.find(origin, row.username);
          let group = groups.get(identity);
          if (!group) { group = { id: randomUUID(), origin, username: row.username, existingId: current?.id || '', candidates: [] }; groups.set(identity, group); }
          if (current?.password === password || group.candidates.some(item => item.password === password)) { issue(stats.passwords, 'duplicate', false); continue; }
          group.candidates.push({ id: randomUUID(), origin, username: row.username, password, sourceURL: row.origin, modified: chromeTime(row.modified) });
          stats.passwords.ready++;
        } catch (error) { issue(stats.passwords, reason(error)); }
      }
      for (const row of raw.cookies) {
        try {
          if (row.encrypted && keyError) throw new Error(keyError);
          cookies.push(cookieDetails(row, chromeTime, decryptChromeValue, key, cookieVersion)); stats.cookies.ready++;
        } catch (error) { issue(stats.cookies, error.message === 'expired' ? 'expired' : reason(error), error.message !== 'expired'); }
      }
    } finally { key?.fill(0); }
    for (const row of raw.history) {
      try { passwordOrigin(row.url); const at = chromeTime(row.visited); if (!at) throw new Error('time'); history.push({ id: randomUUID(), url: row.url, title: row.title || row.url, at }); stats.history.ready++; }
      catch { issue(stats.history, 'unsupported_record', false); }
    }
    const passwords = [...groups.values()].filter(group => group.candidates.length);
    for (const group of passwords) {
      if (group.existingId || group.candidates.length > 1) {
        stats.passwords.conflicts++;
        conflicts.push({ id: group.id, origin: group.origin, username: group.username, hasExisting: !!group.existingId,
          candidates: group.candidates.map(({ id, sourceURL, modified }) => ({ id, sourceURL, modified })) });
      }
    }
    checked();
    for (const [id, job] of this.jobs) if (job.profile === profile) this.cancel(id);
    const id = randomUUID(), timer = setTimeout(() => this.cancel(id), 300000); timer.unref();
    this.jobs.set(id, { id, profile, stats, passwords, cookies, history, conflicts, timer, vaultState: input.selected.passwords ? JSON.stringify(vault.list()) : null });
    return { id, stats, conflicts };
  }
  async commit(profile, vault, input, ensure) {
    const job = this.jobs.get(input?.id);
    if (!job || job.profile !== profile) throw new Error('导入预览已过期，请重新读取 Chrome 资料。');
    if (!input.choices || job.conflicts.some(group => !group.candidates.some(row => row.id === input.choices[group.id]) && !(group.hasExisting && input.choices[group.id] === 'keep'))) throw new Error('请先选择如何处理每条重复密码。');
    if (typeof input.replaceCookies !== 'boolean') throw new Error('请选择 Cookie 冲突处理方式。');
    ensure();
    if (job.vaultState !== null && JSON.stringify(vault.list()) !== job.vaultState) throw new Error('密码库已变化，请重新预览导入。');
    this.cancel(job.id);
    const result = Object.fromEntries(TYPES.map(type => [type, { imported: 0, skipped: job.stats[type].skipped, failed: job.stats[type].failed, reasons: { ...job.stats[type].reasons } }]));
    const chosen = job.passwords.flatMap(group => {
      const choice = input.choices[group.id] || group.candidates[0].id;
      return group.candidates.filter(row => row.id === choice);
    });
    result.passwords.skipped += job.stats.passwords.ready - chosen.length;
    if (chosen.length) {
      try { ensure(); vault.importRows(chosen); result.passwords.imported = chosen.length; }
      catch { result.passwords.failed += chosen.length; result.passwords.reasons.vault_unavailable = chosen.length; }
    }
    if (job.cookies.length) {
      const store = this.dependencies.cookieStore ? this.dependencies.cookieStore(profile) : new ImportCookieStore(profile);
      try {
        const existing = new Map((await store.open()).map(row => [cookieIdentity(row), row]));
        for (const { cookie, identity } of job.cookies) {
          ensure();
          const current = existing.get(identity);
          if (current && !input.replaceCookies) { result.cookies.skipped++; continue; }
          try { await store.set(cookie); result.cookies.imported++; existing.set(identity, cookie); }
          catch { issue(result.cookies, 'cookie_rejected'); }
        }
        ensure();
        try { await store.flush(); }
        catch {
          result.cookies.reasons.cookie_flush_failed = result.cookies.imported;
          result.cookies.failed += result.cookies.imported; result.cookies.imported = 0;
        }
      } catch (error) {
        ensure();
        const remaining = job.cookies.length - result.cookies.imported - (result.cookies.skipped - job.stats.cookies.skipped) - (result.cookies.failed - job.stats.cookies.failed);
        result.cookies.failed += Math.max(0, remaining);
        result.cookies.reasons.cookie_store_unavailable = Math.max(0, remaining);
      } finally { store.close(); }
    }
    ensure();
    if (job.history.length) {
      const known = new Set(profile.data.history.map(row => JSON.stringify([row.url, row.at])));
      const fresh = job.history.filter(row => { const id = JSON.stringify([row.url, row.at]); if (known.has(id)) return false; known.add(id); return true; });
      const previous = profile.data.history;
      profile.data.history = [...previous, ...fresh].sort((a, b) => b.at - a.at);
      try { profile.flush(true); result.history.imported = fresh.length; result.history.skipped += job.history.length - fresh.length; }
      catch { profile.data.history = previous; result.history.failed += fresh.length; result.history.reasons.history_write_failed = fresh.length; }
    }
    return { stats: result };
  }
  cancel(id) { const job = this.jobs.get(id); if (job) clearTimeout(job.timer); this.jobs.delete(id); }
  cancelProfile(profile) { for (const [id, job] of this.jobs) if (job.profile === profile) this.cancel(id); }
  dispose() { for (const id of this.jobs.keys()) this.cancel(id); }
}
