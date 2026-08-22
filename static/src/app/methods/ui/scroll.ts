// @ts-nocheck
import { debugLog } from '../common';
import { usePolicyStore } from '../../../stores/policy';
import { useModelStore } from '../../../stores/model';
import { usePersonalizationStore } from '../../../stores/personalization';
import { useTutorialStore } from '../../../stores/tutorial';
import { renderMarkdown as renderMarkdownHelper } from '../../../composables/useMarkdownRenderer';
import { scrollToBottom as scrollToBottomHelper, conditionalScrollToBottom as conditionalScrollToBottomHelper, scrollThinkingToBottom as scrollThinkingToBottomHelper } from '../../../composables/useScrollControl';
import { startResize as startPanelResize, handleResize as handlePanelResize, stopResize as stopPanelResize } from '../../../composables/usePanelResize';
import {
  SUB_AGENT_DONE_PREFIX_RE,
  BG_RUN_COMMAND_DONE_PREFIX_RE,
  userMDebug,
  UI_BOUNCE_TRACE_MAX,
  uiBounceTraceLastTsByKey,
  isUiBounceTraceEnabled,
  uiBounceTrace,
  isConnectionDiagEnabled,
  pushConnectionDiagRecord,
  connectionDiag,
  parseSubAgentDoneLabel,
  parseBackgroundRunCommandDoneLabel,
  parseSystemNoticeLabel,
} from './shared';

