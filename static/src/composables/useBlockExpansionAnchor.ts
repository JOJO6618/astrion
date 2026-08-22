import { type Ref } from 'vue';

export type BlockExpandDirection = 'auto' | 'up' | 'down';

interface AnchorAnimation {
  element: HTMLElement;
  startTime: number;
  duration: number;
  mode: 'up' | 'down';
  initialTop: number;
  initialBottom: number;
  extendOnGrowth: boolean;
  phase: 'expand' | 'collapse';
}

interface UseBlockExpansionAnchorOptions {
  stopScroll: () => void;
  duration?: number;
  defaultDirection?: BlockExpandDirection;
}

/**
 * 块展开/折叠时，以块的某条边为锚点保持视口位置稳定。
 *
 * 传统 stick-to-bottom 在内容高度变化时会用弹簧动画追底，和 CSS 展开动画不同步，
 * 产生顿挫。该 composable 在 CSS 过渡期间用 rAF 持续覆盖 scrollTop。
 *
 * 方向策略：
 * - 展开时根据块顶边在视口的位置决定：上半向下展开（顶边不动），下半向上展开（底边不动）。
 * - 该方向在本次展开-收起周期内保持一致，确保收起是展开的倒放。
 * - 收起动画结束后清除方向记录，下次展开再根据新位置重新判断。
 */
export function useBlockExpansionAnchor(
  scrollRef: Ref<HTMLElement | null>,
  options: UseBlockExpansionAnchorOptions
) {
  const { stopScroll, duration = 300, defaultDirection = 'auto' } = options;
  const animations = new Map<string, AnchorAnimation>();
  const preferredModes = new Map<string, 'up' | 'down'>();
  let rafId: number | null = null;

  function resolveMode(rect: DOMRect, containerRect: DOMRect, direction: BlockExpandDirection) {
    if (direction !== 'auto') return direction;
    const top = rect.top - containerRect.top;
    const midY = containerRect.height / 2;
    return top > midY ? 'up' : 'down';
  }

  function tick() {
    const container = scrollRef.value;
    if (!container) {
      rafId = null;
      return;
    }

    const containerRect = container.getBoundingClientRect();
    const now = performance.now();
    let hasActive = false;

    for (const [id, anim] of animations) {
      const elapsed = now - anim.startTime;
      const stillInDom =
        document.body.contains(container) && container.contains(anim.element);
      if (!stillInDom) {
        animations.delete(id);
        continue;
      }

      const rect = anim.element.getBoundingClientRect();
      const currentTop = rect.top - containerRect.top;
      const currentBottom = rect.bottom - containerRect.top;

      let delta = 0;
      if (anim.mode === 'up') {
        delta = currentBottom - anim.initialBottom;
      } else {
        delta = -(currentTop - anim.initialTop);
      }

      const stillGrowing = Math.abs(delta) > 1;
      const shouldExtend = anim.extendOnGrowth && stillGrowing && elapsed > anim.duration * 0.6;
      if (shouldExtend) {
        anim.startTime = now - anim.duration * 0.3;
      }

      if (elapsed >= anim.duration && !shouldExtend) {
        // 收起动画结束后清除方向记录，下次展开按新位置重新判断
        if (anim.phase === 'collapse') {
          preferredModes.delete(id);
        }
        animations.delete(id);
        continue;
      }

      hasActive = true;

      if (Math.abs(delta) > 0.5) {
        container.scrollTop += delta;
      }
    }

    if (hasActive) {
      rafId = requestAnimationFrame(tick);
    } else {
      rafId = null;
    }
  }

  function anchorBlockElement(
    element: HTMLElement,
    id: string,
    opts?: {
      duration?: number;
      direction?: BlockExpandDirection;
      extendOnGrowth?: boolean;
      phase?: 'expand' | 'collapse';
    }
  ) {
    const container = scrollRef.value;
    if (!container) return;

    const containerRect = container.getBoundingClientRect();
    const rect = element.getBoundingClientRect();
    const initialTop = rect.top - containerRect.top;
    const initialBottom = rect.bottom - containerRect.top;

    // 只处理视口附近可见的块
    if (initialBottom < -50 || initialTop > containerRect.height + 50) {
      return;
    }

    const phase = opts?.phase ?? 'expand';
    const explicitDirection = opts?.direction ?? defaultDirection;
    let mode: 'up' | 'down';

    if (explicitDirection !== 'auto') {
      mode = explicitDirection;
    } else if (phase === 'collapse' && preferredModes.has(id)) {
      // 收起时沿用展开时记录的方向，保证倒放一致
      mode = preferredModes.get(id)!;
    } else if (phase === 'expand' && preferredModes.has(id)) {
      // 同一次周期内再次展开（不应常见），沿用已有方向
      mode = preferredModes.get(id)!;
    } else {
      mode = resolveMode(rect, containerRect, 'auto');
      preferredModes.set(id, mode);
    }

    const extendOnGrowth = opts?.extendOnGrowth ?? false;

    stopScroll();

    const existing = animations.get(id);
    animations.set(id, {
      element,
      startTime: performance.now(),
      duration: opts?.duration ?? duration,
      mode,
      initialTop,
      initialBottom,
      extendOnGrowth: extendOnGrowth || existing?.extendOnGrowth || false,
      phase
    });

    if (rafId === null) {
      rafId = requestAnimationFrame(tick);
    }
  }

  function stopAll() {
    if (rafId !== null) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
    animations.clear();
    preferredModes.clear();
  }

  return {
    anchorBlockElement,
    stopAll
  };
}
