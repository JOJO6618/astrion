// AI 浏览器操作控制器：按对话（owner）隔离标签、维护模型的当前标签指针与快照基线，
// 执行 22 个工具对应的操作。只由控制桥（随机 token 鉴权）调用。
import path from 'node:path';
import fs from 'node:fs';
import { profileKey, manualNavigationURL } from '../policy.js';
import { browserBounds } from '../geometry.js';
import { TabDriver, BrowserToolError, withTimeout } from './driver.js';
import { chooseSnapshot, truncate, ACTION_SNAPSHOT_LIMIT, EXPLICIT_SNAPSHOT_LIMIT } from './snapshot-text.js';
import { siteOf } from './site.js';
import { passwordOrigin } from '../passwords/vault.js';

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const ACTION_OPS = new Set(['click', 'type', 'fill_form', 'select_option', 'press_key', 'drag', 'drop', 'file_upload', 'evaluate', 'fill_saved_login']);
const DEFAULT_SIZE = { width: 1280, height: 800 };

export function aiOwner(identity) {
  const { username, workspace_id: workspace, conversation_id: conversation } = identity || {};
  for (const value of [username, workspace, conversation]) {
    if (typeof value !== 'string' || !value || value.length > 256 || /[\x00-\x1f]/.test(value)) throw new BrowserToolError('invalid_identity', '浏览器调用缺少有效的对话身份。');
  }
  return { username: username.trim().toLowerCase(), owner: `${profileKey(username)}:${JSON.stringify(['agent', workspace, conversation])}` };
}

