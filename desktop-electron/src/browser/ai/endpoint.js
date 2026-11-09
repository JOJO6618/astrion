// 控制桥上的 AI 浏览器端点：校验桌面壳下发给后端的随机 token，再交给当前窗口的 BrowserAI。
import { timingSafeEqual } from 'node:crypto';
import { BrowserToolError } from './driver.js';

let activeBrowser = null;
let expectedToken = '';

export function setActiveBrowser(browser, owner) {
  if (browser) activeBrowser = browser;
  else if (!owner || activeBrowser === owner) activeBrowser = null;
}
export function setBrowserToken(token) { expectedToken = String(token || ''); }

function authorized(header) {
  if (!expectedToken || typeof header !== 'string') return false;
  const a = Buffer.from(header), b = Buffer.from(expectedToken);
  return a.length === b.length && timingSafeEqual(a, b);
}

/** 返回 [status, body]。 */
export async function handleBrowserRequest(pathname, headers, payload) {
  if (!authorized(headers['x-astrion-browser-token'])) return [403, { success: false, code: 'forbidden', error: 'forbidden' }];
  const browser = activeBrowser;
  if (!browser || browser.disposed) return [200, { success: false, code: 'browser_unavailable', error: '内置浏览器不可用：Astrion 主窗口未打开或不是 macOS 桌面版。' }];
  if (pathname === '/browser/cancel') return [200, { success: true, cancelled: browser.ai.cancel(String(payload?.call_id || '')) }];
  try {
    return [200, await browser.ai.call(payload || {})];
  } catch (error) {
    if (error instanceof BrowserToolError) return [200, { success: false, code: error.code, error: error.message, ...(error.extra || {}) }];
    const message = String(error?.message || error);
    if (/invalid_browser_url|unsupported_browser_url/.test(message)) return [200, { success: false, code: 'unsupported_url', error: '只支持 http/https 网址（不能包含用户名密码）。' }];
    if (/browser_tab_limit/.test(message)) return [200, { success: false, code: 'tab_limit', error: '浏览器标签数量已达上限（48）。' }];
    if (/invalid_key/.test(message)) return [200, { success: false, code: 'invalid_argument', error: 'key 无效，示例：Enter、ArrowDown、Control+A、Meta+Shift+K。' }];
    if (/invalid_modifiers/.test(message)) return [200, { success: false, code: 'invalid_argument', error: 'modifiers 只能包含 Alt/Control/Meta/Shift。' }];
    return [200, { success: false, code: 'browser_error', error: message.slice(0, 500) }];
  }
}
