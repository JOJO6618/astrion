const ai = new shared.BrowserAI(manager);
const queues = new Map();
const cancelled = new Set();
const originalDriver = ai.driver.bind(ai);
ai.driver = tab => {
  const value = originalDriver(tab);
  // WebView2's CDP Runtime events carry console data; Electron's console event does not exist.
  if (!value.nativeConsole) {
    value.nativeConsole = true;
    tab.view.webContents.debugger.on('message', (_event, method, params) => {
      if (method === 'Runtime.consoleAPICalled') value.onConsole({ level: params.type === 'error' ? 'error' : params.type === 'warning' ? 'warning' : 'info', message: (params.args || []).map(arg => arg.value ?? arg.description ?? '').join(' ') });
    });
    const attach = value.attach.bind(value);
    value.attach = async () => { const attached = value.attached; await attach(); if (!attached) await value.cdp('Runtime.enable'); };
    const fixViewport = value.fixViewport.bind(value);
    value.fixViewport = async size => { if (!value.dialog) await fixViewport(size); };
  }
  return value;
};
// Keep vault contents in this private controller only for the duration of a login fill.
ai.fillSavedLogin = async function(tab, driver, args, ref, settle) {
  const records = await rpc('saved-login', tab);
  const local = Object.create(this);
  local.manager = Object.create(manager);
  local.manager.credentials = { vault: () => ({ matches: origin => records.filter(row => row.origin === origin) }) };
  try { return await shared.BrowserAI.prototype.fillSavedLogin.call(local, tab, driver, args, ref, settle); }
  finally { records.splice(0); }
};
async function execute(message) {
  const { payload, owner, profile, searchEngine, rows, reserved } = message;
  if (cancelled.delete(payload.call_id)) throw new driver.BrowserToolError('cancelled', '已取消。');
  manager.profiles.set(payload.identity.username.trim().toLowerCase(), { key: profile, searchEngine: () => searchEngine || 'google' });
  synchronize(rows, owner, reserved);
  activeCalls.set(owner, payload);
  try {
    if (payload.op === 'handle_dialog' && payload.args?.accept === true) {
      const tab = ai.current(owner, payload.args.tab_id);
      if (ai.drivers.get(tab.id)?.dialog?.type !== 'alert') ai.guardSite(tab, payload);
    }
    const result = await ai.call(payload);
    await Promise.allSettled([...outgoing]);
    return result;
  } finally { activeCalls.delete(owner); }
}
chrome.webview.addEventListener('message', event => {
  const message = event.data;
  if (message.type === 'reply') {
    const entry = waiting.get(message.id);
    if (!entry) return;
    waiting.delete(message.id); clearTimeout(entry.timer);
    if (message.error) entry.reject(new driver.BrowserToolError(message.code || 'browser_error', message.error));
    else entry.resolve(message.value);
  } else if (message.type === 'cancel') {
    cancelled.add(message.call_id); ai.cancel(message.call_id);
  } else if (message.type === 'adopt') {
    for (const tab of manager.tabs.values()) if (tab.owner === message.from) tab.owner = message.to;
    ai.migrateOwner(message.from, message.to);
  } else if (message.type === 'event') {
    const tab = manager.tabs.get(message.tab) || manager.reserved.find(tab => tab.id === message.tab);
    if (!tab) return;
    if (message.row) Object.assign(tab, message.row);
    const wc = tab.view.webContents;
    if (message.method === 'native.loaded') wc.emit('did-stop-loading');
    else if (message.method === 'native.closed') { wc.destroyed = true; wc.emit('destroyed'); manager.tabs.delete(tab.id); }
    else if (message.method === 'native.download') { if (tab.aiDownload) tab.aiDownload.items.push(message.params); }
    else wc.debugger.emit('message', {}, message.method, message.params || {});
  } else if (message.type === 'call') {
    const previous = queues.get(message.owner) || Promise.resolve();
    const next = previous.catch(() => {}).then(() => execute(message));
    queues.set(message.owner, next);
    next.then(value => post({ type: 'result', call_id: message.payload.call_id, value }), error => post({ type: 'result', call_id: message.payload.call_id, value: { success: false, code: error.code || 'browser_error', error: String(error.message || error).slice(0, 500), ...(error.extra || {}) } }));
    next.finally(() => { if (queues.get(message.owner) === next) queues.delete(message.owner); }).catch(() => {});
  }
});
post({ type: 'ready' });
