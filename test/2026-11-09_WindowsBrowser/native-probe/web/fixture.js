(() => {
  const role = location.pathname.slice(1);
  document.documentElement.style.background = role === 'main' ? '#1ebec8' : role === 'page-a' ? '#dc323c' : '#f0e080';
  // Wry's own IPC listener expects strings; native listeners share this event.
  const emit = value => chrome.webview.postMessage(JSON.stringify({ role, ...value }));
  window.addEventListener('error', event => emit({ kind: 'script-error', message: event.message }));
  window.addEventListener('unhandledrejection', event => emit({ kind: 'script-error', message: String(event.reason) }));
  setTimeout(() => {
    if (role === 'popup') {
      emit({ kind: 'popup', hasOpener: !!window.opener, trustedBridge: !!window.astrionBrowser });
      window.opener?.postMessage('popup-reply', location.origin);
      return;
    }
    if (role === 'frame') {
      emit({ kind: 'iframe', topFrame: window.top === window });
      return;
    }
    if (role === 'page-a' && __WRITE__) {
      document.cookie = 'astrion_probe=synthetic; Max-Age=86400; SameSite=Lax; Path=/';
      localStorage.setItem('astrion-probe', 'synthetic');
    }
    emit({ kind: 'loaded', cookie: document.cookie, stored: localStorage.getItem('astrion-probe'), trustedBridge: !!window.astrionBrowser });
    if (role === 'main') {
      const frame = document.createElement('iframe');
      frame.src = '/frame';
      document.body.append(frame);
    }
    if (role === 'page-a') {
      window.addEventListener('message', event => {
        if (event.origin === location.origin && event.data === 'popup-reply') emit({ kind: 'opener-roundtrip', ok: true });
      });
      window.open('/popup', 'astrion-probe-popup');
    }
  }, 700);
})();
