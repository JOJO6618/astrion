import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

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
/** 已绑定对话时，旧的稳定标签 key 作为别名，用于把 /new 阶段的标签迁移过来。 */
export function scopeAlias(scope) {
  if (typeof scope?.key !== 'string' || !scope.key || scope.key === scope.conversation) return '';
  if (scope.key.length > 256 || /[\x00-\x1f]/.test(scope.key)) throw new Error('invalid_browser_scope');
  return JSON.stringify([scope.mode, scope.workspace || '', scope.key]);
}
// Keep in sync with desktop/src-tauri/src/browser/policy.rs SEARCH_ENGINES.
export const SEARCH_ENGINES = {
  google: 'https://www.google.com/search?q=',
  bing_cn: 'https://cn.bing.com/search?q=',
  bing: 'https://www.bing.com/search?q=',
  baidu: 'https://www.baidu.com/s?wd='
};
export function searchURL(engine, query) {
  return `${SEARCH_ENGINES[engine] || SEARCH_ENGINES.google}${encodeURIComponent(query)}`;
}
/** search: false, or a search engine key (true means the default engine). Keywords become a search URL. */
export function navigationURL(value, search = false) {
  if (typeof value !== 'string' || value.length > 8192) throw new Error('invalid_browser_url');
  const input = value.trim();
  if (!input || input === 'about:blank') return 'about:blank';
  if (search && !/^https?:\/\//i.test(input) && !/^[\w.-]+\.[a-z]{2,}(?:[/:?#]|$)/i.test(input) && !/^(localhost|127\.0\.0\.1|\[::1\])(?::\d+)?(?:\/|$)/i.test(input)) {
    return searchURL(search === true ? 'google' : search, input);
  }
  const url = new URL(/^[a-z][a-z\d+.-]*:/i.test(input) && !/^[\w.-]+:\d/.test(input) ? input : `${/^(localhost|127\.0\.0\.1|\[::1\])/.test(input) ? 'http' : 'https'}://${input}`);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('unsupported_browser_url');
  return url.href;
}
export function manualNavigationURL(value, search = false) {
  if (typeof value !== 'string' || !/^file:/i.test(value.trim())) return navigationURL(value, search);
  if (value.length > 8192 || /[\x00-\x1f\x7f]/.test(value)) throw new Error('invalid_browser_url');
  const url = new URL(value.trim());
  if (url.protocol !== 'file:' || (url.hostname && url.hostname !== 'localhost') || url.username || url.password) throw new Error('unsupported_browser_url');
  const path = fileURLToPath(url);
  if (/^[\\/]{2}/.test(path) || /[\x00-\x1f\x7f]/.test(path)) throw new Error('unsupported_browser_url');
  return url.href;
}
export function isLocalFile(value) {
  try { return /^file:/i.test(value || '') && !!manualNavigationURL(value); } catch { return false; }
}
export function clipBounds(rect, mainBounds) {
  if (!rect || !['x', 'y', 'width', 'height'].every(key => Number.isFinite(rect[key]))) return null;
  const x = Math.max(0, Math.round(rect.x)), y = Math.max(0, Math.round(rect.y));
  const width = Math.min(Math.max(0, Math.floor(rect.width)), mainBounds.width - x);
  const height = Math.min(Math.max(0, Math.floor(rect.height)), mainBounds.height - y);
  if (width < 1 || height < 1) return null;
  return { x: mainBounds.x + x, y: mainBounds.y + y, width, height };
}
// 与后端对话路由一致（server/app_legacy.py ConversationIdConverter）：
// 对话页是裸路径 /<id>，旧式 /conv_<id> 仍兼容。
export function browserRoute(pathname) {
  return /^\/(?:$|new\/?$|(?:conv_)?\d{8}_\d{6}_\d{3}\/?$|chat(?:\/[^/]+)?\/?$)/.test(pathname);
}
export function trustedFrame(event, view, origin, route = browserRoute) {
  try {
    const frame = event.senderFrame;
    const url = new URL(frame.url);
    return event.sender === view.webContents && frame === view.webContents.mainFrame && url.origin === origin
      && route(url.pathname);
  } catch { return false; }
}
export function safeFilename(value) {
  const name = String(value || 'download').replace(/[\x00-\x1f<>:"/\\|?*]/g, '_').replace(/^\.+/, '').slice(0, 160);
  return name || 'download';
}
