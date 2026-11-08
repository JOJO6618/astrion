import { ipcMain, systemPreferences, clipboard } from 'electron';
import { randomUUID } from 'node:crypto';
import { PasswordVault, passwordOrigin } from './vault.js';
import { ChromeImporter } from '../chrome/importer.js';
const meta = row => ({ id: row.id, origin: row.origin, username: row.username });

export class BrowserCredentials {
  constructor(manager, dependencies = {}) {
    this.manager = manager; this.dependencies = dependencies;
    this.vaults = new Map(); this.forms = new Map(); this.prompts = new Map(); this.fills = new Map(); this.autofilled = new Set();
    this.documents = new Map(); this.vaultErrors = new Set();
    this.importer = new ChromeImporter(dependencies.chrome);
    ipcMain.handle('browser:credentials', (event, payload) => this.command(event, payload));
    ipcMain.on('browser:password-form', this.formHandler = (event, payload) => {
      try { this.onForm(event, payload); } catch { /* Obsolete/untrusted page signals never expose errors or secrets. */ }
    });
  }
  vault(profile) {
    if (!profile) throw new Error('浏览器资料尚未连接。');
    if (!this.vaults.has(profile.key)) this.vaults.set(profile.key, new PasswordVault(profile.directory, this.dependencies.encryption));
    return this.vaults.get(profile.key);
  }
  reset(tab) {
    this.forms.delete(tab.id); this.fills.delete(tab.id);
    for (const value of this.autofilled) if (value.startsWith(`${tab.id}:`)) this.autofilled.delete(value);
  }
  dismissPrompt(id) { const prompt = this.prompts.get(id); if (prompt) clearTimeout(prompt.timer); this.prompts.delete(id); }
  remove(tab) { this.reset(tab); this.dismissPrompt(tab.id); this.documents.delete(tab.id); }
  navigating(tab) {
    this.reset(tab);
    for (const document of this.documents.get(tab.id) || []) if (!document.retiredAt) document.retiredAt = Date.now();
    // A captured submission belongs to the source document, even after a login redirect.
    // Confirmation still shows that exact origin and expires independently after two minutes.
  }
  invalidate() {
    this.operation?.controller.abort(); this.importer.dispose();
    for (const id of this.prompts.keys()) this.dismissPrompt(id);
    this.forms.clear(); this.fills.clear(); this.autofilled.clear(); this.documents.clear(); this.vaultErrors.clear();
  }
  snapshot() {
    const tab = this.manager.active();
    if (!tab) return { prompt: null, fill: null };
    const prompt = this.prompts.get(tab.id);
    return {
      prompt: prompt && prompt.owner === this.manager.owner ? { id: prompt.id, tabId: tab.id, origin: prompt.origin, username: prompt.username, update: !!prompt.existingId } : null,
      fill: this.fills.get(tab.id) || null
    };
  }
  onForm(event, input) {
    const manager = this.manager, tab = manager.byContents(event.sender);
    if (!manager.lease || !tab || manager.active() !== tab || tab.owner !== manager.owner || event.senderFrame !== tab.view.webContents.mainFrame) return;
    if (!input || !['form', 'submitted'].includes(input.kind) || !/^[a-f\d]{32}$/.test(input.documentToken) || !/^[a-f\d]{32}$/.test(input.formToken)) return;
    if (typeof input.url !== 'string' || input.url.length > 8192 || typeof input.action !== 'string' || input.action.length > 8192) return;
    const url = input.url, origin = passwordOrigin(url), actionOrigin = passwordOrigin(input.action);
    const live = !tab.pendingURL && !tab.error && event.senderFrame.url === url;
    let documents = (this.documents.get(tab.id) || []).filter(document => !document.retiredAt || Date.now() - document.retiredAt < 10000);
    const known = documents.find(document => document.token === input.documentToken && document.url === url);
    if (input.kind === 'form' && !live) return;
    if (input.kind === 'submitted' && !live && !known) return;
    if (live && !known) {
      documents.push({ token: input.documentToken, url, retiredAt: 0 });
      documents = documents.slice(-4); this.documents.set(tab.id, documents);
    }
    const form = { documentToken: input.documentToken, formToken: input.formToken, url, origin, tabId: tab.id, owner: tab.owner, allowFill: input.allowFill === true && actionOrigin === origin };
    if (input.kind === 'form') this.forms.set(tab.id, form);
    const vault = this.vault(tab.profile);
    let settings;
    try { settings = vault.load().settings; this.vaultErrors.delete(tab.profile.key); }
    catch {
      if (!this.vaultErrors.has(tab.profile.key)) { this.vaultErrors.add(tab.profile.key); manager.message('密码库暂时无法解锁，请在密码管理中检查钥匙串授权。'); }
      return;
    }
    if (input.kind === 'submitted') {
      if (!settings.save || typeof input.username !== 'string' || input.username.length > 4096 || typeof input.password !== 'string' || !input.password || input.password.length > 16384) return;
      const existing = vault.find(origin, input.username);
      if (existing?.password === input.password) return;
      this.fills.delete(tab.id); this.dismissPrompt(tab.id);
      const timer = setTimeout(() => { this.dismissPrompt(tab.id); manager.publish(); }, 120000); timer.unref();
      this.prompts.set(tab.id, { id: randomUUID(), owner: tab.owner, origin, username: input.username, password: input.password, existingId: existing?.id || '', existingUpdated: existing?.updatedAt || 0, timer });
      manager.publish(); return;
    }
    if (!form.allowFill) { this.fills.delete(tab.id); return; }
    const matches = vault.matches(origin);
    if (!matches.length) { this.fills.delete(tab.id); return; }
    const identity = `${tab.id}:${form.documentToken}:${form.formToken}`;
    if (settings.autofill && matches.length === 1 && input.hasPassword === false && (!input.username || input.username === matches[0].username) && !this.autofilled.has(identity)) {
      this.autofilled.add(identity); this.sendFill(tab, form, matches[0], false); this.fills.delete(tab.id);
    } else if (input.reason === 'focused' && (matches.length > 1 || !settings.autofill)) {
      this.fills.set(tab.id, { id: identity, tabId: tab.id, origin, accounts: matches.map(meta) });
    }
    manager.publish();
  }
  sendFill(tab, form, account, replace = true) {
    const wc = tab.view.webContents;
    if (this.manager.active() !== tab || tab.owner !== this.manager.owner || form?.owner !== tab.owner || !form?.allowFill || tab.pendingURL || wc.isDestroyed() || wc.getURL() !== form.url || account.origin !== form.origin) throw new Error('网页已变化，请重新选择登录表单。');
    wc.send('browser:password-fill', { ...form, username: account.username, password: account.password, replace });
  }
  async authenticate() {
    if (this.dependencies.authenticate) return this.dependencies.authenticate();
    if (systemPreferences.canPromptTouchID()) await systemPreferences.promptTouchID('查看保存的浏览器密码');
  }
  async command(event, input) {
    const manager = this.manager;
    manager.check(event, input?.lease);
    const lease = manager.lease, profile = manager.profile, owner = manager.owner;
    const ensure = () => { manager.check(event, lease); if (manager.profile !== profile || manager.owner !== owner) throw new Error('浏览器资料已切换。'); };
    const action = input?.action;
    if (action === 'cancel-import') {
      const operation = this.operation;
      if (operation?.lease === lease) { operation.controller.abort(); await operation.done; }
      ensure(); this.importer.cancelProfile(profile);
      return {};
    }
    if (this.operation) throw new Error('Chrome 资料正在读取或导入，请等待或取消。');
    const vault = this.vault(profile); let result = {};
    if (action === 'list') result = vault.list();
    else if (action === 'save') {
      const value = { ...input.value };
      if (input.id && !value.password) value.password = vault.get(input.id).password;
      vault.save(value, input.id); result = vault.list();
    } else if (action === 'remove') { vault.remove(input.id); result = vault.list(); }
    else if (action === 'settings') { vault.configure(input.value); result = vault.list(); }
    else if (action === 'reveal' || action === 'copy') {
      await this.authenticate(); ensure();
      const row = vault.get(input.id);
      if (action === 'reveal') result = { password: row.password };
      else {
        clipboard.writeText(row.password);
        const secret = row.password;
        const timer = setTimeout(() => { if (clipboard.readText() === secret) clipboard.clear(); }, 30000); timer.unref();
      }
    } else if (action === 'sources') result = { sources: this.importer.sources() };
    else if (action === 'preview' || action === 'import') {
      let finish;
      const done = new Promise(resolve => { finish = resolve; });
      const operation = { lease, controller: new AbortController(), done }; this.operation = operation;
      const checked = () => { operation.controller.signal.throwIfAborted(); ensure(); };
      try {
        result = action === 'preview'
          ? { preview: await this.importer.preview(profile, vault, input.value, checked, operation.controller.signal, stage => manager.send('browser:credentials-progress', { lease, stage })) }
          : { report: await this.importer.commit(profile, vault, input.value, checked) };
      } finally { if (this.operation === operation) this.operation = null; finish(); }
    } else if (action === 'fill') {
      const tab = manager.tab(input.tabId), fill = this.fills.get(tab.id);
      if (!fill || fill.id !== input.fillId || !fill.accounts.some(row => row.id === input.id)) throw new Error('账号选择已过期，请重新点击登录输入框。');
      this.sendFill(tab, this.forms.get(tab.id), vault.get(input.id)); this.fills.delete(tab.id);
    } else if (action === 'dismiss') {
      const tab = manager.active();
      if (tab) { this.dismissPrompt(tab.id); this.fills.delete(tab.id); }
    } else if (action === 'accept') {
      const tab = manager.active(), prompt = tab && this.prompts.get(tab.id);
      if (!prompt || prompt.id !== input.id || prompt.owner !== manager.owner) throw new Error('保存密码提示已过期。');
      // Prompt origin was authenticated at capture; a successful login may now show another site.
      const existing = vault.find(prompt.origin, prompt.username);
      if ((existing?.id || '') !== prompt.existingId || (existing?.updatedAt || 0) !== prompt.existingUpdated) throw new Error('密码库已变化，请在密码管理中确认。');
      vault.save(prompt, prompt.existingId || undefined); this.dismissPrompt(tab.id);
    } else throw new Error('不支持的密码操作。');
    ensure(); manager.publish(); return result;
  }
  dispose() {
    ipcMain.removeHandler('browser:credentials'); ipcMain.removeListener('browser:password-form', this.formHandler);
    this.invalidate();
    for (const vault of this.vaults.values()) vault.data = null;
    this.vaults.clear();
  }
}
