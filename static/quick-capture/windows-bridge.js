(() => {
  window.__ASTRION_DESKTOP__ = true;
  window.__ASTRION_PLATFORM__ = 'windows';
  const callbacks = new Map();
  const on = (name, callback) => {
    let handlers = callbacks.get(name);
    if (!handlers) callbacks.set(name, (handlers = new Set()));
    handlers.add(callback);
    return () => handlers.delete(callback);
  };
  window.__astrionQuickEmit = (name, value) => {
    for (const handler of callbacks.get(name) || []) handler(value);
  };
  const call = async (op, args = {}) => {
    const response = await fetch('/bridge', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ op, ...args })
    });
    const result = await response.json();
    if (!response.ok || !result.ok)
      throw new Error(result.error || `Native bridge HTTP ${response.status}`);
    return result.data;
  };
  const notify = (op, args) => {
    void call(op, args).catch((error) => console.error('[quick-native]', error));
  };
  if (location.pathname.startsWith('/capture')) {
    window.capture = {
      select: (rect) => notify('selection', { rect }),
      selectWindow: (id) => notify('window-selection', { id }),
      cancel: () => notify('hide'),
      dismiss: () => notify('hide'),
      windows: (callback) => on('windows', callback),
      exclude: (callback) => on('exclude', callback)
    };
  } else {
    let lastDomEscape = -Infinity;
    let composing = false;
    document.addEventListener('compositionstart', () => { composing = true; }, true);
    document.addEventListener('compositionend', () => { composing = false; }, true);
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') lastDomEscape = performance.now();
    }, true);
    on('native-escape', () => {
      const receivedAt = performance.now();
      setTimeout(() => {
        const delivered = lastDomEscape >= receivedAt - 150;
        notify('diagnostic', { data: { stage: 'escape', delivered, composing, focused: document.hasFocus(), active: document.activeElement?.tagName } });
        // WebView2 can report a focused textarea while keyboard events go elsewhere.
        // Fall back only when the real Esc did not arrive, preserving IME and menu handling.
        if (!delivered && !composing) {
          document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        }
      }, 80);
    });
    window.astrionQuick = {
      request: (route, method = 'GET', body, workspace = '') =>
        call('request', { route, method, body, workspace }),
      info: () => call('info'),
      configure: (patch) => call('configure', { patch }),
      ready: () => notify('ready'),
      hide: () => notify('hide'),
      hidden: () => notify('hidden'),
      layout: (regions, presentation) => notify('layout', { regions, presentation }),
      capturePermission: () => call('capture-permission'),
      onCapture: (callback) => on('capture', callback),
      onCaptureError: (callback) => on('capture-error', callback),
      onShow: (callback) => on('show', callback),
      onHide: (callback) => on('will-hide', callback),
      onPointer: (callback) => on('pointer-position', callback)
    };
  }
})();
