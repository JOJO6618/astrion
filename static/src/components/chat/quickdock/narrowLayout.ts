/**
 * 窄对话区的统一空间预算（桌面端）。
 *
 * 规则：对话区宽度低于 QUICK_DOCK_MIN_CHAT_WIDTH 时，依次折叠
 *   1) 快捷面板组（快捷面板、文件内容预览、预览窗口三者绑定，一起收）
 *   2) 右侧面板组（终端 + Git 变更，一起收）
 * 用户在空间不足时手动展开的面板组被「强制保留」，直到被关闭为止。
 * 折叠是单向的：变宽不会自动展开，因此不需要迟滞。
 */
/** 必须与 autoExpansion.ts 的 QUICK_DOCK_MIN_CHAT_WIDTH 相同（回归里有一致性断言）。 */
export const NARROW_MIN_CHAT_WIDTH = 300;

/** 文件内容预览／预览窗口共用的宽度存储键与默认值（与 FilePreviewPanel 保持一致）。 */
const PREVIEW_WIDTH_STORAGE_KEY = 'agents_qd_preview_width';
const PREVIEW_WIDTH_MIN = 320;
const PREVIEW_WIDTH_MAX = 860;
const PREVIEW_WIDTH_DEFAULT = 452;

/** 右侧面板（终端／Git）占位：面板宽度上限 600 + 拖拽手柄。 */
const RIGHT_PANELS_MAX_WIDTH = 600;
const RIGHT_PANELS_DEFAULT_WIDTH = 420;
const RIGHT_PANELS_HANDLE_WIDTH = 6;

export type NarrowCollapseTarget = 'quick-group' | 'right-panels' | null;

export interface NarrowLayoutSnapshot {
  chatWidth: number;
  /** 快捷面板组任一成员处于展开 */
  quickGroupOpen: boolean;
  quickGroupForced: boolean;
  /** 终端或 Git 变更面板处于展开 */
  rightPanelsOpen: boolean;
  rightPanelsForced: boolean;
}

/** 纯函数：当前宽度不足时，下一步该折叠哪一组；无需折叠返回 null。 */
export function decideNarrowCollapse(snapshot: NarrowLayoutSnapshot): NarrowCollapseTarget {
  const { chatWidth } = snapshot;
  if (!Number.isFinite(chatWidth) || chatWidth <= 0) return null;
  if (chatWidth >= NARROW_MIN_CHAT_WIDTH) return null;
  if (snapshot.quickGroupOpen && !snapshot.quickGroupForced) return 'quick-group';
  if (snapshot.rightPanelsOpen && !snapshot.rightPanelsForced) return 'right-panels';
  return null;
}

/** 当前对话区宽度；未挂载或不可见时返回 0。 */
export function measureChatWidth(): number {
  if (typeof document === 'undefined') return 0;
  const chat = document.querySelector<HTMLElement>('.chat-container');
  return chat ? chat.getBoundingClientRect().width : 0;
}

/** 在现有对话区宽度上再占用 addedWidth 后，是否仍保留足够宽度。测不到宽度时视为放不下。 */
export function hasRoomFor(addedWidth: number): boolean {
  const chatWidth = measureChatWidth();
  if (!Number.isFinite(chatWidth) || chatWidth <= 0) return false;
  return chatWidth - addedWidth >= NARROW_MIN_CHAT_WIDTH;
}

export function getStoredPreviewWidth(): number {
  if (typeof window === 'undefined' || !window.localStorage) return PREVIEW_WIDTH_DEFAULT;
  try {
    const raw = Number(window.localStorage.getItem(PREVIEW_WIDTH_STORAGE_KEY));
    if (!Number.isFinite(raw) || raw <= 0) return PREVIEW_WIDTH_DEFAULT;
    return Math.max(PREVIEW_WIDTH_MIN, Math.min(PREVIEW_WIDTH_MAX, raw));
  } catch {
    return PREVIEW_WIDTH_DEFAULT;
  }
}

export function getRightPanelsFootprint(rightWidth?: number): number {
  const width = Math.min(rightWidth || RIGHT_PANELS_DEFAULT_WIDTH, RIGHT_PANELS_MAX_WIDTH);
  return width + RIGHT_PANELS_HANDLE_WIDTH;
}
