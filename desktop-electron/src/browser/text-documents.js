// 纯文本类响应（text/plain、application/json、脚本源码等）在 Chromium 里被渲染成
// 「只有一个 <pre>、没有任何样式」的空文档：html/body 背景透明、默认文字色是黑色。
// 标签视图的底色是应用主题的 --surface-base（深色主题为深灰），于是深色模式下
// 变成「深底黑字」完全不可读。这里给这类文档补一份跟随应用主题的样式；
// 普通网页（text/html）不受任何影响。
// 判定与样式和 Windows 壳 desktop/src-tauri/src/browser/text_document.rs 保持一致。
const STYLE_ID = '__astrion_text_document_theme';
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
  const css = `html{background:${surface} !important;color-scheme:${dark ? 'dark' : 'light'}}`
    + `body{color:${text} !important}`;
  return `(() => { try {
    const type = String(document.contentType || '').toLowerCase();
    if (!type || ${JSON.stringify(DOCUMENT_TYPES)}.includes(type)) return false;
    const body = document.body;
    if (!body) return false;
    // Chromium 的文本查看器把响应包成单个 <pre>（JSON 还会多一个提示节点）。
    const children = Array.prototype.slice.call(body.children);
    if (!children.length || !children.some(child => child.tagName === 'PRE')) return false;
    let style = document.getElementById(${JSON.stringify(STYLE_ID)});
    if (!style) {
      style = document.createElement('style');
      style.id = ${JSON.stringify(STYLE_ID)};
      (document.head || document.documentElement).appendChild(style);
    }
    style.textContent = ${JSON.stringify(css)};
    return true;
  } catch (error) { return false; } })();`;
}

/** 每次文档加载完成（dom-ready）后判定并注入；命中后记在标签上，主题变化时重刷。 */
export async function applyTextDocumentTheme(manager, tab) {
  const wc = tab.view?.webContents;
  if (!wc || wc.isDestroyed() || tab.retiredDownload) return;
  try {
    const applied = await wc.executeJavaScript(textDocumentScript(manager));
    if (!wc.isDestroyed()) tab.textDocument = applied === true;
  } catch { tab.textDocument = false; }
}

export function refreshTextDocuments(manager) {
  for (const tab of manager.tabs.values()) {
    if (!tab.textDocument) continue;
    void applyTextDocumentTheme(manager, tab);
  }
}
