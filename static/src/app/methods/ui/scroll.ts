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
    const follow = payload?.followState === 'escaped' ? 'escaped' : 'locked';
    uiBounceTrace(
      'stick-state-change',
      {
        isAtBottom: !!payload?.isAtBottom,
        isNearBottom: !!payload?.isNearBottom,
        escapedFromLock: !!payload?.escapedFromLock,
        followState: follow,
        autoScrollEnabled: this.autoScrollEnabled,
        userScrolling: this.userScrolling
      },
      'stick-state-change',
      180
    );
    this.stickIsAtBottom = !!payload?.isAtBottom;
    this.stickIsNearBottom = !!payload?.isNearBottom;
    // 脱锁/回锁裁决已收敛到 ChatArea 的 followState 权威（可信用户输入驱动）；
    // 库层 escapedFromLock / nearBottom 不再反向驱动锁定决策，这里只做展示态同步。
    // escaped 即视为「用户滚动中」，供条件追底阻断与旧版 fallback 路径使用。
    this.chatSetScrollState({ userScrolling: follow === 'escaped' });
  },
  handleUserScrollIntent(payload) {
    const ts = Number(payload?.ts || Date.now());
    const chatArea = this.getChatAreaController();
    if (chatArea && typeof chatArea.stopStickScroll === 'function') {
      chatArea.stopStickScroll();
    }
    // 脱锁状态由 ChatArea followState 权威持有（其内部输入监听已先行置 escaped），
    // 这里仅同步展示态。不再设冷却期——escaped 不因时间流逝自动恢复，只由触底回锁。
    this.chatSetScrollState({ userScrolling: true });
    uiBounceTrace(
      'ui.user-scroll-intent',
      {
        ts,
        delta: Number(payload?.delta || 0),
        top: Number(payload?.top || 0)
      },
      'ui.user-scroll-intent',
      80
    );
  },
  scrollHistoryToBottomInstant() {
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
    // 历史落定 = 明确回锁
    if (chatArea && typeof chatArea.scrollToBottom === 'function') {
      chatArea.scrollToBottom({ behavior: 'auto', force: true });
    }
    this.chatSetScrollState({ userScrolling: false });
  },
  async settleHistoryRenderAndScroll() {
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
    // 历史落定 = 明确回锁
    if (chatArea && typeof chatArea.scrollToBottom === 'function') {
      chatArea.scrollToBottom({ behavior: 'auto', force: true });
    }
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
    const chatArea = this.getChatAreaController();
    if (chatArea && typeof chatArea.scrollToBottom === 'function') {
      // 主动滚到底 = 明确回锁意图（发送消息等场景），一律强制
      chatArea.scrollToBottom({
        behavior: 'auto',
        force: true
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
    // 脱锁期间一律不自动追底（唯一权威：ChatArea followState）；
    // 旧实现依赖 escapedFromLock + _escapedByUserScroll + 冷却期的三重猜测，
    // 库「任意下滚即清锁」会让阻断失效，表现为微滚向下被瞬间拽回底部。
    if (stickState?.followState === 'escaped') {
      uiBounceTrace(
        'ui.conditionalScrollToBottom:skip-escaped',
        { followState: stickState.followState },
        'ui.conditionalScrollToBottom:skip-escaped',
        120
      );
      return;
    }
    // 旧版 fallback 路径（非 stick 引擎）仍尊重 userScrolling
    if (!stickState && this.userScrolling) {
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
