/**
 * 发送 / 换行快捷键（用户级设置 personalization.send_shortcut，主输入框与桌面快捷窗口共用）。
 *
 * - mod_enter（默认）：Ctrl/⌘+Enter 发送；Enter、Shift+Enter 换行
 * - enter：Enter 发送；Shift+Enter、Ctrl/⌘+Enter 换行
 *
 * 约束：
 * - 输入法组字中（isComposing / keyCode 229）的 Enter 一律不处理，交回输入法确认候选词
 * - 触屏设备上 enter 模式不生效：软键盘回车始终换行，发送走按钮（外接键盘的 Ctrl/⌘+Enter 仍可发送）
 * - Mac 上 ⌘ 与 Ctrl 等价
 */
export type SendShortcut = 'mod_enter' | 'enter';

export const DEFAULT_SEND_SHORTCUT: SendShortcut = 'mod_enter';

export const normalizeSendShortcut = (value: unknown): SendShortcut =>
  value === 'enter' ? 'enter' : DEFAULT_SEND_SHORTCUT;

export type EnterIntent = 'send' | 'newline';

export const isImeComposing = (event: KeyboardEvent): boolean =>
  event.isComposing || event.keyCode === 229;

export const hasEnterModifier = (event: KeyboardEvent): boolean =>
  event.shiftKey || event.ctrlKey || event.metaKey;

export const isCoarsePointerDevice = (): boolean =>
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(hover: none), (pointer: coarse)').matches;

/**
 * 判定一次 Enter 按键的意图；非 Enter、输入法组字中、带 Alt 时返回 null（交给默认行为）。
 */
export function resolveEnterIntent(
  event: KeyboardEvent,
  shortcut: SendShortcut,
  coarsePointer = isCoarsePointerDevice()
): EnterIntent | null {
  if (event.key !== 'Enter' || isImeComposing(event) || event.altKey) return null;
  const mod = event.ctrlKey || event.metaKey;
  if (shortcut === 'enter' && !coarsePointer) {
    return hasEnterModifier(event) ? 'newline' : 'send';
  }
  return mod && !event.shiftKey ? 'send' : 'newline';
}

const isApplePlatform = (): boolean => {
  if (typeof navigator === 'undefined') return false;
  const nav = navigator as Navigator & { userAgentData?: { platform?: string } };
  const platform = nav.userAgentData?.platform || nav.platform || nav.userAgent || '';
  return /mac|iphone|ipad|ipod/i.test(platform);
};

/** 当前平台下的「修饰键+Enter」显示文本：Mac 为 ⌘+Enter，其它为 Ctrl+Enter。 */
export const modEnterLabel = (): string => (isApplePlatform() ? '⌘+Enter' : 'Ctrl+Enter');
