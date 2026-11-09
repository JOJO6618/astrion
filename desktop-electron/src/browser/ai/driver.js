// 单个浏览器标签的 AI 控制驱动：webContents.debugger（CDP）+ 隔离环境快照 + 真实输入事件。
// 不开放远程调试端口，不向模型透传原始 CDP。
import { BOOT_SCRIPT, WORLD_NAME } from './world.js';
import { keyEvents, charEvents, modifierBits } from './keys.js';

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const SETTLE_TYPES = new Set(['Fetch', 'XHR', 'Document']);
const STATIC_TYPES = new Set(['Image', 'Stylesheet', 'Font', 'Media', 'Script', 'Manifest', 'Other', 'TextTrack', 'Prefetch', 'Ping', 'CSPViolationReport']);
const SECRET_HEADERS = /^(cookie|set-cookie|authorization|proxy-authorization|x-csrf-token|x-xsrf-token|x-api-key)$/i;

export class BrowserToolError extends Error {
  constructor(code, message, extra = {}) { super(message); this.code = code; Object.assign(this, { extra }); }
}

export class TabDriver {
  constructor(tab) {
    this.tab = tab; this.wc = tab.view.webContents;
    this.attached = false; this.contextId = 0; this.docGen = 0; this.mainFrameId = '';
    this.dialog = null; this.dialogWaiters = new Set(); this.fileChooser = null; this.interceptFiles = false;
    this.requests = new Map(); this.requestOrder = []; this.requestSeq = 0; this.inflight = new Set();
    this.console = []; this.consoleSeq = 0;
    this.listeners = [];
    this.queue = Promise.resolve();
  }
  /** 同一标签的 AI 调用串行执行。 */
  run(task) {
    const next = this.queue.then(task, task);
    this.queue = next.catch(() => {});
    return next;
  }
  async attach() {
    if (this.attached) return;
    const wc = this.wc, dbg = wc.debugger;
    if (dbg.isAttached()) throw new BrowserToolError('debugger_busy', '该标签已被其他调试器占用（例如开发者工具），无法由 AI 操作。');
    // 从未加载过的 WebContents 还没有渲染进程，CDP 的 Page.enable 会一直挂起。
    if (!wc.getURL()) await wc.loadURL('about:blank').catch(() => {});
    dbg.attach('1.3');
    this.attached = true;
    const onMessage = (_event, method, params) => this.onEvent(method, params);
    const onDetach = () => { this.attached = false; this.contextId = 0; this.inflight.clear(); };
    dbg.on('message', onMessage); dbg.on('detach', onDetach);
    const onConsole = (...args) => this.onConsole(...args);
    wc.on('console-message', onConsole);
    this.listeners = [() => dbg.removeListener('message', onMessage), () => dbg.removeListener('detach', onDetach), () => wc.removeListener('console-message', onConsole)];
    await this.cdp('Page.enable');
    await this.cdp('Network.enable', { maxTotalBufferSize: 8 * 1024 * 1024, maxResourceBufferSize: 2 * 1024 * 1024 });
    // 后台/隐藏标签里页面也表现为拥有焦点，键盘事件与 :focus 才正常（Playwright 同做法）。
    await this.cdp('Emulation.setFocusEmulationEnabled', { enabled: true }).catch(() => {});
    const tree = await this.cdp('Page.getFrameTree');
    this.mainFrameId = tree.frameTree.frame.id;
    // 隐藏的标签也要正常排版、渲染和截图；AI 结束后由控制器释放。
    try { wc.incrementCapturerCount(undefined, true, false); this.capturing = true; } catch { this.capturing = false; }
    wc.setBackgroundThrottling(false);
  }
  detach() {
    for (const off of this.listeners.splice(0)) off();
    if (this.wc.isDestroyed()) return;
    if (this.capturing) { try { this.wc.decrementCapturerCount(true, false); } catch { /* destroyed */ } this.capturing = false; }
    this.wc.setBackgroundThrottling(true);
    if (this.attached) { try { this.wc.debugger.detach(); } catch { /* already detached */ } }
    this.attached = false; this.contextId = 0;
  }
  cdp(method, params = {}) {
    if (this.wc.isDestroyed()) return Promise.reject(new BrowserToolError('tab_closed', '标签已关闭。'));
    const p = this.wc.debugger.sendCommand(method, params);
    return p;
  }
  onEvent(method, params) {
    if (method === 'Page.javascriptDialogOpening') {
      this.dialog = { type: params.type, message: String(params.message || '').slice(0, 2000), defaultPrompt: params.defaultPrompt || '', url: params.url };
      for (const wake of this.dialogWaiters) wake();
    } else if (method === 'Page.javascriptDialogClosed') this.dialog = null;
    else if (method === 'Page.fileChooserOpened') this.fileChooser = { backendNodeId: params.backendNodeId, mode: params.mode };
    else if (method === 'Page.frameNavigated' && !params.frame.parentId) {
      this.docGen++; this.contextId = 0; this.mainFrameId = params.frame.id; this.fileChooser = null;
      const loader = params.frame.loaderId;
      this.requestOrder = this.requestOrder.filter(id => this.requests.get(id)?.loaderId === loader);
      for (const id of [...this.requests.keys()]) if (!this.requestOrder.includes(id)) this.requests.delete(id);
      this.console = [];
    } else if (method === 'Network.requestWillBeSent') {
      const known = this.requests.get(params.requestId);
      const row = known || { seq: ++this.requestSeq, id: params.requestId, loaderId: params.loaderId };
      Object.assign(row, { method: params.request.method, url: params.request.url, type: params.type || 'Other', requestHeaders: params.request.headers, postData: params.request.postData, hasPostData: !!params.request.hasPostData, status: 0, failed: '', done: false });
      if (!known) { this.requests.set(params.requestId, row); this.requestOrder.push(params.requestId); }
      if (this.requestOrder.length > 500) { const drop = this.requestOrder.shift(); this.requests.delete(drop); this.inflight.delete(drop); }
      if (SETTLE_TYPES.has(row.type)) this.inflight.add(params.requestId);
    } else if (method === 'Network.responseReceived') {
      const row = this.requests.get(params.requestId);
      if (row) Object.assign(row, { status: params.response.status, statusText: params.response.statusText, mimeType: params.response.mimeType, responseHeaders: params.response.headers, type: params.type || row.type });
    } else if (method === 'Network.loadingFinished' || method === 'Network.loadingFailed') {
      const row = this.requests.get(params.requestId);
      if (row) { row.done = true; if (method === 'Network.loadingFailed') row.failed = params.canceled ? 'canceled' : params.errorText || 'failed'; }
      this.inflight.delete(params.requestId);
    }
  }
  onConsole(event, legacyLevel, legacyMessage, legacyLine, legacySource) {
    const levels = ['debug', 'info', 'warning', 'error'];
    const level = typeof event?.level === 'string' ? (event.level === 'verbose' ? 'debug' : event.level) : levels[legacyLevel] || 'info';
    const message = String(event?.message ?? legacyMessage ?? '').slice(0, 2000);
    const source = event?.sourceId ?? legacySource ?? '', line = event?.lineNumber ?? legacyLine ?? 0;
    this.console.push({ seq: ++this.consoleSeq, level, message, source: source ? `${String(source).slice(0, 200)}:${line}` : '' });
    if (this.console.length > 300) this.console.shift();
  }
  checkDialog() {
    if (this.dialog) throw new BrowserToolError('dialog_pending', `页面有 ${this.dialog.type} 对话框等待处理，请先调用 browser_handle_dialog。`, { dialog: this.dialog });
  }
  async ensureWorld() {
    if (this.contextId) return this.contextId;
    const world = await this.cdp('Page.createIsolatedWorld', { frameId: this.mainFrameId, worldName: WORLD_NAME, grantUniveralAccess: false });
    this.contextId = world.executionContextId;
    await this.raw(BOOT_SCRIPT);
    return this.contextId;
  }
  async raw(expression, returnByValue = true) {
    const result = await this.cdp('Runtime.evaluate', { expression, contextId: this.contextId, returnByValue, awaitPromise: true });
    if (result.exceptionDetails) throw new BrowserToolError('page_script_failed', result.exceptionDetails.exception?.description?.split('\n')[0] || result.exceptionDetails.text || 'script failed');
    return returnByValue ? result.result.value : result.result;
  }
  /** 在隔离环境调用 __astrionAI 方法；上下文失效（跳转）时重建一次。 */
  async world(call, returnByValue = true) {
    this.checkDialog();
    for (let attempt = 0; ; attempt++) {
      try {
        await this.ensureWorld();
        return await this.raw(`globalThis.__astrionAI.${call}`, returnByValue);
      } catch (error) {
        if (attempt || error instanceof BrowserToolError || !/context|Execution context|Cannot find|target closed/i.test(String(error?.message))) throw error;
        this.contextId = 0;
      }
    }
  }
  /** 隐藏标签的渲染视口可能是 0×0（不会随 setBounds 更新）；此时用固定尺寸模拟，标签显示后撤销。 */
  async fixViewport(size) {
    if (this.tab.view.getVisible()) { await this.clearViewport(); return; }
    if (this.metricsOverride) return;
    const current = await this.cdp('Runtime.evaluate', { expression: '[innerWidth, innerHeight]', returnByValue: true });
    const [width, height] = current.result.value || [0, 0];
    if (width < 100 || height < 100) {
      // 按面板实际尺寸模拟，标签显示时排版不跳变；没有面板尺寸时用 1280×800。
      const target = size && size.width >= 100 && size.height >= 100 ? size : { width: 1280, height: 800 };
      await this.cdp('Emulation.setDeviceMetricsOverride', { width: Math.round(target.width), height: Math.round(target.height), deviceScaleFactor: 0, mobile: false });
      this.metricsOverride = true;
    }
  }
  /** 标签进入可见状态时立即撤销尺寸模拟，交还给真实视图尺寸。 */
  async clearViewport() {
    if (!this.metricsOverride) return;
    this.metricsOverride = false;
    if (this.attached && !this.wc.isDestroyed()) await this.cdp('Emulation.clearDeviceMetricsOverride').catch(() => {});
  }
  zoom() { return this.wc.getZoomFactor() || 1; }
  /** 输入事件会在网页同步弹出 alert/confirm 时一直不返回；对话框出现即视为已送达。 */
  input(method, params) {
    if (this.dialog) return Promise.resolve();
    const sent = this.cdp(method, params);
    sent.catch(() => {});
    return new Promise((resolve, reject) => {
      const wake = () => { this.dialogWaiters.delete(wake); resolve(); };
      this.dialogWaiters.add(wake);
      sent.then(() => { this.dialogWaiters.delete(wake); resolve(); }, error => { this.dialogWaiters.delete(wake); if (this.dialog) resolve(); else reject(error); });
    });
  }
  async mouse(type, x, y, extra = {}) {
    const zoom = this.zoom();
    await this.input('Input.dispatchMouseEvent', { type, x: x * zoom, y: y * zoom, ...extra });
  }
  async point(ref, position) {
    const point = await this.world(`point(${JSON.stringify(ref)}, ${JSON.stringify(position || null)})`);
    if (point.error === 'stale_ref') throw staleRef(ref);
    if (point.error === 'not_visible') throw new BrowserToolError('not_visible', `元素 ${point.element} 不可见或尺寸为 0。`);
    if (point.error) throw new BrowserToolError(point.error, `无法定位元素 ${point.element || ref}（${point.error}）。`);
    if (!point.ok) throw new BrowserToolError('element_obscured', `元素 ${point.element} 被 ${point.blocker || '其他元素'} 遮挡，未执行。可先关闭遮挡层或滚动后重试。`);
    return point;
  }
  async click(ref, { button = 'left', doubleClick = false, modifiers } = {}) {
    if (!['left', 'right', 'middle'].includes(button)) throw new BrowserToolError('invalid_argument', 'button 只能是 left/right/middle。');
    const bits = modifierBits(modifiers);
    const { x, y } = await this.point(ref);
    await this.mouse('mouseMoved', x, y, { modifiers: bits });
    for (let count = 1; count <= (doubleClick ? 2 : 1); count++) {
      await this.mouse('mousePressed', x, y, { button, clickCount: count, modifiers: bits, buttons: 1 });
      await this.mouse('mouseReleased', x, y, { button, clickCount: count, modifiers: bits });
    }
  }
  async hover(ref) { const { x, y } = await this.point(ref); await this.mouse('mouseMoved', x, y); }
  async type(ref, text, { slowly = false, submit = false } = {}) {
    if (typeof text !== 'string' || text.length > 20000) throw new BrowserToolError('invalid_argument', 'text 必须是不超过 20000 字符的字符串。');
    const focused = await this.world(`focusForTyping(${JSON.stringify(ref)})`);
    if (focused.error === 'stale_ref') throw staleRef(ref);
    if (focused.error) throw new BrowserToolError(focused.error, `元素 ${focused.element || ref} 不可输入。`);
    if (!focused.ok) { await this.click(ref); }
    if (!slowly) {
      if (text) await this.input('Input.insertText', { text });
      else for (const event of keyEvents('Delete')) await this.input('Input.dispatchKeyEvent', event);
    } else {
      for (const event of keyEvents('Delete')) await this.input('Input.dispatchKeyEvent', event);
      for (const char of text) {
        const events = charEvents(char);
        if (events) for (const event of events) await this.input('Input.dispatchKeyEvent', event);
        else await this.input('Input.insertText', { text: char });
        await sleep(30);
      }
    }
    if (submit) await this.press('Enter');
    return { password: focused.password };
  }
  async press(key) { for (const event of keyEvents(key)) await this.input('Input.dispatchKeyEvent', event); }
  async selectOption(ref, values) {
    if (!Array.isArray(values) || !values.length || values.some(v => typeof v !== 'string')) throw new BrowserToolError('invalid_argument', 'values 必须是非空字符串数组。');
    const result = await this.world(`selectOption(${JSON.stringify(ref)}, ${JSON.stringify(values)})`);
    if (result.error === 'stale_ref') throw staleRef(ref);
    if (result.error === 'not_select') throw new BrowserToolError('not_select', `元素 ${result.element} 不是原生下拉框；自定义下拉请先 browser_click 展开再点选项。`);
    if (result.error === 'option_missing') throw new BrowserToolError('option_missing', `下拉框中没有指定选项。可选：${result.available.join(' / ')}`);
    if (result.error) throw new BrowserToolError(result.error, `无法选择选项（${result.error}）。`);
    return result.selected;
  }
  async setChecked(ref, checked) {
    const state = await this.world(`checked(${JSON.stringify(ref)})`);
    if (state.error === 'stale_ref') throw staleRef(ref);
    if (state.error) throw new BrowserToolError(state.error, `元素 ${state.element || ref} 不是复选框或单选框。`);
    if (state.checked !== checked) await this.click(ref);
  }
  async setRange(ref, value) {
    const result = await this.world(`setRange(${JSON.stringify(ref)}, ${JSON.stringify(String(value))})`);
    if (result.error === 'stale_ref') throw staleRef(ref);
    if (result.error) throw new BrowserToolError(result.error, `元素 ${result.element || ref} 不是滑块。`);
  }
  async drag(startRef, endRef) {
    const start = await this.point(startRef);
    await this.cdp('Input.setInterceptDrags', { enabled: true });
    let intercepted = null;
    const onMessage = (_e, method, params) => { if (method === 'Input.dragIntercepted') intercepted = params.data; };
    this.wc.debugger.on('message', onMessage);
    try {
      await this.mouse('mouseMoved', start.x, start.y);
      await this.mouse('mousePressed', start.x, start.y, { button: 'left', clickCount: 1, buttons: 1 });
      const end = await this.point(endRef).catch(error => { throw error; });
      for (let i = 1; i <= 5; i++) await this.mouse('mouseMoved', start.x + (end.x - start.x) * i / 5, start.y + (end.y - start.y) * i / 5, { button: 'left', buttons: 1 });
      await sleep(30);
      if (intercepted) {
        const zoom = this.zoom();
        for (const type of ['dragEnter', 'dragOver', 'drop']) await this.cdp('Input.dispatchDragEvent', { type, x: end.x * zoom, y: end.y * zoom, data: intercepted });
      }
      await this.mouse('mouseReleased', end.x, end.y, { button: 'left', clickCount: 1 });
    } finally {
      this.wc.debugger.removeListener('message', onMessage);
      await this.cdp('Input.setInterceptDrags', { enabled: false }).catch(() => {});
    }
  }
  async drop(ref, { paths = [], data = {} } = {}) {
    const { x, y } = await this.point(ref);
    const items = Object.entries(data || {}).map(([mimeType, value]) => ({ mimeType, data: String(value) }));
    const dragData = { items, files: paths, dragOperationsMask: 1 | 2 | 16 };
    const zoom = this.zoom();
    for (const type of ['dragEnter', 'dragOver', 'drop']) await this.cdp('Input.dispatchDragEvent', { type, x: x * zoom, y: y * zoom, data: dragData });
  }
  async scroll(ref, direction = 'down', amount) {
    if (!['up', 'down', 'left', 'right'].includes(direction)) throw new BrowserToolError('invalid_argument', 'direction 只能是 up/down/left/right。');
    const viewport = await this.world('viewport()');
    let x = viewport.width / 2, y = viewport.height / 2;
    if (ref) ({ x, y } = await this.point(ref).catch(error => { if (error.code === 'element_obscured') return { x, y }; throw error; }));
    const step = Number.isFinite(amount) && amount > 0 ? Math.min(amount, 20000) : Math.round((direction === 'up' || direction === 'down' ? viewport.height : viewport.width) * 0.8);
    const deltaX = direction === 'left' ? -step : direction === 'right' ? step : 0;
    const deltaY = direction === 'up' ? -step : direction === 'down' ? step : 0;
    await this.mouse('mouseWheel', x, y, { deltaX, deltaY });
  }
  async setFiles(paths, ref) {
    if (ref) {
      const handle = await this.world(`element(${JSON.stringify(ref)})`, false);
      if (!handle.objectId) throw staleRef(ref);
      await this.cdp('DOM.setFileInputFiles', { files: paths, objectId: handle.objectId });
      await this.cdp('Runtime.releaseObject', { objectId: handle.objectId }).catch(() => {});
      return;
    }
    if (!this.fileChooser) throw new BrowserToolError('no_file_chooser', '当前没有打开的文件选择框。先点击上传按钮，或用 target 指定 <input type=file> 元素。');
    const chooser = this.fileChooser; this.fileChooser = null;
    if (chooser.mode === 'selectSingle' && paths.length > 1) throw new BrowserToolError('invalid_argument', '该文件选择框只接受一个文件。');
    await this.cdp('DOM.setFileInputFiles', { files: paths, backendNodeId: chooser.backendNodeId });
  }
  async interceptFileChooser(enabled) {
    if (this.interceptFiles === enabled || (this.dialog && !enabled)) return;
    this.interceptFiles = enabled;
    await this.cdp('Page.setInterceptFileChooserDialog', { enabled }).catch(() => {});
  }
  async handleDialog(accept, promptText) {
    if (!this.dialog) throw new BrowserToolError('no_dialog', '当前没有等待处理的网页对话框。');
    const dialog = this.dialog;
    await this.cdp('Page.handleJavaScriptDialog', { accept: !!accept, ...(typeof promptText === 'string' ? { promptText } : {}) });
    this.dialog = null;
    return dialog;
  }
  async evaluate(fn, ref) {
    if (typeof fn !== 'string' || !fn.trim() || fn.length > 20000) throw new BrowserToolError('invalid_argument', 'function 必须是 JavaScript 函数源码。');
    this.checkDialog();
    let result;
    if (ref) {
      const handle = await this.world(`element(${JSON.stringify(ref)})`, false);
      if (!handle.objectId) throw staleRef(ref);
      const node = await this.cdp('DOM.describeNode', { objectId: handle.objectId });
      const main = await this.cdp('DOM.resolveNode', { backendNodeId: node.node.backendNodeId });
      result = await this.cdp('Runtime.callFunctionOn', { objectId: main.object.objectId, functionDeclaration: fn, arguments: [{ objectId: main.object.objectId }], returnByValue: true, awaitPromise: true });
      await this.cdp('Runtime.releaseObject', { objectId: handle.objectId }).catch(() => {});
    } else {
      result = await this.cdp('Runtime.evaluate', { expression: `(${fn})()`, returnByValue: true, awaitPromise: true, userGesture: true });
    }
    if (result.exceptionDetails) throw new BrowserToolError('evaluate_failed', `脚本出错：${result.exceptionDetails.exception?.description?.split('\n')[0] || result.exceptionDetails.text}`);
    return result.result.value === undefined ? (result.result.description || 'undefined') : result.result.value;
  }
  async screenshot({ ref, fullPage = false } = {}) {
    this.checkDialog();
    // 标签视图隐藏时 CDP 截图拿不到合成帧会一直挂起；可视区域与元素截图走 Electron capturePage。
    if (fullPage) {
      try {
        const metrics = await this.cdp('Page.getLayoutMetrics');
        const size = metrics.cssContentSize || metrics.contentSize;
        const clip = { x: 0, y: 0, width: Math.min(size.width, 4000), height: Math.min(size.height, 12000), scale: 1 };
        const shot = await withTimeout(this.cdp('Page.captureScreenshot', { format: 'png', clip, captureBeyondViewport: true }), 4000, 'screenshot_timeout');
        return { data: shot.data, truncated: clip.height >= 12000 || clip.width >= 4000 };
      } catch (error) {
        if (error.code !== 'screenshot_timeout') throw error;
        const data = await this.capture();
        return { data, truncated: false, viewportOnly: true };
      }
    }
    let rect;
    if (ref) {
      const point = await this.world(`rect(${JSON.stringify(ref)})`);
      if (point.error) throw staleRef(ref);
      const view = await this.world('viewport()');
      rect = { x: point.x - view.scrollX, y: point.y - view.scrollY, width: point.width, height: point.height };
      const x = Math.max(0, rect.x), y = Math.max(0, rect.y);
      rect = { x, y, width: Math.min(rect.width - (x - rect.x), view.width - x), height: Math.min(rect.height - (y - rect.y), view.height - y) };
      if (rect.width < 1 || rect.height < 1) throw new BrowserToolError('not_visible', '目标元素不在可视区域内或尺寸为 0。');
    }
    return { data: await this.capture(rect), truncated: false };
  }
  async capture(rect) {
    const zoom = this.zoom();
    const area = rect ? { x: Math.floor(rect.x * zoom), y: Math.floor(rect.y * zoom), width: Math.ceil(rect.width * zoom), height: Math.ceil(rect.height * zoom) } : undefined;
    const image = await withTimeout(area ? this.wc.capturePage(area) : this.wc.capturePage(), 15000, 'screenshot_timeout');
    if (image.isEmpty()) throw new BrowserToolError('screenshot_empty', '截图为空（页面可能还没有渲染）。');
    return image.toPNG().toString('base64');
  }
  async emulateMedia({ media, colorScheme, reducedMotion }) {
    // 每次调用都完整设置三项；未提供的恢复默认（空字符串）。
    const features = [
      { name: 'prefers-color-scheme', value: colorScheme || '' },
      { name: 'prefers-reduced-motion', value: reducedMotion || '' }
    ];
    await this.cdp('Emulation.setEmulatedMedia', { media: media || '', features });
  }
  /** 动作后等待页面稳定：DOM 静默 + 无 fetch/xhr/document 请求；上限 cap（文档加载中延长到 15 秒）。 */
  async settle(signal, { cap = 3000, quiet = 300 } = {}) {
    const start = Date.now();
    await sleep(60);
    for (;;) {
      if (signal?.aborted) throw new BrowserToolError('cancelled', '已取消。');
      if (this.dialog) return { ms: Date.now() - start, dialog: true };
      const loading = this.wc.isLoadingMainFrame() && [...this.inflight].some(id => this.requests.get(id)?.type === 'Document');
      const limit = loading ? 15000 : cap;
      if (Date.now() - start >= limit) return { ms: Date.now() - start, capped: true };
      let idle = false;
      try {
        idle = await withTimeout(this.world(`quiet(${quiet}) && document.readyState !== 'loading'`), 1000, 'world_timeout');
      } catch (error) { if (error.code === 'dialog_pending') continue; idle = false; }
      if (idle && !this.inflight.size) return { ms: Date.now() - start, capped: false };
      await sleep(60);
    }
  }
  consoleMessages(level = 'info') {
    const order = ['debug', 'info', 'warning', 'error'];
    const min = order.indexOf(level === 'warn' ? 'warning' : level);
    if (min < 0) throw new BrowserToolError('invalid_argument', 'level 只能是 debug/info/warning/error。');
    return this.console.filter(row => order.indexOf(row.level) >= min);
  }
  networkList(includeStatic = false) {
    return this.requestOrder.map(id => this.requests.get(id)).filter(row => row && (includeStatic || !STATIC_TYPES.has(row.type)));
  }
  async networkDetail(seq) {
    const row = [...this.requests.values()].find(item => item.seq === seq);
    if (!row) throw new BrowserToolError('unknown_request', `没有编号为 ${seq} 的请求（页面跳转后旧请求会清空）。`);
    const redact = headers => Object.fromEntries(Object.entries(headers || {}).map(([k, v]) => [k, SECRET_HEADERS.test(k) ? '[已隐藏]' : String(v).slice(0, 1000)]));
    let body = '', bodyNote = '';
    if (row.done && !row.failed) {
      try {
        const res = await this.cdp('Network.getResponseBody', { requestId: row.id });
        if (res.base64Encoded) bodyNote = '二进制内容未显示';
        else { body = res.body.slice(0, 20000); if (res.body.length > 20000) bodyNote = `已截断，共 ${res.body.length} 字符`; }
      } catch { bodyNote = '响应体已不可用'; }
    } else bodyNote = row.failed ? `请求失败：${row.failed}` : '请求尚未完成';
    let postData = row.postData || '';
    if (!postData && row.hasPostData) {
      try { postData = (await this.cdp('Network.getRequestPostData', { requestId: row.id })).postData; } catch { /* unavailable */ }
    }
    return { seq: row.seq, method: row.method, url: row.url, type: row.type, status: row.status, statusText: row.statusText || '', mimeType: row.mimeType || '', failed: row.failed,
      requestHeaders: redact(row.requestHeaders), responseHeaders: redact(row.responseHeaders), postData: String(postData || '').slice(0, 4000), body, bodyNote };
  }
}

export function staleRef(ref) {
  return new BrowserToolError('stale_ref', `ref ${ref} 已失效（元素已移除或重新渲染），请看最新快照中的 ref。`);
}

export function withTimeout(promise, ms, code) {
  let timer;
  return Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new BrowserToolError(code, '操作超时。')), ms); })]).finally(() => clearTimeout(timer));
}