export const scrollMethods = {
  clearLocalTaskUiState(reason = 'safe-navigation') {
    // 只清理当前浏览器视图里的运行态/工具态，不取消后端任务。
    // 用于切换对话/工作区后，避免旧任务残留让新视图的发送按钮显示为“停止”。
    try {
      if (typeof this.stopWaitingTaskProbe === 'function') {
        this.stopWaitingTaskProbe();
      }
    } catch (_) {}
    this.streamingMessage = false;
    this.taskInProgress = false;
    this.stopRequested = false;
    this.waitingForSubAgent = false;
    this.waitingForBackgroundCommand = false;
    this.dropToolEvents = false;
    this.runtimeQueuedMessages = [];
    this.runtimeGuidanceFallbackQueue = [];
    this.runtimeQueueAutoSendInProgress = false;
    this.runtimeQueueSyncLockKey = '';
    this.runtimeQueueSyncLockUntil = 0;
    this.goalModeArmed = false;
    this.goalRunning = false;
    this.goalProgress = null;
    this.goalDialogOpen = false;
    if (typeof this.clearRuntimeQueueSuppressionState === 'function') {
      this.clearRuntimeQueueSuppressionState();
    } else {
      this.runtimeQueueSuppressedMessageIds = new Set();
      this.runtimeGuidanceSuppressedTextCounts = {};
    }
    if (typeof this.clearPendingTools === 'function') {
      this.clearPendingTools(reason);
    } else {
      this.preparingTools?.clear?.();
      this.activeTools?.clear?.();
      this.toolActionIndex?.clear?.();
      this.toolStacks?.clear?.();
    }
    if (typeof this.chatClearThinkingLocks === 'function') {
      this.chatClearThinkingLocks();
    }
    this.$forceUpdate?.();
  },
  ensureScrollListener() {
    if (this._scrollListenerReady) {
      return;
    }
    const area = this.getMessagesAreaElement();
    if (!area) {
      return;
    }
    this.initScrollListener();
    this._scrollListenerReady = true;
  },
  handleStickStateChange(payload) {
    const escaped = !!payload?.escapedFromLock;
    const isAtBottom = !!payload?.isAtBottom;
    const isNearBottom = !!payload?.isNearBottom;
    const userEscaped = !!this._escapedByUserScroll;
    uiBounceTrace(
      'stick-state-change',
      {
        isAtBottom,
        isNearBottom,
        escapedFromLock: escaped,
        userEscaped,
        autoScrollEnabled: this.autoScrollEnabled,
        userScrolling: this.userScrolling
      },
      'stick-state-change',
      180
    );
    this.stickIsAtBottom = isAtBottom;
    this.stickIsNearBottom = isNearBottom;

    // 用户主动脱离锁定：只要没真正回到底部，就保持“手动滚动中”状态。
    // 注意不能用 nearBottom（70px 容差）判定“已回底”：移动端浅滚动脱锁常停在
    // 70px 以内，若据此清除脱锁标志会立刻重新上锁，表现为「第一次滚动必被拽回底部」。
    // 另外脱锁意图后的冷却期（_manualScrollSuppressUntil）内禁止重置：移动端触摸滚动
    // 由合成器驱动，scrollTop 更新滞后于状态事件，此时 scrollTop 可能仍贴着底部，
    // 若立即按位置判定“已回底”会误清脱锁标志。
    if (userEscaped) {
      const inIntentCooldown = Date.now() <= (this._manualScrollSuppressUntil || 0);
      const area = this.getMessagesAreaElement();
      const strictAtBottom = area
        ? area.scrollHeight - area.scrollTop - area.clientHeight <= 4
        : isAtBottom;
      if (strictAtBottom && !inIntentCooldown) {
        this._escapedByUserScroll = false;
        this.chatSetScrollState({ userScrolling: false });
      } else {
        this.chatSetScrollState({ userScrolling: true });
      }
      return;
    }

    // 非用户触发的 escaped（例如高度突增导致的短暂锚点漂移）不应解除锁定
    this.chatSetScrollState({ userScrolling: false });
    if (!escaped) {
      return;
    }
    const active = typeof this.isOutputActive === 'function' ? this.isOutputActive() : true;
    if (!this.autoScrollEnabled || !active) {
      return;
    }
    const chatArea = this.getChatAreaController();
    if (chatArea && typeof chatArea.conditionalStickToBottom === 'function') {
      chatArea.conditionalStickToBottom({ force: false });
      return;
    }
    conditionalScrollToBottomHelper(this);
  },
  handleUserScrollIntent(payload) {
    const ts = Number(payload?.ts || Date.now());
    const chatArea = this.getChatAreaController();
    if (chatArea && typeof chatArea.stopStickScroll === 'function') {
      chatArea.stopStickScroll();
    }
    // 用户手动滚动后，短时间内禁止任何自动追底，规避 escapedFromLock 状态抖动竞态
    this._manualScrollSuppressUntil = Math.max(this._manualScrollSuppressUntil || 0, ts + 1600);
    this._escapedByUserScroll = true;
    this.chatSetScrollState({ userScrolling: true });
    uiBounceTrace(
      'ui.user-scroll-intent',
      {
        ts,
        delta: Number(payload?.delta || 0),
        top: Number(payload?.top || 0),
        suppressUntil: this._manualScrollSuppressUntil
      },
      'ui.user-scroll-intent',
      80
    );
  },
  scrollHistoryToBottomInstant() {
    this._escapedByUserScroll = false;
    const chatArea = this.getChatAreaController();
    if (chatArea && typeof chatArea.stopStickScroll === 'function') {
      chatArea.stopStickScroll();
    }
    const area = this.getMessagesAreaElement();
    if (!area) {
      return;
    }
    const jump = () => {
      area.scrollTop = area.scrollHeight;
    };
    jump();
    requestAnimationFrame(() => {
      jump();
      requestAnimationFrame(jump);
    });
    this.chatSetScrollState({ userScrolling: false });
  },
  async settleHistoryRenderAndScroll() {
    this._escapedByUserScroll = false;
    const chatArea = this.getChatAreaController();
    if (chatArea && typeof chatArea.stopStickScroll === 'function') {
      chatArea.stopStickScroll();
    }
    const area = this.getMessagesAreaElement();
    if (!area) {
      return;
    }
    const nextFrame = () => new Promise((resolve) => requestAnimationFrame(resolve));
    const sleep = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));
    const jump = () => {
      area.scrollTop = area.scrollHeight;
    };

    let lastHeight = -1;
    let stableFrames = 0;
    const startedAt = Date.now();
    while (Date.now() - startedAt < 900 && stableFrames < 4) {
      await nextFrame();
      jump();
      const height = area.scrollHeight;
      if (Math.abs(height - lastHeight) <= 1) {
        stableFrames += 1;
      } else {
        stableFrames = 0;
        lastHeight = height;
      }
    }

    // 给 show_html / 表格滚动壳等异步同步 DOM 一次短暂稳定窗口；仍在 historyLoading 隐藏期内完成。
    await sleep(80);
    jump();
    await nextFrame();
    jump();
    this.chatSetScrollState({ userScrolling: false });
  },
  scrollToBottom() {
    uiBounceTrace(
      'ui.scrollToBottom:called',
      {
        autoScrollEnabled: this.autoScrollEnabled,
        userScrolling: this.userScrolling
      },
      'ui.scrollToBottom:called',
      80
    );
    this._escapedByUserScroll = false;
    const chatArea = this.getChatAreaController();
    if (chatArea && typeof chatArea.scrollToBottom === 'function') {
      chatArea.scrollToBottom({
        behavior: 'auto',
        force: this.autoScrollEnabled
      });
      this.chatSetScrollState({ userScrolling: false });
      return;
    }
    scrollToBottomHelper(this);
  },
  conditionalScrollToBottom() {
    const active = typeof this.isOutputActive === 'function' ? this.isOutputActive() : true;
    const chatArea = this.getChatAreaController();
    const stickState =
      chatArea && typeof chatArea.getStickState === 'function' ? chatArea.getStickState() : null;
    uiBounceTrace(
      'ui.conditionalScrollToBottom:called',
      {
        active,
        autoScrollEnabled: this.autoScrollEnabled,
        userScrolling: this.userScrolling,
        stickIsNearBottom: this.stickIsNearBottom,
        escapedFromLock: !!stickState?.escapedFromLock
      },
      'ui.conditionalScrollToBottom:called',
      120
    );
    if (!active) {
      return;
    }
    const now = Date.now();
    if (now <= (this._manualScrollSuppressUntil || 0)) {
      uiBounceTrace(
        'ui.conditionalScrollToBottom:skip-manual-cooldown',
        {
          now,
          suppressUntil: this._manualScrollSuppressUntil
        },
        'ui.conditionalScrollToBottom:skip-manual-cooldown',
        120
      );
      return;
    }
    // 仅在“确认是用户主动脱离锁定”时才阻断自动追底；
    // 对于内容高度突变导致的 escapedFromLock，不应长期卡住锁定。
    if (stickState?.escapedFromLock && this._escapedByUserScroll) {
      uiBounceTrace(
        'ui.conditionalScrollToBottom:skip-escaped-lock',
        {
          escapedFromLock: !!stickState?.escapedFromLock,
          escapedByUser: !!this._escapedByUserScroll,
          isNearBottom: !!stickState?.isNearBottom,
          isAtBottom: !!stickState?.isAtBottom
        },
        'ui.conditionalScrollToBottom:skip-escaped-lock',
        120
      );
      return;
    }
    // 只要用户处于手动滚动状态，就停止自动追底，避免“被弹回”
    // （stickIsNearBottom 在 show_html 动态重排时可能短暂失真，不可作为阻断前置条件）
    if (this.userScrolling) {
      uiBounceTrace(
        'ui.conditionalScrollToBottom:skip-user-scrolling',
        {
          userScrolling: this.userScrolling,
          stickIsNearBottom: this.stickIsNearBottom
        },
        'ui.conditionalScrollToBottom:skip-user-scrolling',
        120
      );
      return;
    }
    if (chatArea && typeof chatArea.conditionalStickToBottom === 'function') {
      chatArea.conditionalStickToBottom({ force: false });
      return;
    }
    conditionalScrollToBottomHelper(this);
  },
  toggleScrollLock() {
    uiBounceTrace(
      'ui.scrollToBottomButton:clicked',
      {
        autoScrollEnabled: this.autoScrollEnabled,
        userScrolling: this.userScrolling,
        stickNearBottom: this.stickIsNearBottom
      },
      'ui.scrollToBottomButton:clicked',
      0
    );
    const chatArea = this.getChatAreaController();
    if (chatArea && typeof chatArea.scrollToBottom === 'function') {
      chatArea.scrollToBottom({ behavior: 'smooth', force: true });
    } else {
      scrollToBottomHelper(this, {
        ignoreUserScrolling: true,
        resetUserScrolling: true,
        behavior: 'smooth',
        force: true
      });
    }
    this._escapedByUserScroll = false;
    this.chatSetScrollState({ autoScrollEnabled: true, userScrolling: false });
    return true;
  },
  scrollThinkingToBottom(blockId) {
    scrollThinkingToBottomHelper(this, blockId);
  },
  initScrollListener() {
    const chatArea = this.getChatAreaController();
    if (
      chatArea &&
      typeof chatArea.isUsingStickToBottom === 'function' &&
      chatArea.isUsingStickToBottom()
    ) {
      this._scrollListenerReady = true;
      return;
    }
    const messagesArea = this.getMessagesAreaElement();
    if (!messagesArea) {
      console.warn('消息区域未找到');
      return;
    }
    this._scrollListenerReady = true;
    let scrollDebugCount = 0;
    const SCROLL_DEBUG_MAX = 1200;
    let scrollDebugLastTs = 0;
    const isScrollDebugEnabled = () => {
      if (typeof window === 'undefined') return false;
      const until = Number((window as any).__SHOW_TAG_DRAWING_UNTIL__ || 0);
      return Date.now() <= until;
    };
    const scrollDebug = (event, payload = {}, throttleMs = 80) => {
      if (!isScrollDebugEnabled()) {
        return;
      }
      if (scrollDebugCount >= SCROLL_DEBUG_MAX) {
        return;
      }
      const now = Date.now();
      if (throttleMs > 0 && now - scrollDebugLastTs < throttleMs) {
        return;
      }
      scrollDebugLastTs = now;
      scrollDebugCount += 1;
      if (scrollDebugCount === SCROLL_DEBUG_MAX) {
        console.warn('[SCROLL_DEBUG]', 'listener:log-limit-reached', { max: SCROLL_DEBUG_MAX });
        return;
      }
      console.log('[SCROLL_DEBUG]', event, payload);
    };
    scrollDebug(
      'listener:setup',
      {
        autoScrollEnabled: this.autoScrollEnabled,
        userScrolling: this.userScrolling
      },
      0
    );

    let isProgrammaticScroll = false;
    const bottomThreshold = 12;

    this._setScrollingFlag = (flag) => {
      isProgrammaticScroll = !!flag;
      scrollDebug('listener:programmatic-flag', { flag: !!flag }, 0);
    };

    messagesArea.addEventListener('scroll', () => {
      if (isProgrammaticScroll) {
        scrollDebug(
          'listener:scroll:ignore-programmatic',
          {
            top: messagesArea.scrollTop,
            height: messagesArea.scrollHeight,
            client: messagesArea.clientHeight
          },
          120
        );
        return;
      }

      const scrollTop = messagesArea.scrollTop;
      const scrollHeight = messagesArea.scrollHeight;
      const clientHeight = messagesArea.clientHeight;
      const isAtBottom = scrollHeight - scrollTop - clientHeight < bottomThreshold;
      const nearBottomThreshold = 48;
      const isNearBottom = scrollHeight - scrollTop - clientHeight < nearBottomThreshold;
      const active = typeof this.isOutputActive === 'function' ? this.isOutputActive() : true;
      if (this.autoScrollEnabled && active) {
        // 锁定模式下不让 userScrolling 状态来回抖动
        this.chatSetScrollState({ userScrolling: false });
        scrollDebug('listener:scroll:locked-keep-bottom-state', {
          top: scrollTop,
          height: scrollHeight,
          client: clientHeight,
          remain: scrollHeight - scrollTop - clientHeight,
          isAtBottom,
          isNearBottom
        });
        return;
      }
      // 仅维护状态，不在 scroll 事件中强制写 scrollTop（避免上下抖动）
      this.chatSetScrollState({ userScrolling: !(isAtBottom || isNearBottom) });
      scrollDebug('listener:scroll:update-state', {
        top: scrollTop,
        height: scrollHeight,
        client: clientHeight,
        remain: scrollHeight - scrollTop - clientHeight,
        isAtBottom,
        isNearBottom,
        autoScrollEnabled: this.autoScrollEnabled,
        userScrollingBefore: this.userScrolling,
        userScrollingNext: !(isAtBottom || isNearBottom)
      });
    });
  },
  handleThinkingScroll(blockId, event) {
    if (!blockId || !event || !event.target) {
      return;
    }
    const el = event.target;
    const threshold = 12;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
    this.chatSetThinkingLock(blockId, atBottom);
  },
  toggleBlock(blockId) {
    if (!blockId) {
      return;
    }
    if (this.expandedBlocks && this.expandedBlocks.has(blockId)) {
      this.chatCollapseBlock(blockId);
    } else {
      this.chatExpandBlock(blockId);
    }
  }
};
