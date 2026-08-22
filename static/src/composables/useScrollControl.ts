// 负责聊天区域滚动控制的通用方法，解耦 App.vue 中的 DOM 操作

type ScrollContext = {
  getMessagesAreaElement?: () => HTMLElement | null;
  getThinkingContentElement?: (blockId: string) => HTMLElement | null;
  chatToggleScrollLockState?: () => boolean;
  chatSetScrollState?: (payload: { autoScrollEnabled?: boolean; userScrolling?: boolean }) => void;
  thinkingScrollLocks?: Map<string, boolean>;
  _setScrollingFlag?: (value: boolean) => void;
  autoScrollEnabled?: boolean;
  userScrolling?: boolean;
  isOutputActive?: () => boolean;
  streamingMessage?: boolean;
  hasPendingToolActions?: () => boolean;
};

type ScrollOptions = {
  ignoreUserScrolling?: boolean;
  force?: boolean;
  /**
   * When true, force userScrolling=false after the programmatic scroll so that
   * later自动滚动不会被“用户滚动中”状态卡住。
   */
  resetUserScrolling?: boolean;
  /**
   * Control scroll behavior; 'smooth' 用于点击滚动锁按钮时的动画滚动。
   */
  behavior?: ScrollBehavior;
};

type PendingScrollTask = {
  rafId: number | null;
  settleTimer: ReturnType<typeof setTimeout> | null;
};

const pendingScrollTasks = new WeakMap<HTMLElement, PendingScrollTask>();
let autoScrollRafId: number | null = null;
const lastAutoFollowTsByArea = new WeakMap<HTMLElement, number>();
let lastKnownMessagesArea: HTMLElement | null = null;
let scrollDebugCount = 0;
const SCROLL_DEBUG_MAX = 1200;
const scrollDebugLastTsByKey = new Map<string, number>();
let showHtmlTraceCount = 0;
const SHOW_HTML_TRACE_MAX = 160;
const showHtmlTraceLastTsByKey = new Map<string, number>();

function isShowTagDrawingActive() {
  if (typeof window === 'undefined') return false;
  try {
    const until = Number((window as any).__SHOW_TAG_DRAWING_UNTIL__ || 0);
    return Date.now() <= until;
  } catch {
    return false;
  }
}

function isScrollDebugEnabled() {
  if (typeof window === 'undefined') return false;
  try {
    const explicit = (window as any).__SCROLL_DEBUG__;
    if (explicit === false || explicit === '0') return false;
    if (explicit === true || explicit === '1') {
      const until = Number((window as any).__SHOW_TAG_DRAWING_UNTIL__ || 0);
      return Date.now() <= until;
    }
    const localFlag = window.localStorage?.getItem('scrollDebug');
    if (localFlag === '0' || localFlag === 'false') return false;
    if (localFlag === '1' || localFlag === 'true') {
      const until = Number((window as any).__SHOW_TAG_DRAWING_UNTIL__ || 0);
      return Date.now() <= until;
    }
    const until = Number((window as any).__SHOW_TAG_DRAWING_UNTIL__ || 0);
    return Date.now() <= until;
  } catch {
    return false;
  }
}

function isShowHtmlScrollTraceEnabled() {
  // 默认关闭：排障时通过 window.__SHOW_HTML_SCROLL_TRACE__ = true 或 localStorage.showHtmlScrollTrace = '1' 显式打开
  if (typeof window === 'undefined') return false;
  try {
    const explicit = (window as any).__SHOW_HTML_SCROLL_TRACE__;
    if (explicit === false || explicit === '0') return false;
    if (explicit === true || explicit === '1') return true;
    const localFlag = window.localStorage?.getItem('showHtmlScrollTrace');
    if (localFlag === '0' || localFlag === 'false') return false;
    if (localFlag === '1' || localFlag === 'true') return true;
    return false;
  } catch {
    return false;
  }
}

function getMessagesAreaMetrics(messagesArea: HTMLElement) {
  const top = messagesArea.scrollTop;
  const height = messagesArea.scrollHeight;
  const client = messagesArea.clientHeight;
  const maxTop = Math.max(0, height - client);
  const remain = height - top - client;
  return { top, height, client, maxTop, remain };
}

