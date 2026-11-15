// 桌面多视图隐藏态：壳层显示/隐藏视图时经 astrionShell.onVisibility 通知。
// 不依赖 document.visibilityState；网页端没有该桥，恒为可见。
type Listener = (visible: boolean) => void;

let visible = true;
let subscribed = false;
const listeners = new Set<Listener>();

/** 可见时任务事件轮询 250ms，隐藏时降为约 1s（从不完全停止）。 */
export const VISIBLE_POLL_MS = 250;
export const HIDDEN_POLL_MS = 1000;

export function isViewVisible(): boolean {
  return visible;
}

export function pollingDelayMs(): number {
  return visible ? VISIBLE_POLL_MS : HIDDEN_POLL_MS;
}

export function setViewVisible(next: boolean): void {
  next = Boolean(next);
  if (next === visible) return;
  visible = next;
  for (const listener of [...listeners]) {
    try {
      listener(visible);
    } catch {
      // 单个订阅者失败不影响其他订阅者。
    }
  }
}

/** 订阅可见性变化；返回取消函数。 */
export function onViewVisibilityChange(listener: Listener): () => void {
  ensureShellSubscription();
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function ensureShellSubscription(): void {
  if (subscribed) return;
  const shell = (window as any).astrionShell;
  if (!shell?.onVisibility) return;
  subscribed = true;
  shell.onVisibility((payload: { visible?: boolean }) =>
    setViewVisible(payload?.visible !== false)
  );
}

/** 测试用：重置模块状态。 */
export function resetViewVisibilityForTest(): void {
  visible = true;
  subscribed = false;
  listeners.clear();
}

ensureShellSubscription();
