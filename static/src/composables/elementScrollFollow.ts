import { nextTick } from 'vue';

type FollowOptions = {
  threshold?: number;
  isLocked?: () => boolean;
  setLocked?: (locked: boolean) => void;
  isCurrent?: () => boolean;
};

/** 内容增长只请求追底；只有用户滚动输入可以解除跟随。 */
export function createElementScrollFollow(element: HTMLElement, options: FollowOptions = {}) {
  let locked = true;
  let disposed = false;
  let queued = false;
  let pointerScrolling = false;
  let touchY: number | null = null;
  let downInputAt = 0;
  let lastTop = element.scrollTop;
  let lastHeight = element.scrollHeight;
  let lastClientHeight = element.clientHeight;
  let observedContent: Element | null = null;
  const threshold = options.threshold ?? 12;
  const isLocked = () => options.isLocked?.() ?? locked;
  const isCurrent = () => !disposed && element.isConnected && (options.isCurrent?.() ?? true);
  const setLocked = (value: boolean) => {
    locked = value;
    options.setLocked?.(value);
  };
  const snapshot = () => {
    lastTop = element.scrollTop;
    lastHeight = element.scrollHeight;
    lastClientHeight = element.clientHeight;
  };
  const nearBottom = () =>
    element.scrollHeight - element.scrollTop - element.clientHeight <= threshold;

  // 内层思考/代码块有自己的阅读位置，其输入不能解除外层展开区的跟随。
  const belongsToElement = (target: EventTarget | null) => {
    let node = target instanceof Element ? target : null;
    while (node && node !== element) {
      if (node instanceof HTMLElement && node.scrollHeight > node.clientHeight + 2) {
        const overflow = getComputedStyle(node).overflowY;
        if (overflow === 'auto' || overflow === 'scroll') return false;
      }
      node = node.parentElement;
    }
    return node === element;
  };
  const input = (delta: number) => {
    if (!isCurrent() || !delta) return;
    if (delta < 0) {
      downInputAt = 0;
      if (element.scrollTop > 0) setLocked(false);
    } else {
      downInputAt = Date.now();
      if (nearBottom()) {
        setLocked(true);
        requestFollow();
      }
    }
  };
  const onWheel = (event: WheelEvent) => {
    if (belongsToElement(event.target)) input(event.deltaY);
  };
  const onTouchStart = (event: TouchEvent) => {
    touchY = event.touches.length === 1 ? event.touches[0].clientY : null;
  };
  const onTouchMove = (event: TouchEvent) => {
    if (touchY === null || event.touches.length !== 1) return;
    const nextY = event.touches[0].clientY;
    if (belongsToElement(event.target)) input(touchY - nextY);
    touchY = nextY;
  };
  const onTouchEnd = () => { touchY = null; };
  const onKeyDown = (event: KeyboardEvent) => {
    if (!belongsToElement(event.target)) return;
    const target = event.target as HTMLElement;
    if (target.closest('input, textarea, [contenteditable="true"]')) return;
    if (['ArrowUp', 'PageUp', 'Home'].includes(event.key)) input(-1);
    if (['ArrowDown', 'PageDown', 'End'].includes(event.key)) input(1);
    if (event.key === ' ') input(event.shiftKey ? -1 : 1);
  };
  const onPointerDown = (event: PointerEvent) => {
    if (event.target !== element || event.button !== 0) return;
    const rect = element.getBoundingClientRect();
    const gutter = element.offsetWidth - element.clientWidth;
    if (gutter <= 0 || event.clientX < rect.right - gutter) return;
    pointerScrolling = true;
    // 拖动滚动条时先停止写入，避免下一次流式更新抢走拖动位置。
    setLocked(false);
    snapshot();
  };
  const onPointerEnd = () => {
    if (!pointerScrolling) return;
    pointerScrolling = false;
    if (isLocked()) requestFollow();
  };
  const onScroll = () => {
    const delta = element.scrollTop - lastTop;
    const layoutChanged = element.scrollHeight !== lastHeight ||
      element.clientHeight !== lastClientHeight;
    if (pointerScrolling && !layoutChanged) {
      input(delta);
    }
    if (delta > 0 && !layoutChanged && Date.now() - downInputAt < 600 && nearBottom()) {
      setLocked(true);
      requestFollow();
    }
    snapshot();
  };

  const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => {
    // 先接纳布局变化；浏览器随后派发的 clamp/锚定 scroll 不属于用户拖动。
    snapshot();
    requestFollow();
  });
  observer?.observe(element);
  const observeContent = () => {
    const child = element.firstElementChild;
    if (child === observedContent) return;
    if (observedContent) observer?.unobserve(observedContent);
    observedContent = child;
    if (child) observer?.observe(child);
  };
  function requestFollow() {
    if (!isCurrent() || queued) return;
    queued = true;
    // 等当前批次的 Vue DOM 提交；异步 Markdown 高度变化另由 RO 继续驱动。
    void nextTick(() => {
      queued = false;
      if (!isCurrent()) return;
      observeContent();
      if (!isLocked() || pointerScrolling) return;
      element.scrollTop = Math.max(0, element.scrollHeight - element.clientHeight);
      snapshot();
    });
  }

  element.addEventListener('wheel', onWheel, { passive: true });
  element.addEventListener('touchstart', onTouchStart, { passive: true });
  element.addEventListener('touchmove', onTouchMove, { passive: true });
  element.addEventListener('touchend', onTouchEnd, { passive: true });
  element.addEventListener('touchcancel', onTouchEnd, { passive: true });
  element.addEventListener('keydown', onKeyDown);
  element.addEventListener('pointerdown', onPointerDown);
  element.addEventListener('scroll', onScroll, { passive: true });
  element.ownerDocument.addEventListener('pointerup', onPointerEnd);
  element.ownerDocument.addEventListener('pointercancel', onPointerEnd);
  observeContent();

  return {
    requestFollow,
    scroll: onScroll,
    reset() {
      downInputAt = 0;
      setLocked(true);
      requestFollow();
    },
    dispose() {
      disposed = true;
      observer?.disconnect();
      element.removeEventListener('wheel', onWheel);
      element.removeEventListener('touchstart', onTouchStart);
      element.removeEventListener('touchmove', onTouchMove);
      element.removeEventListener('touchend', onTouchEnd);
      element.removeEventListener('touchcancel', onTouchEnd);
      element.removeEventListener('keydown', onKeyDown);
      element.removeEventListener('pointerdown', onPointerDown);
      element.removeEventListener('scroll', onScroll);
      element.ownerDocument.removeEventListener('pointerup', onPointerEnd);
      element.ownerDocument.removeEventListener('pointercancel', onPointerEnd);
    }
  };
}

export type ElementScrollFollow = ReturnType<typeof createElementScrollFollow>;