function getLastShowHtmlMetrics(messagesArea: HTMLElement) {
  const list = messagesArea.querySelectorAll('.chat-inline-card--html, .chat-inline-html');
  const last = (list[list.length - 1] as HTMLElement) || null;
  if (!last) return null;
  const areaRect = messagesArea.getBoundingClientRect();
  const rect = last.getBoundingClientRect();
  return {
    count: list.length,
    y: Math.round(rect.y),
    h: Math.round(rect.height),
    bottomInArea: Math.round(rect.bottom - areaRect.top),
    areaClient: Math.round(messagesArea.clientHeight),
    belowViewportPx: Math.round(Math.max(0, rect.bottom - areaRect.bottom))
  };
}

function hasShowHtmlNodeInArea(messagesArea: HTMLElement) {
  return !!messagesArea.querySelector(
    'show_html, show-html, .chat-inline-html, .chat-inline-card--html'
  );
}

function showHtmlTraceLog(
  event: string,
  payload: Record<string, any> = {},
  key = event,
  throttleMs = 280
) {
  if (!isShowHtmlScrollTraceEnabled()) return;
  if (showHtmlTraceCount >= SHOW_HTML_TRACE_MAX) return;
  const now = Date.now();
  const last = showHtmlTraceLastTsByKey.get(key) || 0;
  if (throttleMs > 0 && now - last < throttleMs) return;
  showHtmlTraceLastTsByKey.set(key, now);
  showHtmlTraceCount += 1;
  if (showHtmlTraceCount === SHOW_HTML_TRACE_MAX) {
    console.warn('[SHOW_HTML_SCROLL_TRACE]', 'log-limit-reached', { max: SHOW_HTML_TRACE_MAX });
    return;
  }
  console.log('[SHOW_HTML_SCROLL_TRACE]', event, payload);
}

function scrollDebugLog(
  event: string,
  payload: Record<string, any> = {},
  key = event,
  throttleMs = 120
) {
  if (!isScrollDebugEnabled()) return;
  if (scrollDebugCount >= SCROLL_DEBUG_MAX) return;
  const now = Date.now();
  const last = scrollDebugLastTsByKey.get(key) || 0;
  if (throttleMs > 0 && now - last < throttleMs) return;
  scrollDebugLastTsByKey.set(key, now);
  scrollDebugCount += 1;
  if (scrollDebugCount === SCROLL_DEBUG_MAX) {
    console.warn('[SCROLL_DEBUG]', 'log-limit-reached', { max: SCROLL_DEBUG_MAX });
    return;
  }
  console.log('[SCROLL_DEBUG]', event, payload);
}

