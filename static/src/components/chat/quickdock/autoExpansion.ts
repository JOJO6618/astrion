/** 自动展开快捷窗口后，对话区至少保留的宽度。 */
export const QUICK_DOCK_MIN_CHAT_WIDTH = 600;

/**
 * 只在自动展开入口调用；手动点击不受此限制。
 * 把 dock 当前占位加回，再扣除完整列宽，展开/收起动画中也使用同一空间预算。
 * 宽度未测得时保持收起，避免首帧先展开再纠正。
 */
export function canAutoExpandQuickDock(dock: HTMLElement | null): boolean {
  const chat = dock?.parentElement?.querySelector<HTMLElement>(':scope > .chat-container');
  if (!dock || !chat) return false;

  const chatWidth = chat.getBoundingClientRect().width;
  const dockWidth = dock.getBoundingClientRect().width;
  const expandedWidth = Number.parseFloat(
    getComputedStyle(dock).getPropertyValue('--qd-expanded-width')
  );
  if (
    !Number.isFinite(chatWidth) ||
    !Number.isFinite(dockWidth) ||
    !Number.isFinite(expandedWidth) ||
    chatWidth <= 0 ||
    dockWidth < 0 ||
    expandedWidth <= 0
  ) {
    return false;
  }

  return chatWidth + dockWidth - expandedWidth >= QUICK_DOCK_MIN_CHAT_WIDTH;
}
