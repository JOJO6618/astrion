// 独立对话窗口（设计 §4.7）：BaseWindow + 一个铺满的内容视图（页面自绘约 38px 顶部栏）。
// 本模块只管窗口本身与位置记忆；视图的迁入迁出、关闭后放回由 ViewManager 负责。
// 位置记忆按标签 key 存 userData/conversation-windows.json（参考 browser/popout-window.js 的 placement()）。
import path from 'node:path';
import fs from 'node:fs';
import { app, BaseWindow, screen } from 'electron';
import { popoutChromeOptions, applyPopoutChromeTheme } from '../platform.js';
import { watchConversationNotificationFocus } from '../conversation-notifications.js';
import { DETACHED_BAR_HEIGHT, toInt } from './layout.js';

const DEFAULT_SIZE = { width: 980, height: 740 };
const MIN_SIZE = { width: 480, height: 360 };
const CASCADE = 28;
const MAX_REMEMBERED = 100;

const boundsFile = () => path.join(app.getPath('userData'), 'conversation-windows.json');
const validBounds = (value) => ['x', 'y', 'width', 'height'].every((key) => Number.isFinite(value?.[key]));
function readAll() {
  try {
    const value = JSON.parse(fs.readFileSync(boundsFile(), 'utf8'));
    if (value && typeof value === 'object' && !Array.isArray(value)) return value;
  } catch { /* 首次使用或文件损坏时按默认位置 */ }
  return {};
}
function remember(key, bounds) {
  try {
    const all = readAll();
    delete all[key]; // 重新插入以保持“最近使用”在末尾
    all[key] = { ...bounds, at: Date.now() };
    const keys = Object.keys(all);
    for (const old of keys.slice(0, Math.max(0, keys.length - MAX_REMEMBERED))) delete all[old];
    fs.writeFileSync(boundsFile(), JSON.stringify(all), { mode: 0o600 });
  } catch { /* 记忆失败不影响窗口 */ }
}

/** BaseWindow 构造参数要求整数（-0/NaN/小数会触发 gin 转换失败）；非有限数退回工作区原点/默认尺寸。 */
function integral(bounds, area) {
  return {
    x: toInt(bounds.x) ?? toInt(area.x) ?? 0, y: toInt(bounds.y) ?? toInt(area.y) ?? 0,
    width: toInt(bounds.width) || DEFAULT_SIZE.width, height: toInt(bounds.height) || DEFAULT_SIZE.height
  };
}

/** 记忆的位置（重启恢复）或按指针落点放置；与已开窗口重叠时错开，并夹到屏幕工作区内。 */
export function placement(key, point, occupied, offset = null) {
  const saved = readAll()[key];
  const known = validBounds(saved) ? saved : null;
  const anchor = point || (known ? { x: known.x, y: known.y } : null);
  const display = anchor ? screen.getDisplayNearestPoint({ x: Math.round(anchor.x), y: Math.round(anchor.y) }) : screen.getPrimaryDisplay();
  const area = display.workArea;
  const size = known ? { width: known.width, height: known.height } : DEFAULT_SIZE;
  const bounds = { ...size, x: 0, y: 0 };
  if (point && offset) { bounds.x = Math.round(point.x - offset.x); bounds.y = Math.round(point.y - offset.y); } // 拖出：指针落在顶部栏的抓取点上
  else if (point) { bounds.x = Math.round(point.x - 40); bounds.y = Math.round(point.y - 16); } // 左上角靠近指针
  else if (known) { bounds.x = known.x; bounds.y = known.y; }
  else { bounds.x = area.x + Math.round((area.width - size.width) / 2); bounds.y = area.y + Math.round((area.height - size.height) / 2); }
  for (let i = 0; i < 20 && !offset && occupied.some((row) => Math.abs(row.x - bounds.x) < 8 && Math.abs(row.y - bounds.y) < 8); i++) {
    bounds.x += CASCADE; bounds.y += CASCADE;
  }
  bounds.width = Math.round(Math.max(MIN_SIZE.width, Math.min(bounds.width, area.width)));
  bounds.height = Math.round(Math.max(MIN_SIZE.height, Math.min(bounds.height, area.height)));
  if (offset) { bounds.y = Math.max(area.y, bounds.y); return integral(bounds, area); } // 跟随指针：只保证顶部栏不越过屏幕上沿
  bounds.x = Math.round(Math.max(area.x, Math.min(bounds.x, area.x + area.width - bounds.width)));
  bounds.y = Math.round(Math.max(area.y, Math.min(bounds.y, area.y + area.height - bounds.height)));
  return integral(bounds, area);
}

/**
 * 创建独立对话窗口。callbacks：onClose()（用户关闭，窗口尚存）、onFocus()、onResize()、
 * onVisibility(visible)（隐藏/最小化为 false）。colors：{surface, chrome, text}。
 */
export function createDetachedWindow({ key, point, offset = null, focus = true, occupied = [], colors }, callbacks) {
  const bounds = placement(key, point, occupied, offset);
  const window = new BaseWindow({
    ...bounds, minWidth: MIN_SIZE.width, minHeight: MIN_SIZE.height, show: false,
    backgroundColor: colors.surface,
    ...popoutChromeOptions(process.platform, colors.chrome, colors.text, DETACHED_BAR_HEIGHT)
  });
  window.contentView.setBackgroundColor(colors.surface);
  const save = () => {
    if (window.isDestroyed() || window.isFullScreen() || window.isMinimized()) return;
    if (window.astrionDragging) return; // 拖出进行中（尚未确认）不写位置记忆
    remember(key, window.getNormalBounds());
  };
  window.astrionRemember = save;
  for (const event of ['resize', 'enter-full-screen', 'leave-full-screen', 'maximize', 'unmaximize']) window.on(event, () => callbacks.onResize?.());
  window.on('moved', save);
  window.on('resized', save);
  window.on('focus', () => callbacks.onFocus?.());
  // BaseWindow 不在 app 的 browser-window-focus 覆盖范围：独立对话窗口获得焦点也清除通知横幅。
  watchConversationNotificationFocus(window);
  const visibility = () => callbacks.onVisibility?.(!window.isDestroyed() && window.isVisible() && !window.isMinimized());
  for (const event of ['show', 'hide', 'minimize', 'restore']) window.on(event, visibility);
  window.on('close', () => { save(); callbacks.onClose?.(); });
  if (focus) { window.show(); window.focus(); } else window.showInactive();
  return window;
}

/** 独立窗口的 Windows 叠加层主题（保持 38px 高度）。 */
export const applyDetachedChromeTheme = (window, chrome, text) => applyPopoutChromeTheme(window, chrome, text, process.platform, DETACHED_BAR_HEIGHT);