export function scrollToBottom(ctx: ScrollContext, options?: ScrollOptions) {
  const messagesArea = ctx.getMessagesAreaElement?.();
  if (!messagesArea) {
    scrollDebugLog('scrollToBottom:skip:no-area');
    return;
  }
  const drawingActive = isShowTagDrawingActive() && hasShowHtmlNodeInArea(messagesArea);
  const hasOverflow = messagesArea.scrollHeight > messagesArea.clientHeight + 2;
  if (!hasOverflow && !options?.force && !drawingActive) {
    scrollDebugLog(
      'scrollToBottom:skip:no-overflow',
      {
        top: messagesArea.scrollTop,
        height: messagesArea.scrollHeight,
        client: messagesArea.clientHeight,
        drawingActive
      },
      'scrollToBottom:skip:no-overflow',
      120
    );
    if (typeof ctx._setScrollingFlag === 'function') {
      ctx._setScrollingFlag(false);
    }
    if (options?.resetUserScrolling) {
      ctx.chatSetScrollState?.({ userScrolling: false });
    }
    return;
  }
  const traceId = `t${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
  const beforeMetrics = getMessagesAreaMetrics(messagesArea);
  if (drawingActive || options?.force) {
    showHtmlTraceLog(
      'scrollToBottom:start',
      {
        traceId,
        drawingActive,
        force: !!options?.force,
        ...beforeMetrics,
        lastShowHtml: getLastShowHtmlMetrics(messagesArea)
      },
      'scrollToBottom:start',
      120
    );
  }

  const useSmooth =
    options?.behavior === 'smooth' && typeof (messagesArea as HTMLElement).scrollTo === 'function';
  scrollDebugLog(
    'scrollToBottom:start',
    {
      traceId,
      useSmooth,
      ignoreUserScrolling: !!options?.ignoreUserScrolling,
      resetUserScrolling: !!options?.resetUserScrolling,
      autoScrollEnabled: ctx.autoScrollEnabled,
      userScrolling: ctx.userScrolling,
      drawingActive,
      top: messagesArea.scrollTop,
      height: messagesArea.scrollHeight,
      client: messagesArea.clientHeight
    },
    `scrollToBottom:start:${traceId}`,
    0
  );

  let task = pendingScrollTasks.get(messagesArea);
  if (!task) {
    task = { rafId: null, settleTimer: null };
    pendingScrollTasks.set(messagesArea, task);
  }
  if (task.rafId !== null) {
    cancelAnimationFrame(task.rafId);
    task.rafId = null;
  }
  if (task.settleTimer) {
    clearTimeout(task.settleTimer);
    task.settleTimer = null;
  }

  const perform = () => {
    if (ctx.userScrolling && !options?.ignoreUserScrolling && !options?.force) {
      scrollDebugLog(
        'scrollToBottom:skip:user-scrolling',
        { traceId, userScrolling: ctx.userScrolling },
        'scrollToBottom:skip:user-scrolling',
        0
      );
      if (typeof ctx._setScrollingFlag === 'function') {
        ctx._setScrollingFlag(false);
      }
      return;
    }

    if (useSmooth) {
      (messagesArea as HTMLElement).scrollTo({
        top:
          messagesArea.scrollHeight +
          (drawingActive ? Math.max(24, Math.floor(messagesArea.clientHeight * 0.12)) : 0),
        behavior: 'smooth'
      });
    } else {
      messagesArea.scrollTop =
        messagesArea.scrollHeight +
        (drawingActive ? Math.max(24, Math.floor(messagesArea.clientHeight * 0.12)) : 0);
    }
    if (drawingActive || options?.force) {
      showHtmlTraceLog(
        'scrollToBottom:after-write',
        {
          traceId,
          drawingActive,
          ...getMessagesAreaMetrics(messagesArea),
          lastShowHtml: getLastShowHtmlMetrics(messagesArea)
        },
        'scrollToBottom:after-write',
        120
      );
    }

    let settleCount = 0;
    let lastHeight = -1;
    const settleStartTs = Date.now();
    const settleMaxCount = drawingActive ? 30 : 12;
    const settleMaxDurationMs = drawingActive ? 3200 : 1200;
    const settleScroll = () => {
      settleCount += 1;
      messagesArea.scrollTop =
        messagesArea.scrollHeight +
        (drawingActive ? Math.max(24, Math.floor(messagesArea.clientHeight * 0.12)) : 0);
      const currentHeight = messagesArea.scrollHeight;
      const remain = currentHeight - messagesArea.scrollTop - messagesArea.clientHeight;
      const heightStable = Math.abs(currentHeight - lastHeight) <= 2;
      const atBottom = Math.abs(remain) <= 2;
      const settleElapsed = Date.now() - settleStartTs;
      const shouldContinue =
        !atBottom && settleCount < settleMaxCount && settleElapsed < settleMaxDurationMs;
      scrollDebugLog(
        'scrollToBottom:settle',
        {
          traceId,
          settleCount,
          settleElapsed,
          currentHeight,
          lastHeight,
          remain,
          atBottom,
          heightStable,
          top: messagesArea.scrollTop,
          client: messagesArea.clientHeight
        },
        'scrollToBottom:settle',
        80
      );
      lastHeight = currentHeight;
      if (shouldContinue) {
        if (
          (drawingActive || options?.force) &&
          (settleCount === 1 || settleCount === 4 || settleCount === 6)
        ) {
          showHtmlTraceLog(
            'scrollToBottom:settle-progress',
            {
              traceId,
              settleCount,
              drawingActive,
              currentHeight,
              top: messagesArea.scrollTop,
              client: messagesArea.clientHeight,
              remain
            },
            'scrollToBottom:settle-progress',
            140
          );
        }
        task!.settleTimer = setTimeout(settleScroll, 80);
        return;
      }
      if (typeof ctx._setScrollingFlag === 'function') {
        ctx._setScrollingFlag(false);
      }
      if (options?.resetUserScrolling) {
        if (typeof ctx.chatSetScrollState === 'function') {
          ctx.chatSetScrollState({ userScrolling: false });
        } else if ('userScrolling' in ctx) {
          ctx.userScrolling = false;
        }
      }
      task!.settleTimer = null;
      if (drawingActive || options?.force || Math.abs(remain) > 2) {
        showHtmlTraceLog(
          'scrollToBottom:done',
          {
            traceId,
            settleCount,
            drawingActive,
            ...getMessagesAreaMetrics(messagesArea),
            lastShowHtml: getLastShowHtmlMetrics(messagesArea)
          },
          'scrollToBottom:done',
          120
        );
      }
      scrollDebugLog(
        'scrollToBottom:done',
        {
          traceId,
          settleCount,
          top: messagesArea.scrollTop,
          height: messagesArea.scrollHeight,
          client: messagesArea.clientHeight
        },
        `scrollToBottom:done:${traceId}`,
        0
      );
    };
    task!.settleTimer = setTimeout(settleScroll, 120);
  };

  if (typeof ctx._setScrollingFlag === 'function') {
    ctx._setScrollingFlag(true);
  }

  task.rafId = requestAnimationFrame(() => {
    task!.rafId = null;
    scrollDebugLog('scrollToBottom:raf', { traceId }, `scrollToBottom:raf:${traceId}`, 0);
    perform();
    if (!task!.settleTimer && typeof ctx._setScrollingFlag === 'function') {
      ctx._setScrollingFlag(false);
    }
  });
}

export function conditionalScrollToBottom(ctx: ScrollContext) {
  const active = typeof ctx.isOutputActive === 'function' ? ctx.isOutputActive() : true;
  const messagesArea = ctx.getMessagesAreaElement?.();
  const drawingActive =
    !!messagesArea && isShowTagDrawingActive() && hasShowHtmlNodeInArea(messagesArea);
  if (!drawingActive && isShowTagDrawingActive() && messagesArea) {
    showHtmlTraceLog(
      'conditional:skip-stale-drawing-window',
      {
        ...getMessagesAreaMetrics(messagesArea),
        lastShowHtml: getLastShowHtmlMetrics(messagesArea)
      },
      'conditional:skip-stale-drawing-window',
      260
    );
  }
  scrollDebugLog(
    'conditional:start',
    {
      active,
      autoScrollEnabled: ctx.autoScrollEnabled,
      userScrolling: ctx.userScrolling,
      drawingActive
    },
    'conditional:start',
    120
  );
  if (ctx.autoScrollEnabled === true && active) {
    if (!messagesArea) {
      scrollDebugLog('conditional:skip:no-area');
      return;
    }
    if (lastKnownMessagesArea && lastKnownMessagesArea !== messagesArea) {
      scrollDebugLog('conditional:area-replaced', {}, 'conditional:area-replaced', 0);
      const lastTask = pendingScrollTasks.get(lastKnownMessagesArea);
      if (lastTask && lastTask.rafId !== null) {
        cancelAnimationFrame(lastTask.rafId);
        lastTask.rafId = null;
      }
      if (lastTask && lastTask.settleTimer) {
        clearTimeout(lastTask.settleTimer);
        lastTask.settleTimer = null;
      }
    }
    lastKnownMessagesArea = messagesArea;
    const top = messagesArea.scrollTop;
    const height = messagesArea.scrollHeight;
    const client = messagesArea.clientHeight;
    const remain = height - top - client;
    // 高度尚未溢出时，跳过跟底，避免“0高度/刚重建”阶段反复触发滚动链路
    if (height <= client + 2 && !drawingActive) {
      ctx.chatSetScrollState?.({ userScrolling: false });
      scrollDebugLog(
        'conditional:skip:no-overflow',
        { top, height, client, remain },
        'conditional:skip:no-overflow',
        120
      );
      return;
    }
    if (height <= client + 2 && drawingActive) {
      showHtmlTraceLog(
        'conditional:drawing-no-overflow-but-continue',
        { top, height, client, remain, drawingActive },
        'conditional:drawing-no-overflow-but-continue',
        200
      );
    }
    const pending = pendingScrollTasks.get(messagesArea);
    if (pending?.rafId !== null || !!pending?.settleTimer) {
      scrollDebugLog(
        'conditional:skip:pending-task',
        { hasRaf: pending?.rafId !== null, hasSettle: !!pending?.settleTimer, top, height, client },
        'conditional:skip:pending-task',
        100
      );
      return;
    }
    const now = Date.now();
    const lastTs = lastAutoFollowTsByArea.get(messagesArea) || 0;
    if (now - lastTs < 90) {
      scrollDebugLog(
        'conditional:skip:too-frequent',
        { gap: now - lastTs, top, height, client },
        'conditional:skip:too-frequent',
        100
      );
      return;
    }
    lastAutoFollowTsByArea.set(messagesArea, now);
    // 锁定模式下（autoScrollEnabled=true）始终跟随到底，避免 streaming 期间状态抖动导致“忽上忽下”
    if (ctx.userScrolling === true) {
      ctx.chatSetScrollState?.({ userScrolling: false });
    }
    if (autoScrollRafId !== null) {
      cancelAnimationFrame(autoScrollRafId);
    }
    autoScrollRafId = requestAnimationFrame(() => {
      autoScrollRafId = null;
      const latestArea = ctx.getMessagesAreaElement?.();
      if (!latestArea || latestArea !== messagesArea) {
        scrollDebugLog(
          'conditional:skip:area-changed-before-raf',
          {},
          'conditional:skip:area-changed-before-raf',
          0
        );
        return;
      }
      const latestHeight = latestArea.scrollHeight;
      const latestClient = latestArea.clientHeight;
      const latestDrawingActive = isShowTagDrawingActive() && hasShowHtmlNodeInArea(latestArea);
      if (latestHeight <= latestClient + 2 && !latestDrawingActive) {
        scrollDebugLog(
          'conditional:skip:no-overflow-before-raf',
          { top: latestArea.scrollTop, height: latestHeight, client: latestClient },
          'conditional:skip:no-overflow-before-raf',
          100
        );
        return;
      }
      if (latestDrawingActive) {
        showHtmlTraceLog(
          'conditional:raf-scroll',
          {
            top: latestArea.scrollTop,
            height: latestHeight,
            client: latestClient,
            remain: latestHeight - latestArea.scrollTop - latestClient,
            lastShowHtml: getLastShowHtmlMetrics(latestArea)
          },
          'conditional:raf-scroll',
          140
        );
      }
      scrollDebugLog(
        'conditional:raf-scroll',
        {
          top: latestArea.scrollTop,
          height: latestHeight,
          client: latestClient
        },
        'conditional:raf-scroll',
        80
      );
      scrollToBottom(ctx, {
        ignoreUserScrolling: true,
        resetUserScrolling: true,
        force: latestDrawingActive
      });
    });
  }
}

export function toggleScrollLock(ctx: ScrollContext) {
  const active = typeof ctx.isOutputActive === 'function' ? ctx.isOutputActive() : true;
  scrollDebugLog(
    'toggle:start',
    {
      active,
      autoScrollEnabled: ctx.autoScrollEnabled,
      userScrolling: ctx.userScrolling
    },
    'toggle:start',
    0
  );

  // 没有模型输出时：允许点击，但不切换锁定，仅单次滚动到底部
  if (!active) {
    scrollToBottom(ctx, {
      ignoreUserScrolling: true,
      resetUserScrolling: true,
      behavior: 'smooth',
      force: true
    });
    return ctx.autoScrollEnabled ?? false;
  }

  const nextState = ctx.chatToggleScrollLockState?.() ?? false;
  if (nextState) {
    scrollToBottom(ctx, {
      ignoreUserScrolling: true,
      resetUserScrolling: true,
      force: true
    });
  }
  scrollDebugLog(
    'toggle:end',
    {
      nextState,
      autoScrollEnabled: ctx.autoScrollEnabled,
      userScrolling: ctx.userScrolling
    },
    'toggle:end',
    0
  );
  return nextState;
}

/**
 * 在页面初始化/刷新后同步滚动锁定的默认状态：
 * 始终保持锁定状态，不再根据输出状态自动解锁。
 */
export function normalizeScrollLock(ctx: ScrollContext) {
  // 确保滚动锁定始终启用
  if (!ctx.autoScrollEnabled) {
    ctx.chatSetScrollState?.({ autoScrollEnabled: true, userScrolling: false });
  }
}

export function scrollThinkingToBottom(ctx: ScrollContext, blockId: string) {
  if (!blockId) {
    return;
  }
  if (!ctx.thinkingScrollLocks?.get(blockId)) {
    return;
  }
  const el = ctx.getThinkingContentElement?.(blockId);
  if (el) {
    el.scrollTop = el.scrollHeight;
  }
}
