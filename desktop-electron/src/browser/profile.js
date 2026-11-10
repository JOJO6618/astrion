import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { app, session } from 'electron';
import { profileKey, navigationURL, SEARCH_ENGINES } from './policy.js';

/** 9 site tiles + the "add shortcut" cell fill the 2×5 new-tab grid. */
const MAX_PINNED = 9, MAX_HIDDEN = 200;
/** New-tab shortcuts: pinned tiles (user-added or pinned) and site keys hidden from "most visited". */
export function normalizeShortcuts(value, strict = false) {
  const fail = () => { if (strict) throw new Error('invalid_browser_shortcuts'); };
  const pinned = [], hidden = [];
  if (!value || typeof value !== 'object') { fail(); return { pinned, hidden }; }
  if (Array.isArray(value.pinned)) {
    if (strict && value.pinned.length > MAX_PINNED) fail();
    for (const row of value.pinned.slice(0, MAX_PINNED)) {
      try {
        if (typeof row?.url !== 'string' || !/^https?:\/\//i.test(row.url.trim())) throw new Error('invalid');
        const url = navigationURL(row.url);
        if (pinned.some(item => item.url === url)) continue;
        const title = typeof row.title === 'string' ? row.title.replace(/[\x00-\x1f\x7f]/g, ' ').trim().slice(0, 60) : '';
        pinned.push({ url, title: title || new URL(url).hostname.replace(/^www\./, '') });
      } catch { fail(); }
    }
  } else if (value.pinned !== undefined) fail();
  if (Array.isArray(value.hidden)) {
    if (strict && value.hidden.length > MAX_HIDDEN) fail();
    for (const key of value.hidden.slice(0, MAX_HIDDEN)) {
      if (typeof key === 'string' && /^[a-z0-9.:[\]-]{1,260}$/i.test(key)) { if (!hidden.includes(key.toLowerCase())) hidden.push(key.toLowerCase()); }
      else fail();
    }
  } else if (value.hidden !== undefined) fail();
  return { pinned, hidden };
}

export class BrowserProfile {
  constructor(root, username) {
    this.key = profileKey(username);
    this.directory = path.join(root, 'browser', this.key);
    fs.mkdirSync(this.directory, { recursive: true, mode: 0o700 });
    this.file = path.join(this.directory, 'metadata.json');
    // Electron 35 grants its macOS Network Service access only beneath sessionData.
    // Keep Chromium's encrypted cookies/cache inside that native sandbox root.
    const chromium = path.join(app.getPath('sessionData'), 'browser', this.key, 'chromium');
    fs.mkdirSync(chromium, { recursive: true, mode: 0o700 });
    this.session = session.fromPath(chromium, { cache: true });
    this.data = { version: 1, history: [], downloads: [], owners: {}, settings: { searchEngine: '' }, shortcuts: { pinned: [], hidden: [] } };
    try {
      const saved = JSON.parse(fs.readFileSync(this.file, 'utf8'));
      if (saved.version === 1) {
        this.data.history = Array.isArray(saved.history) ? saved.history : [];
        this.data.downloads = Array.isArray(saved.downloads) ? saved.downloads.slice(0, 100).map(row => ({ ...row, state: row.state === 'progressing' ? 'interrupted' : row.state })) : [];
        this.data.owners = saved.owners && typeof saved.owners === 'object' && !Array.isArray(saved.owners) ? saved.owners : {};
        const engine = saved.settings?.searchEngine;
        this.data.settings = { searchEngine: Object.hasOwn(SEARCH_ENGINES, engine) ? engine : '' };
        this.data.shortcuts = normalizeShortcuts(saved.shortcuts);
      }
    } catch (error) {
      if (error.code !== 'ENOENT') throw new Error(`browser_profile_read_failed: ${error.message}`);
    }
  }
  /** Unset (empty) falls back to Google; the panel stores a language-based default on first connect. */
  searchEngine() { return this.data.settings.searchEngine || 'google'; }
  setSearchEngine(engine) {
    if (typeof engine !== 'string' || !Object.hasOwn(SEARCH_ENGINES, engine)) throw new Error('invalid_browser_search_engine');
    this.data.settings.searchEngine = engine; this.save();
  }
  setShortcuts(value) { this.data.shortcuts = normalizeShortcuts(value, true); this.save(); }
  record(url, title) {
    if (!/^https?:/.test(url)) return;
    const last = this.data.history[0];
    if (last?.url === url && Date.now() - last.at < 30000) { last.title = title || url; this.save(); return; }
    this.data.history.unshift({ id: randomUUID(), url, title: title || url, at: Date.now() });
    this.save();
  }
  save() {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.flush(), 250);
  }
  flush(strict = false) {
    clearTimeout(this.timer);
    const temporary = `${this.file}.${process.pid}.tmp`;
    try { fs.writeFileSync(temporary, JSON.stringify(this.data), { mode: 0o600 }); fs.renameSync(temporary, this.file); }
    catch (error) {
      this.onError?.('browser_profile_write_failed');
      if (strict) throw new Error('browser_profile_write_failed');
    }
  }
  async close() {
    this.flush();
    await this.session.cookies.flushStore();
    this.session.flushStorageData();
  }
}
