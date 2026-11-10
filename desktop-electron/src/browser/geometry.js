import { clipBounds } from './policy.js';
import { refreshTextDocuments } from './text-documents.js';

// Project the right-docked surface immediately while the renderer catches up.
// The authoritative CSS rectangle replaces this projection on its next layout.
export function browserBounds(manager) {
  if (!manager.geometry) return null;
  const zoom = manager.mainView.webContents.getZoomFactor();
  const main = manager.mainView.getBounds();
  let rect = manager.geometry;
  const viewport = manager.geometryViewport;
  if (viewport) {
    const width = main.width / zoom, height = main.height / zoom;
    const panelWidth = Math.max(380, Math.min(viewport.panelWidth, width - 360));
    const surfaceWidth = Math.max(0, panelWidth - viewport.surfaceInset);
    const right = viewport.width - rect.x - rect.width;
    rect = { x: width - right - surfaceWidth, y: rect.y, width: surfaceWidth,
      height: Math.max(0, rect.height + height - viewport.height) };
  }
  return clipBounds(Object.fromEntries(Object.entries(rect).map(([key, value]) => [key, value * zoom])), main);
}

export function browserViewport(value) {
  if (!value || !['width', 'height', 'panelWidth', 'surfaceInset'].every(key => Number.isFinite(value[key]))) return null;
  if (value.width < 1 || value.height < 1 || value.width > 100000 || value.height > 100000 ||
    value.panelWidth < 380 || value.panelWidth > 100000 || value.surfaceInset < 0 || value.surfaceInset > 100) return null;
  return { width: value.width, height: value.height, panelWidth: value.panelWidth, surfaceInset: value.surfaceInset };
}

const COLOR = /^#[a-f\d]{6}$/i;
const themeColor = value => (typeof value === 'string' && COLOR.test(value) ? value.toLowerCase() : '');

/** 应用主题：接受 '#rrggbb' 或 { surfaceColor, textColor }。
 *  文字色只用于纯文本类文档（text-documents.js），普通网页不受影响。 */
export function setBrowserSurfaceColor(manager, payload) {
  const surface = themeColor(typeof payload === 'string' ? payload : payload?.surfaceColor);
  const text = themeColor(typeof payload === 'object' && payload ? payload.textColor : '');
  if (!surface) return;
  // 初始主题可能与缓存相同，但窗口根 View 仍需要明确的不透明背景。
  manager.window.setBackgroundColor(surface);
  manager.window.contentView.setBackgroundColor(surface);
  const changed = surface !== manager.surfaceColor || (!!text && text !== manager.textColor);
  if (text) manager.textColor = text;
  if (!changed) return;
  manager.surfaceColor = surface;
  for (const tab of manager.tabs.values()) {
    // 页面回收会把标签挂起（view 置空），背景同步跳过而不是抛错。
    const view = tab.view;
    if (!view || view.webContents.isDestroyed()) continue;
    view.setBackgroundColor(surface);
  }
  refreshTextDocuments(manager);
}
