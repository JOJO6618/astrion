// 纯文本类响应（text/plain、application/json、脚本源码等）由 Chromium 包装成 <pre>。
// 这些浏览器生成的文档使用应用主题配色。判定与配色同旧 Windows WebView2 实现保持一致。
// Chromium 会自己渲染的文档类型：不注入。
const DOCUMENT_TYPES = ['text/html', 'application/xhtml+xml', 'image/svg+xml', 'application/pdf'];
const COLOR = /^#[a-f\d]{6}$/i;
// 前端没上报文字色时（例如首次绘制前）按背景明暗兜底，不引入第二套主题来源。
const FALLBACK_DARK_TEXT = '#ffffff';
const FALLBACK_LIGHT_TEXT = '#141413';
const FALLBACK_LIGHT_SURFACE = '#faf9f5';

export function isDarkColor(color) {
  const value = parseInt(color.slice(1), 16);
  const r = (value >> 16) & 0xff, g = (value >> 8) & 0xff, b = value & 0xff;
  return 0.299 * r + 0.587 * g + 0.114 * b < 128;
}

/** 在页面里执行的注入脚本：只对文本渲染的文档生效，返回是否命中。 */
export function textDocumentScript(manager) {
  const surface = COLOR.test(manager.surfaceColor || '') ? manager.surfaceColor : FALLBACK_LIGHT_SURFACE;
  const dark = isDarkColor(surface);
  const text = COLOR.test(manager.textColor || '') ? manager.textColor : (dark ? FALLBACK_DARK_TEXT : FALLBACK_LIGHT_TEXT);
  const scheme = dark ? 'dark' : 'light';
  return `(() => { try {
    const type = String(document.contentType || '').toLowerCase();
    if (!type || ${JSON.stringify(DOCUMENT_TYPES)}.includes(type)) return false;
    const body = document.body;
    if (!body) return false;
    // Chromium 的文本查看器把响应包成单个 <pre>（JSON 还会多一个提示节点）。
    const children = Array.prototype.slice.call(body.children);
    if (!children.some(child => child.tagName === 'PRE')) return false;
    // 逐项设置 CSSOM 属性，响应的 CSP 仍可正常限制页面自己的样式标签与属性。
    document.documentElement.style.setProperty('background', ${JSON.stringify(surface)}, 'important');
    document.documentElement.style.setProperty('color-scheme', ${JSON.stringify(scheme)});
    body.style.setProperty('color', ${JSON.stringify(text)}, 'important');
    return true;
  } catch (error) { return false; } })();`;
}

/** 每次文档加载完成（dom-ready）后判定并注入；命中后记在标签上，主题变化时重刷。 */
export async function applyTextDocumentTheme(manager, tab) {
  const wc = tab.view?.webContents;
  if (!wc || wc.isDestroyed() || tab.retiredDownload) return;
  const palette = { surfaceColor: manager.surfaceColor, textColor: manager.textColor };
  try {
    const applied = await wc.executeJavaScript(textDocumentScript(palette));
    if (wc.isDestroyed() || tab.view?.webContents !== wc || tab.retiredDownload) return;
    tab.textDocument = applied === true;
    // 首次识别期间的切色可能发生在标签尚未进入刷新的名单时。
    if (tab.textDocument && (palette.surfaceColor !== manager.surfaceColor || palette.textColor !== manager.textColor)) {
      await applyTextDocumentTheme(manager, tab);
    }
  } catch { tab.textDocument = false; }
}

export function refreshTextDocuments(manager) {
  for (const tab of manager.tabs.values()) {
    if (!tab.textDocument) continue;
    void applyTextDocumentTheme(manager, tab);
  }
}
