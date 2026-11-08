import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { app, session } from 'electron';
import { profileKey } from './policy.js';

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
    this.data = { version: 1, history: [], downloads: [], owners: {} };
    try {
      const saved = JSON.parse(fs.readFileSync(this.file, 'utf8'));
      if (saved.version === 1) {
        this.data.history = Array.isArray(saved.history) ? saved.history : [];
        this.data.downloads = Array.isArray(saved.downloads) ? saved.downloads.slice(0, 100).map(row => ({ ...row, state: row.state === 'progressing' ? 'interrupted' : row.state })) : [];
        this.data.owners = saved.owners && typeof saved.owners === 'object' && !Array.isArray(saved.owners) ? saved.owners : {};
      }
    } catch (error) {
      if (error.code !== 'ENOENT') throw new Error(`browser_profile_read_failed: ${error.message}`);
    }
  }
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
