(() => {
  if (window !== window.top || !window.chrome?.webview) return;
  const documentId = crypto.randomUUID();
  const pending = new Map();
  const listeners = new Map();
  let sequence = 0;
  function call(type, data = {}, wait = true) {
    const id = `${documentId}:${++sequence}`;
    const message = JSON.stringify({ bridge: 'astrion-browser-v1', document: documentId, id, type, data });
    if (!wait) { chrome.webview.postMessage(message); return; }
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { pending.delete(id); reject(new Error('browser_native_request_timeout')); }, type === 'credentials' ? 180000 : 30000);
      pending.set(id, { resolve, reject, timer });
      chrome.webview.postMessage(message);
    });
  }
  function subscribe(type, callback) {
    if (!listeners.has(type)) listeners.set(type, new Set());
    listeners.get(type).add(callback);
    if (type === 'ai-activity') call('hello', {}, false);
    return () => listeners.get(type)?.delete(callback);
  }
  chrome.webview.addEventListener('message', event => {
    const value = event.data;
    if (value?.bridge !== 'astrion-browser-v1' || value.document !== documentId) return;
    if (value.id) {
      const entry = pending.get(value.id);
      if (!entry) return;
      clearTimeout(entry.timer); pending.delete(value.id);
      if (value.error) entry.reject(new Error(value.error)); else entry.resolve(value.value);
    } else {
      for (const callback of listeners.get(value.event) || []) callback(value.value);
    }
  });
  addEventListener('pagehide', () => {
    for (const entry of pending.values()) { clearTimeout(entry.timer); entry.reject(new Error('stale_browser_view')); }
    pending.clear(); listeners.clear();
  }, { once: true });
  Object.defineProperty(window, 'astrionBrowser', { configurable: false, writable: false, value: Object.freeze({
    surfaceMode: 'regions',
    credentialFileImport: true,
    connect: (scope, labels) => call('connect', { scope, labels }),
    theme: value => call('theme', value, false),
    command: value => call('command', value),
    credentials: value => call('credentials', value),
    layout: value => call('layout', value, false),
    input: value => call('input', value, false),
    onState: callback => subscribe('state', callback),
    onShortcut: callback => subscribe('shortcut', callback),
    onContextMenu: callback => subscribe('context-menu', callback),
    onMessage: callback => subscribe('message', callback),
    onAiActivity: callback => subscribe('ai-activity', callback),
    onGeometryRequest: callback => subscribe('geometry-request', callback),
    onCredentialProgress: callback => subscribe('credentials-progress', callback)
  }) });
})();
