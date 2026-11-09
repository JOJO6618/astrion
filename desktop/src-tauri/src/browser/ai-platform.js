// Runs only in the private native controller, never inside a website or the app UI.
let rpcSequence = 0;
const waiting = new Map();
const activeCalls = new Map();
const tabCalls = new Map();
const outgoing = new Set();
const post = value => chrome.webview.postMessage(JSON.stringify(value));
function rpc(type, tab, data = {}) {
  const id = String(++rpcSequence);
  const call = tab && activeCalls.get(tab.owner);
  const promise = new Promise((resolve, reject) => {
    const timer = setTimeout(() => { waiting.delete(id); reject(new Error('native_browser_timeout')); }, 65000);
    waiting.set(id, { resolve, reject, timer });
    post({ type, id, tab: tab?.id, owner: tab?.owner, call_id: call?.call_id, data });
  });
  outgoing.add(promise);
  promise.then(() => outgoing.delete(promise), () => outgoing.delete(promise));
  return promise;
}
class Emitter {
  constructor() { this.events = new Map(); }
  on(name, fn) { if (!this.events.has(name)) this.events.set(name, new Set()); this.events.get(name).add(fn); return this; }
  once(name, fn) { const once = (...args) => { this.removeListener(name, once); fn(...args); }; return this.on(name, once); }
  removeListener(name, fn) { this.events.get(name)?.delete(fn); }
  emit(name, ...args) { for (const fn of [...(this.events.get(name) || [])]) fn(...args); }
}
const manager = {
  tabs: new Map(), profiles: new Map(), reserved: [],
  ensureProfile(username) { const profile = this.profiles.get(username.trim().toLowerCase()); if (!profile) throw new Error('unknown_browser_profile'); return profile; },
  send(name, value) { if (name === 'browser:ai-activity') post({ type: 'activity', data: value }); },
  credentials: { vault() { throw new Error('credentials_require_native_request'); } },
  registry: {
    create(_url, { owner }) {
      const tab = manager.reserved.find(tab => tab.owner === owner);
      if (!tab) throw new Error('native_tab_not_ready');
      manager.reserved = manager.reserved.filter(item => item !== tab);
      manager.tabs.set(tab.id, tab);
      return tab;
    },
    load(tab, url) { rpc('navigate', tab, { url }).catch(error => { tab.error = error.message; tab.view.webContents.emit('did-stop-loading'); }); },
    close(tab) { rpc('close', tab).catch(() => {}); manager.tabs.delete(tab.id); tab.view.webContents.destroyed = true; tab.view.webContents.emit('destroyed'); }
  }
};
function makeTab(row) {
  const tab = { ...row, ai: undefined, aiDownload: null };
  const wc = new Emitter();
  wc.destroyed = false;
  wc.isDestroyed = () => wc.destroyed;
  wc.getURL = () => tab.url;
  wc.getTitle = () => tab.title;
  wc.isLoading = wc.isLoadingMainFrame = () => tab.loading;
  wc.getZoomFactor = () => tab.zoom || 1;
  wc.setBackgroundThrottling = wc.incrementCapturerCount = wc.decrementCapturerCount = () => {};
  wc.loadURL = url => rpc('navigate', tab, { url });
  wc.reload = () => rpc('navigate', tab, { action: 'reload' });
  wc.navigationHistory = {
    canGoBack: () => !!tab.back, canGoForward: () => !!tab.forward,
    goBack: () => rpc('navigate', tab, { action: 'back' }),
    goForward: () => rpc('navigate', tab, { action: 'forward' })
  };
  const dbg = new Emitter();
  dbg.attached = false;
  dbg.isAttached = () => dbg.attached;
  dbg.attach = () => { dbg.attached = true; };
  dbg.detach = () => { dbg.attached = false; dbg.emit('detach'); };
  dbg.sendCommand = (method, params) => rpc('cdp', tab, { method, params });
  wc.debugger = dbg;
  wc.capturePage = async area => {
    const shot = await rpc('capture', tab);
    let data = shot.data;
    if (area && data) {
      const image = new Image();
      await new Promise((resolve, reject) => { image.onload = resolve; image.onerror = reject; image.src = 'data:image/png;base64,' + data; });
      const viewport = await dbg.sendCommand('Runtime.evaluate', { expression: '[innerWidth,innerHeight]', returnByValue: true });
      const [width, height] = viewport.result.value;
      const scaleX = image.width / width / (tab.zoom || 1), scaleY = image.height / height / (tab.zoom || 1);
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.ceil(area.width * scaleX)); canvas.height = Math.max(1, Math.ceil(area.height * scaleY));
      canvas.getContext('2d').drawImage(image, area.x * scaleX, area.y * scaleY, canvas.width, canvas.height, 0, 0, canvas.width, canvas.height);
      data = canvas.toDataURL('image/png').split(',')[1];
    }
    return { isEmpty: () => !data, toPNG: () => ({ toString: () => data }) };
  };
  tab.view = {
    webContents: wc,
    getBounds: () => ({ x: 0, y: 0, width: 1280, height: 800 }),
    setBounds: () => {}, getVisible: () => !!tab.visible
  };
  return tab;
}
function synchronize(rows, owner, reserved) {
  const ids = new Set(rows.map(row => row.id));
  for (const [id, tab] of manager.tabs) {
    if (tab.owner === owner && !ids.has(id)) {
      tab.view.webContents.destroyed = true; tab.view.webContents.emit('destroyed'); manager.tabs.delete(id);
    }
  }
  for (const row of rows) {
    let tab = manager.tabs.get(row.id);
    if (!tab) tab = makeTab(row);
    else Object.assign(tab, row);
    if (row.id === reserved) { manager.reserved = manager.reserved.filter(item => item.owner !== owner); manager.reserved.push(tab); }
    else manager.tabs.set(tab.id, tab);
  }
}
// Keep in sync with policy.rs SEARCH_ENGINES and desktop-electron policy.js.
const SEARCH_ENGINES = {
  google: 'https://www.google.com/search?q=',
  bing_cn: 'https://cn.bing.com/search?q=',
  bing: 'https://www.bing.com/search?q=',
  baidu: 'https://www.baidu.com/s?wd='
};
const platform = {
  path: { resolve: value => value },
  // Paths were canonicalized by the shared backend and are rechecked natively at use.
  fs: { statSync: () => ({ isFile: () => true }) },
  profileKey: username => manager.ensureProfile(username).key,
  // search: false or the profile's engine key. Mirrors navigationURL in desktop-electron policy.js.
  manualNavigationURL(value, search = false) {
    if (value === 'about:blank') return value;
    const input = String(value).trim();
    if (search && input.length <= 8192 && !/^[a-z][a-z\d+.-]*:/i.test(input) && !/^[\w.-]+\.[a-z]{2,}(?:[/:?#]|$)/i.test(input) && !/^(localhost|127\.0\.0\.1|\[::1\])(?::\d+)?(?:\/|$)/i.test(input))
      return `${SEARCH_ENGINES[search] || SEARCH_ENGINES.google}${encodeURIComponent(input)}`;
    const url = new URL(/^[a-z][a-z\d+.-]*:/i.test(input) && !/^[\w.-]+:\d/.test(input) ? input : `${/^(localhost|127\.0\.0\.1|\[::1\])/.test(input) ? 'http' : 'https'}://${input}`);
    if (!['http:', 'https:', 'file:'].includes(url.protocol) || url.username || url.password) throw new Error('invalid_browser_url');
    if (url.protocol === 'file:' && ((url.hostname && url.hostname !== 'localhost') || !/^\/[a-z]:\//i.test(url.pathname))) throw new Error('invalid_browser_url');
    return url.href;
  },
  browserBounds: () => null,
  passwordOrigin(value) { const url = new URL(value); if (!['http:', 'https:'].includes(url.protocol)) throw new Error('invalid_password_origin'); return url.origin; }
};
