import { createHash } from 'node:crypto';

export const SECURE_WEB_PREFERENCES = Object.freeze({
  sandbox: true, contextIsolation: true, nodeIntegration: false,
  nodeIntegrationInSubFrames: false, webviewTag: false, webSecurity: true,
  allowRunningInsecureContent: false, spellcheck: true
});
export function profileKey(username) {
  if (typeof username !== 'string' || !username.trim()) throw new Error('browser_identity_required');
  return createHash('sha256').update(username.trim().toLowerCase()).digest('hex').slice(0, 32);
}
export function scopeKey(scope) {
  if (!scope || !['agent', 'chat'].includes(scope.mode)) throw new Error('invalid_browser_scope');
  const values = [scope.mode, scope.workspace || '', scope.conversation || 'new'];
  if (values.some(value => typeof value !== 'string' || value.length > 256 || /[\x00-\x1f]/.test(value))) throw new Error('invalid_browser_scope');
  return JSON.stringify(values);
}
export function navigationURL(value, search = false) {
  if (typeof value !== 'string' || value.length > 8192) throw new Error('invalid_browser_url');
  const input = value.trim();
  if (!input || input === 'about:blank') return 'about:blank';
  if (search && !/^https?:\/\//i.test(input) && !/^[\w.-]+\.[a-z]{2,}(?:[/:?#]|$)/i.test(input) && !/^(localhost|127\.0\.0\.1|\[::1\])(?::\d+)?(?:\/|$)/i.test(input)) {
    return `https://www.google.com/search?q=${encodeURIComponent(input)}`;
  }
  const url = new URL(/^[a-z][a-z\d+.-]*:/i.test(input) && !/^[\w.-]+:\d/.test(input) ? input : `${/^(localhost|127\.0\.0\.1|\[::1\])/.test(input) ? 'http' : 'https'}://${input}`);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('unsupported_browser_url');
  return url.href;
}
export function clipBounds(rect, mainBounds) {
  if (!rect || !['x', 'y', 'width', 'height'].every(key => Number.isFinite(rect[key]))) return null;
  const x = Math.max(0, Math.round(rect.x)), y = Math.max(0, Math.round(rect.y));
  const width = Math.min(Math.max(0, Math.floor(rect.width)), mainBounds.width - x);
  const height = Math.min(Math.max(0, Math.floor(rect.height)), mainBounds.height - y);
  if (width < 1 || height < 1) return null;
  return { x: mainBounds.x + x, y: mainBounds.y + y, width, height };
}
export function browserRoute(pathname) {
  return /^\/(?:$|new\/?$|conv_[a-zA-Z0-9_-]+\/?$|chat(?:\/[^/]+)?\/?$)/.test(pathname);
}
export function trustedFrame(event, view, origin) {
  try {
    const frame = event.senderFrame;
    const url = new URL(frame.url);
    return event.sender === view.webContents && frame === view.webContents.mainFrame && url.origin === origin
      && browserRoute(url.pathname);
  } catch { return false; }
}
export function safeFilename(value) {
  const name = String(value || 'download').replace(/[\x00-\x1f<>:"/\\|?*]/g, '_').replace(/^\.+/, '').slice(0, 160);
  return name || 'download';
}