export class BrowserAI {
  constructor(manager) {
    this.manager = manager;
    this.state = new Map(); // owner -> { seq, ids: Map<tabId, short>, current }
    this.drivers = new Map(); // tabId -> TabDriver
    this.calls = new Map(); // callId -> AbortController
  }
  ownerState(owner) {
    let state = this.state.get(owner);
    if (!state) { state = { seq: 0, ids: new Map(), current: '' }; this.state.set(owner, state); }
    return state;
  }
  /** 把 /new 阶段（稳定标签 key）的标签状态迁移到对话 owner。 */
  migrateOwner(from, to) {
    const state = this.state.get(from);
    if (state && !this.state.has(to)) { this.state.set(to, state); this.state.delete(from); }
  }
  shortId(owner, tab) {
    const state = this.ownerState(owner);
    if (!state.ids.has(tab.id)) state.ids.set(tab.id, `t${++state.seq}`);
    return state.ids.get(tab.id);
  }
  ownerTabs(owner) { return [...this.manager.tabs.values()].filter(tab => tab.owner === owner && !tab.section && (tab.suspended || (tab.view && !tab.view.webContents.isDestroyed()))); }
  tabByShort(owner, short) {
    const state = this.ownerState(owner);
    const id = [...state.ids].find(([, value]) => value === short)?.[0];
    const tab = id && this.manager.tabs.get(id);
    if (!tab || tab.owner !== owner || (!tab.suspended && (!tab.view || tab.view.webContents.isDestroyed()))) throw new BrowserToolError('unknown_tab', `本对话没有标签 ${short}。用 browser_tabs action=list 查看。`);
    return tab;
  }
  current(owner, short) {
    if (short) return this.tabByShort(owner, short);
    const state = this.ownerState(owner);
    const tab = this.manager.tabs.get(state.current);
    if (tab && tab.owner === owner && (tab.suspended || (tab.view && !tab.view.webContents.isDestroyed()))) return tab;
    const fallback = this.ownerTabs(owner).at(-1);
    if (!fallback) throw new BrowserToolError('no_tab', '本对话还没有浏览器标签。先用 browser_navigate 打开网址或 browser_tabs action=new。');
    state.current = fallback.id;
    return fallback;
  }
  profileFor(username) {
    const manager = this.manager;
    return manager.ensureProfile(username);
  }
  /** 网页应有的尺寸：显示该对话的宿主面板（Windows 共享脚本的 manager 代理没有宿主，回退原逻辑）。 */
  panelBounds(owner) { return this.manager.boundsFor ? this.manager.boundsFor(owner) : browserBounds(this.manager); }
  driver(tab) {
    let driver = this.drivers.get(tab.id);
    if (!driver) {
      driver = new TabDriver(tab);
      this.drivers.set(tab.id, driver);
      tab.view.webContents.once('destroyed', () => { this.drivers.delete(tab.id); });
    }
    const bounds = tab.view.getBounds();
    const panel = this.panelBounds(tab.owner);
    if (bounds.width < 50 || bounds.height < 50) tab.view.setBounds({ x: 0, y: 0, ...(panel ? { width: panel.width, height: panel.height } : DEFAULT_SIZE) });
    tab.ai = tab.ai || { modelFull: undefined, modelDocGen: -1, modelEpoch: -1 };
    return driver;
  }
  release(tab) {
    const driver = this.drivers.get(tab.id);
    if (driver) { driver.detach(); this.drivers.delete(tab.id); }
  }
  dispose() {
    for (const controller of this.calls.values()) controller.abort();
    for (const driver of this.drivers.values()) driver.detach();
    this.drivers.clear();
  }
  cancel(callId) { this.calls.get(callId)?.abort(); return this.calls.has(callId); }
  deleteOwner(owner) {
    for (const controller of this.calls.values()) if (controller.owner === owner) controller.abort();
    this.state.delete(owner);
  }
  tabInfo(owner, tab) {
    const wc = tab.view?.webContents;
    return { id: this.shortId(owner, tab), url: wc?.getURL() || tab.url || 'about:blank', title: wc?.getTitle() || tab.title || '', loading: wc?.isLoading() || false, suspended: !!tab.suspended };
  }
  reveal(owner, tab, payload) {
    // 已弹出到独立窗口的对话无需展开主窗口面板。
    if (payload.reveal !== true || this.manager.popouts?.has(owner)) return;
    const { workspace_id: workspace, conversation_id: conversation } = payload.identity || {};
    this.manager.send('browser:ai-activity', { workspace, conversation });
  }
  /** 预检：返回目标标签当前站点（用于审批），不进入标签串行队列。 */
  site(payload) {
    const { owner } = aiOwner(payload.identity);
    const tab = this.current(owner, payload.args?.tab_id);
    const url = tab.view?.webContents.getURL() || tab.url;
    return { success: true, site: siteOf(url), url, tab: this.tabInfo(owner, tab) };
  }
  async call(payload) {
    if (typeof payload?.call_id !== 'string' || !payload.call_id) throw new BrowserToolError('invalid_call', '缺少调用 ID。');
    const controller = new AbortController();
    controller.owner = aiOwner(payload.identity).owner;
    this.calls.set(payload.call_id, controller);
    try { return await this.dispatch(payload, controller.signal); }
    finally { this.calls.delete(payload.call_id); }
  }
  navigationTarget(value, payload, profile) {
    let url;
    try { url = manualNavigationURL(value, profile.searchEngine()); }
    catch { throw new BrowserToolError('unsupported_url', '支持 http/https 网址和可读范围内的本机 file:// 文件地址（不能包含用户名密码）。'); }
    TabDriver.checkFileURL(url, payload.file_urls);
    return url;
  }
  async dispatch(payload, signal) {
    const op = String(payload.op || ''), args = payload.args && typeof payload.args === 'object' ? payload.args : {};
    if (op === 'site') return this.site(payload);
    const { owner, username } = aiOwner(payload.identity);
    const profile = this.profileFor(username);
    const manager = this.manager;
    if (op === 'tabs') return this.tabsOp(owner, profile, args, payload, signal);
    if (op === 'navigate' && !args.tab_id && !this.ownerTabs(owner).length) {
      if (!args.url) throw new BrowserToolError('no_tab', '本对话还没有浏览器标签，请提供 url。');
      const tab = manager.registry.create('about:blank', { owner, profile, ai: true, select: false });
      this.ownerState(owner).current = tab.id;
      manager.follow(owner, tab);
    }
    let tab = this.current(owner, args.tab_id);
    if (tab.section) throw new BrowserToolError('internal_page', '该标签是浏览器管理页，不能由 AI 操作。');
    if (tab.suspended) TabDriver.checkFileURL(tab.url, payload.file_urls);
    tab = manager.retention?.resume(tab) || tab;
    const restored = !!tab.restored;
    if (restored && tab.view.webContents.isLoading()) await this.waitLoad(tab, signal, () => {});
    tab.lastActive = tab.lastAi = Date.now();
    const driver = this.driver(tab);
    return driver.run(async () => {
      await driver.attach();
      this.ownerState(owner).current = tab.id;
      // 跟随开启时，任何一次操作都把用户视图带到这次操作的标签；关闭时这里不写 selected。
      // 先跟随再修正视口：标签已可见时 fixViewport 直接用真实尺寸，不再反复设置/清除模拟视口。
      manager.follow(owner, tab);
      const panel = this.panelBounds(owner), zoom = tab.view.webContents.getZoomFactor() || 1;
      await driver.fixViewport(panel ? { width: panel.width / zoom, height: panel.height / zoom } : null);
      this.reveal(owner, tab, payload);
      if (ACTION_OPS.has(op)) this.guardSite(tab, payload);
      tab.aiDownload = payload.download_dir ? { dir: payload.download_dir, until: Infinity, items: [] } : null;
      tab.aiBusy = true;
      let result;
      try { result = await this.operate(op, args, { owner, profile, tab, driver, payload, signal }); }
      finally { tab.aiBusy = false; tab.lastActive = tab.lastAi = Date.now(); }
      if (restored) { result.notes = [...(result.notes || []), '该标签已在内存回收后重新加载，原页面运行状态已丢失。']; tab.restored = false; }
      if (tab.aiDownload) { tab.aiDownload.until = Date.now() + 5000; if (tab.aiDownload.items.length) result.downloads = tab.aiDownload.items.slice(); }
      if (driver.fileChooser && op !== 'file_upload') result.notes = [...(result.notes || []), '页面打开了文件选择框，请调用 browser_file_upload 提供文件（paths 为空数组表示取消）。'];
      result.tab = result.tab || this.tabInfo(owner, tab);
      return { success: true, ...result };
    }, payload.file_urls);
  }
  guardSite(tab, payload) {
    const allowed = payload.allowed_sites;
    if (allowed === null || allowed === undefined) {
      if (payload.any_site === true) return;
      throw new BrowserToolError('site_not_approved', '该操作没有站点授权。');
    }
    const site = siteOf(tab.view.webContents.getURL() || tab.url);
    if (!Array.isArray(allowed) || !allowed.includes(site)) {
      throw new BrowserToolError('site_not_approved', `页面当前在 ${site || '空白页'}，与批准的站点不一致，未执行。请查看当前页面后重新操作。`, { site });
    }
  }
  async tabsOp(owner, profile, args, payload, signal) {
    const manager = this.manager, state = this.ownerState(owner);
    const action = args.action || 'list';
    if (action === 'list') {
      const tabs = this.ownerTabs(owner).map(tab => ({ ...this.tabInfo(owner, tab), current: tab.id === state.current }));
      return { success: true, tabs };
    }
    if (action === 'new') {
      if (this.ownerTabs(owner).length >= 48) throw new BrowserToolError('tab_limit', '本对话浏览器标签数量已达上限（48），请先关闭不用的标签。');
      const url = args.url ? this.navigationTarget(args.url, payload, profile) : 'about:blank';
      const tab = manager.registry.create('about:blank', { owner, profile, ai: true, select: false });
      state.current = tab.id;
      manager.follow(owner, tab);
      if (url === 'about:blank') return { success: true, tab: this.tabInfo(owner, tab), message: '已新建空白标签。' };
      return this.dispatch({ ...payload, op: 'navigate', args: { url, tab_id: this.shortId(owner, tab) } }, signal);
    }
    let tab = this.tabByShort(owner, args.tab_id || '');
    if (action === 'select') {
      if (tab.suspended) TabDriver.checkFileURL(tab.url, payload.file_urls);
      tab = manager.retention?.resume(tab) || tab;
      if (tab.restored && tab.view.webContents.isLoading()) await this.waitLoad(tab, signal, () => {});
      tab.lastActive = tab.lastAi = Date.now();
      state.current = tab.id;
      manager.follow(owner, tab);
      const driver = this.driver(tab);
      return driver.run(async () => {
        await driver.attach();
        const snapshot = await this.snapshotFor(tab, driver, payload, { explicit: false });
        return { success: true, tab: this.tabInfo(owner, tab), snapshot, message: '已切换当前标签。' };
      }, payload.file_urls);
    }
    if (action === 'close') {
      const info = this.tabInfo(owner, tab);
      this.release(tab);
      manager.registry.close(tab);
      if (state.current === tab.id) state.current = this.ownerTabs(owner).filter(row => row.id !== tab.id).at(-1)?.id || '';
      return { success: true, tab: info, message: `已关闭标签 ${info.id}。` };
    }
    throw new BrowserToolError('invalid_argument', 'action 只能是 list/new/select/close。');
  }
  async waitLoad(tab, signal, start) {
    const wc = tab.view.webContents;
    await withTimeout(new Promise((resolve, reject) => {
      const done = () => { cleanup(); resolve(); };
      const abort = () => { cleanup(); reject(new BrowserToolError('cancelled', '已取消。')); };
      const inPage = (_e, _url, isMain) => { if (isMain) done(); };
      const cleanup = () => { wc.removeListener('did-stop-loading', done); wc.removeListener('did-fail-load', fail); wc.removeListener('did-navigate-in-page', inPage); signal?.removeEventListener('abort', abort); };
      const fail = (_e, code, _text, _url, isMain) => { if (isMain && code !== -3) done(); };
      wc.on('did-stop-loading', done); wc.on('did-fail-load', fail); wc.on('did-navigate-in-page', inPage); signal?.addEventListener('abort', abort);
      start();
    }), 30000, 'navigation_timeout').catch(error => { if (error.code !== 'navigation_timeout') throw error; });
  }
  async operate(op, args, context) {
    const { tab, driver, payload, signal, profile } = context;
    const wc = tab.view.webContents, manager = this.manager;
    const settleAndSnap = async (options = {}) => {
      const settled = await driver.settle(signal);
      return this.respondAfterAction(tab, driver, payload, settled, options, signal);
    };
    const withChooser = async action => {
      await driver.interceptFileChooser(true);
      try { await action(); }
      finally { if (!driver.fileChooser && !driver.dialog) await driver.interceptFileChooser(false); }
    };
    const ref = value => {
      if (typeof value !== 'string' || !/^(?:f\d+)?e\d+$/.test(value)) throw new BrowserToolError('invalid_argument', 'target 必须是快照中的 ref（如 e12）。');
      return value;
    };
    switch (op) {
      case 'navigate': {
        driver.checkDialog();
        const action = args.action || (args.url ? 'goto' : '');
        if (action === 'goto') {
          if (typeof args.url !== 'string' || !args.url.trim()) throw new BrowserToolError('invalid_argument', '缺少 url。');
          const url = this.navigationTarget(args.url, payload, profile);
          await this.waitLoad(tab, signal, () => manager.registry.load(tab, url));
        } else if (action === 'back' || action === 'forward') {
          const history = wc.navigationHistory;
          if (action === 'back' ? !history.canGoBack() : !history.canGoForward()) throw new BrowserToolError('no_history', action === 'back' ? '没有可后退的页面。' : '没有可前进的页面。');
          await this.waitLoad(tab, signal, () => (action === 'back' ? history.goBack() : history.goForward()));
        } else if (action === 'reload') {
          await this.waitLoad(tab, signal, () => { tab.error = ''; wc.reload(); });
        } else throw new BrowserToolError('invalid_argument', '请提供 url，或 action=back/forward/reload。');
        if (tab.error) return { success: false, error: `页面加载失败：${tab.error}`, code: 'navigation_failed' };
        return settleAndSnap();
      }
      case 'snapshot': {
        driver.checkDialog();
        const target = args.target ? ref(args.target) : undefined;
        const depth = args.depth === undefined ? undefined : Number(args.depth);
        if (depth !== undefined && (!Number.isInteger(depth) || depth < 1 || depth > 100)) throw new BrowserToolError('invalid_argument', 'depth 必须是 1–100 的整数。');
        return { snapshot: await this.snapshotFor(tab, driver, payload, { explicit: true, target, depth }) };
      }
      case 'find': return this.find(tab, driver, args);
      case 'click':
        await withChooser(() => driver.click(ref(args.target), { button: args.button, doubleClick: args.double_click === true, modifiers: args.modifiers }));
        return settleAndSnap();
      case 'hover': await driver.hover(ref(args.target)); return settleAndSnap();
      case 'type': {
        const typed = await driver.type(ref(args.target), args.text, { slowly: args.slowly === true, submit: args.submit === true });
        return settleAndSnap({ notes: typed.password ? ['已向密码框输入内容（内容不会出现在快照中）。'] : [] });
      }
      case 'fill_form': {
        if (!Array.isArray(args.fields) || !args.fields.length || args.fields.length > 50) throw new BrowserToolError('invalid_argument', 'fields 必须是 1–50 项的数组。');
        for (const [index, field] of args.fields.entries()) {
          if (signal.aborted) throw new BrowserToolError('cancelled', '已取消。');
          const target = ref(field?.target), type = field?.type || 'textbox';
          try {
            if (type === 'textbox') await driver.type(target, String(field.value ?? ''));
            else if (type === 'checkbox' || type === 'radio') await driver.setChecked(target, field.value === true || field.value === 'true');
            else if (type === 'combobox') await driver.selectOption(target, Array.isArray(field.value) ? field.value.map(String) : [String(field.value ?? '')]);
            else if (type === 'slider') await driver.setRange(target, field.value);
            else throw new BrowserToolError('invalid_argument', `不支持的字段类型 ${type}。`);
          } catch (error) {
            if (error instanceof BrowserToolError) { error.message = `第 ${index + 1} 个字段（${field?.element || target}）：${error.message}${index ? `（前 ${index} 个字段已填写）` : ''}`; }
            throw error;
          }
        }
        return settleAndSnap();
      }
      case 'select_option': {
        const selected = await driver.selectOption(ref(args.target), args.values);
        return settleAndSnap({ notes: [`已选择：${selected.join('、')}`] });
      }
      case 'press_key': await withChooser(() => driver.press(args.key)); return settleAndSnap();
      case 'drag': await driver.drag(ref(args.start_target), ref(args.end_target)); return settleAndSnap();
      case 'drop': {
        const paths = this.checkPaths(args.paths || [], payload);
        if (!paths.length && !(args.data && Object.keys(args.data).length)) throw new BrowserToolError('invalid_argument', 'paths 与 data 至少提供一个。');
        await driver.drop(ref(args.target), { paths, data: args.data });
        return settleAndSnap();
      }
      case 'file_upload': {
        const paths = this.checkPaths(args.paths || [], payload);
        await driver.setFiles(paths, args.target ? ref(args.target) : undefined);
        await driver.interceptFileChooser(false);
        return settleAndSnap({ notes: [paths.length ? `已选择 ${paths.length} 个文件。` : '已取消文件选择。'] });
      }
      case 'scroll': await driver.scroll(args.target ? ref(args.target) : undefined, args.direction, args.amount); return settleAndSnap();
      case 'wait_for': return this.waitFor(tab, driver, args, payload, signal);
      case 'take_screenshot': {
        const shot = await driver.screenshot({ ref: args.target ? ref(args.target) : undefined, fullPage: args.full_page === true });
        const notes = shot.truncated ? ['整页过长，只截取了前 12000 像素高度。'] : shot.viewportOnly ? ['标签当前不在界面上显示，整页截图不可用，已改为截取可视区域。'] : [];
        return { image_base64: shot.data, notes };
      }
      case 'handle_dialog': {
        const dialog = await driver.handleDialog(args.accept === true, args.prompt_text);
        return settleAndSnap({ notes: [`已${args.accept ? '确认' : '取消'} ${dialog.type} 对话框。`] });
      }
      case 'evaluate': {
        const value = await withTimeout(driver.evaluate(args.function, args.target ? ref(args.target) : undefined), 30000, 'evaluate_timeout');
        let text;
        try { text = typeof value === 'string' ? value : JSON.stringify(value, null, 2); } catch { text = String(value); }
        return { result: truncate(text ?? 'undefined', 20000).text };
      }
      case 'fill_saved_login': return this.fillSavedLogin(tab, driver, args, ref, settleAndSnap);
      case 'console_messages': return { messages: driver.consoleMessages(args.level || 'info').slice(-200) };
      case 'network_requests': return { requests: driver.networkList(args.include_static === true).slice(-200).map(row => ({ id: row.seq, method: row.method, url: row.url.slice(0, 500), type: row.type, status: row.status, failed: row.failed, done: row.done })) };
      case 'network_request': return { request: await driver.networkDetail(Number(args.id)) };
      case 'emulate_media': {
        const allowed = { media: ['screen', 'print'], color_scheme: ['light', 'dark', 'no-preference'], reduced_motion: ['reduce', 'no-preference'] };
        const value = key => (args[key] === undefined || args[key] === null || args[key] === 'default' ? undefined : args[key]);
        for (const [key, values] of Object.entries(allowed)) if (value(key) !== undefined && !values.includes(value(key))) throw new BrowserToolError('invalid_argument', `${key} 取值无效。`);
        await driver.emulateMedia({ media: value('media'), colorScheme: value('color_scheme'), reducedMotion: value('reduced_motion') });
        return settleAndSnap({ notes: ['已更新媒体模拟（仅影响该标签，直到重新设置）。'] });
      }
      default: throw new BrowserToolError('unknown_operation', `未知的浏览器操作 ${op}。`);
    }
  }
  /** 用浏览器已保存的账号密码填写登录框；密码只经 CDP 送入页面，不出现在任何返回值里。 */
  async fillSavedLogin(tab, driver, args, ref, settleAndSnap) {
    const passwordRef = ref(args.password_target);
    const usernameRef = args.username_target ? ref(args.username_target) : undefined;
    let origin;
    try { origin = passwordOrigin(tab.view.webContents.getURL()); } catch { throw new BrowserToolError('no_saved_login', '当前页面不是 http/https 网页。'); }
    let matches;
    try { matches = this.manager.credentials.vault(tab.profile).matches(origin); }
    catch (error) { throw new BrowserToolError('vault_locked', `密码库无法读取：${error.message}`); }
    if (!matches.length) throw new BrowserToolError('no_saved_login', `浏览器没有保存 ${origin} 的账号密码。请用户自己登录，或在对话中提供账号密码。`);
    const usernames = matches.map(row => row.username || '(无用户名)');
    let account;
    if (typeof args.username === 'string' && args.username) {
      account = matches.find(row => row.username === args.username);
      if (!account) throw new BrowserToolError('unknown_account', `没有保存账号「${args.username}」。已保存的账号：${usernames.join('、')}`);
    } else if (matches.length === 1) account = matches[0];
    else throw new BrowserToolError('choose_account', `${origin} 保存了多个账号，请用 username 指定：${usernames.join('、')}`);
    const isPassword = await driver.world(`isPassword(${JSON.stringify(passwordRef)})`);
    if (!isPassword) throw new BrowserToolError('not_password_field', 'password_target 必须是密码输入框。');
    if (usernameRef) {
      if (!account.username) throw new BrowserToolError('no_username', '这条保存记录没有用户名，请省略 username_target。');
      await driver.type(usernameRef, account.username);
    }
    await driver.type(passwordRef, account.password);
    return settleAndSnap({ notes: [`已用浏览器保存的账号「${account.username || '(无用户名)'}」填写登录信息（密码不会显示），尚未提交。`] });
  }
  checkPaths(paths, payload) {
    if (!Array.isArray(paths) || paths.length > 20 || paths.some(item => typeof item !== 'string')) throw new BrowserToolError('invalid_argument', 'paths 必须是不超过 20 项的路径数组。');
    const allowed = Array.isArray(payload.readable_paths) ? payload.readable_paths : null;
    if (!allowed && paths.length) throw new BrowserToolError('path_not_allowed', '文件路径未通过可读范围校验。');
    for (const file of paths) {
      if (!allowed.includes(file)) throw new BrowserToolError('path_not_allowed', `文件不在可读范围内：${file}`);
      let stat;
      try { stat = fs.statSync(file); } catch { throw new BrowserToolError('file_not_found', `文件不存在：${file}`); }
      if (!stat.isFile()) throw new BrowserToolError('not_a_file', `不是普通文件：${file}`);
    }
    return paths.map(file => path.resolve(file));
  }
  async find(tab, driver, args) {
    driver.checkDialog();
    const query = args.text ?? args.regex;
    if (typeof query !== 'string' || !query || query.length > 500) throw new BrowserToolError('invalid_argument', '请提供 text 或 regex（≤500 字符）。');
    let test;
    if (args.regex !== undefined) {
      let pattern;
      try { pattern = new RegExp(args.regex, 'i'); } catch { throw new BrowserToolError('invalid_argument', 'regex 无效。'); }
      test = line => pattern.test(line);
    } else { const needle = query.toLowerCase(); test = line => line.toLowerCase().includes(needle); }
    const snap = await driver.world(`snapshot(${JSON.stringify({})})`);
    const lines = String(snap.full || '').split('\n');
    const matches = [];
    let total = 0;
    for (let i = 0; i < lines.length; i++) {
      if (!test(lines[i])) continue;
      total++;
      if (matches.length >= 30) continue;
      // 附上最近的带 ref 的祖先，便于定位上下文。
      const indent = lines[i].match(/^ */)[0].length;
      let parent = '';
      for (let j = i - 1; j >= 0; j--) {
        const own = lines[j].match(/^ */)[0].length;
        if (own < indent && /\[ref=/.test(lines[j])) { parent = lines[j].trim().replace(/:$/, '').slice(0, 160); break; }
      }
      matches.push({ line: lines[i].trim().slice(0, 300), parent });
    }
    return { matches, total };
  }
  async waitFor(tab, driver, args, payload, signal) {
    const timeout = Math.min(Math.max(Number(args.timeout) || 10, 1), 60) * 1000;
    if (args.time !== undefined) {
      const seconds = Number(args.time);
      if (!Number.isFinite(seconds) || seconds < 0 || seconds > 60) throw new BrowserToolError('invalid_argument', 'time 必须是 0–60 秒。');
      const until = Date.now() + seconds * 1000;
      while (Date.now() < until) { if (signal.aborted) throw new BrowserToolError('cancelled', '已取消。'); await sleep(Math.min(200, until - Date.now())); }
    } else {
      const text = args.text ?? args.text_gone;
      if (typeof text !== 'string' || !text) throw new BrowserToolError('invalid_argument', '请提供 time、text 或 text_gone 之一。');
      const gone = args.text === undefined;
      const start = Date.now();
      for (;;) {
        if (signal.aborted) throw new BrowserToolError('cancelled', '已取消。');
        if (driver.dialog) break;
        const body = await driver.world('text()').catch(() => '');
        if (String(body).includes(text) !== gone) break;
        if (Date.now() - start > timeout) {
          return { success: false, code: 'wait_timeout', error: `等待超时（${timeout / 1000} 秒）：文字「${text}」${gone ? '仍然存在' : '没有出现'}。` };
        }
        await sleep(200);
      }
    }
    const settled = await driver.settle(signal);
    return this.respondAfterAction(tab, driver, payload, settled, {}, signal);
  }
  async snapshotFor(tab, driver, payload, { explicit, target, depth }) {
    const epoch = Number.isSafeInteger(payload.epoch) ? payload.epoch : 0;
    if (target || depth) {
      const snap = await driver.world(`snapshot(${JSON.stringify({ target, depth })})`);
      if (snap.error === 'stale_ref') throw new BrowserToolError('stale_ref', `ref ${target} 已失效，请看最新快照中的 ref。`);
      return { kind: 'full', partial: true, ...truncate(snap.full, EXPLICIT_SNAPSHOT_LIMIT) };
    }
    const newDocument = explicit || tab.ai.modelDocGen !== driver.docGen || tab.ai.modelEpoch !== epoch || tab.ai.modelFull === undefined;
    const snap = await driver.world(`snapshot(${JSON.stringify({ track: `m${epoch}` })})`);
    const choice = chooseSnapshot({ newDocument, incremental: snap.incremental, beforeFull: tab.ai.modelFull, afterFull: snap.full, limit: explicit ? EXPLICIT_SNAPSHOT_LIMIT : ACTION_SNAPSHOT_LIMIT });
    Object.assign(tab.ai, { modelFull: snap.full, modelDocGen: driver.docGen, modelEpoch: epoch });
    return choice;
  }
  async respondAfterAction(tab, driver, payload, settled, { notes = [] } = {}, signal) {
    const result = { notes: [...notes] };
    if (settled.capped) result.notes.push('页面持续变化，已在 3 秒上限时返回当前状态。');
    if (driver.dialog) {
      result.dialog = driver.dialog;
      result.notes.push(`页面弹出了 ${driver.dialog.type} 对话框，需要先调用 browser_handle_dialog 处理。`);
      return result;
    }
    let snapshot = await this.snapshotFor(tab, driver, payload, { explicit: false });
    if (snapshot.kind === 'none') {
      // 纯定时器触发的更新可能晚于稳定判定；判定「无变化」前再多等 1 秒。
      await sleep(1000);
      if (signal?.aborted) throw new BrowserToolError('cancelled', '已取消。');
      if (!driver.dialog) {
        const later = await this.snapshotFor(tab, driver, payload, { explicit: false });
        if (later.kind !== 'none') snapshot = later;
      }
    }
    result.snapshot = snapshot;
    return result;
  }
  /** 深压缩后由后端递增 epoch；这里不需要主动清理，下次调用自然返回完整快照。 */
}

export { BrowserToolError };
