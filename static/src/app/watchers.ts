// @ts-nocheck
import { debugLog, traceLog } from './methods/common';
import { useConversationStore } from '../stores/conversation';
import { useQuickDockStore } from '../stores/quickDock';
import { useFileStore } from '../stores/file';

export const watchers = {
  inputMessage() {
    this.autoResizeInput();
    if (typeof this.scheduleComposerDraftPersist === 'function') {
      this.scheduleComposerDraftPersist('watch-input-message');
    }
  },
  // 模型菜单分页切换的高度动画（2026-09 重写：WAAPI 直接驱动弹窗本体）。
  // 旧实现给 .model-menu-panes 容器设 inline height 过渡——弹窗是多列 grid，
  // 渲染高度 = max(各列)，容器动画不必然传导到弹窗可视边框（曾出现容器过渡
  // 完整播放、弹窗却方向不对称跳变的问题）。现直接测量弹窗渲染高度并用
  // WAAPI 插值：切换期间弹窗钉在动画值，overflow 裁切堆叠页溢出内容；
  // after-leave（旧页移除、auto 高度稳定）后 cancel 动画回落 auto，零跳变。
  headerModelMenuPage() {
    const panes = Array.from(document.querySelectorAll('.model-menu-panes')).filter(
      (el) => el instanceof HTMLElement && el.getClientRects().length > 0
    );
    if (!panes.length) return;
    const popups = panes.map((el) => el.closest('.model-mode-dropdown'));
    if (popups.some((p) => !(p instanceof HTMLElement))) return;
    // 起始高度在 DOM 更新前量（watcher 默认 pre flush），此时只有旧页；
    // 若上一轮动画 fill:forwards 未清理，offsetHeight 反映动画当前值，
    // 恰好是新动画的正确起点
    const startHeights = popups.map((p) => p.offsetHeight);
    this.$nextTick(() => {
      panes.forEach((el, i) => {
        const popup = popups[i];
        const startHeight = startHeights[i];
        // 终止上一轮未完的动画（快速来回切换），让真实 auto 高度参与目标测量
        if (popup.__menuHeightAnim) {
          popup.__menuHeightAnim.cancel();
          popup.__menuHeightAnim = null;
        }
        // 量「仅新页」时弹窗的稳定高度：leave 页临时脱离 grid 流，同帧测量后
        // 恢复（同步代码不触发渲染，无闪烁；leave 页 transform 过渡由类驱动，
        // position 还原后不受影响）
        const leavePane = el.querySelector('.model-menu-pane[class*="-leave-"]');
        if (leavePane instanceof HTMLElement) {
          leavePane.style.position = 'absolute';
        }
        const endHeight = popup.offsetHeight;
        if (leavePane instanceof HTMLElement) {
          leavePane.style.position = '';
        }
        if (Math.abs(endHeight - startHeight) < 2) return;
        popup.style.overflow = 'hidden'; // 动画期间裁切堆叠页溢出部分
        const anim = popup.animate(
          [{ height: `${startHeight}px` }, { height: `${endHeight}px` }],
          { duration: 220, easing: 'ease', fill: 'forwards' }
        );
        popup.__menuHeightAnim = anim;
        // 保底：after-leave 事件丢失时 1s 后强制回落 auto（序号防快速切换时
        // 旧 timeout 误清新一轮动画）
        const seq = (popup.__menuHeightAnimSeq = (popup.__menuHeightAnimSeq || 0) + 1);
        window.setTimeout(() => {
          if (popup.__menuHeightAnimSeq !== seq) return;
          if (popup.__menuHeightAnim) {
            popup.__menuHeightAnim.cancel();
            popup.__menuHeightAnim = null;
          }
          popup.style.overflow = '';
        }, 1000);
      });
    });
  },
  messages: {
    deep: true,
    handler() {
      this.refreshBlankHeroState();
    }
  },
  composerBusy(newValue, oldValue) {
    if (oldValue && !newValue && typeof this.tryAutoSendRuntimeQueuedMessages === 'function') {
      this.tryAutoSendRuntimeQueuedMessages('watch-composer-idle');
    }
  },
  runtimeQueuedMessages: {
    deep: true,
    handler(list) {
      if (
        Array.isArray(list) &&
        list.length > 0 &&
        !this.composerBusy &&
        typeof this.tryAutoSendRuntimeQueuedMessages === 'function'
      ) {
        this.tryAutoSendRuntimeQueuedMessages('watch-runtime-queue');
      }
    }
  },
  runtimeGuidanceFallbackQueue: {
    deep: true,
    handler(list) {
      if (
        Array.isArray(list) &&
        list.length > 0 &&
        !this.composerBusy &&
        typeof this.tryAutoSendRuntimeQueuedMessages === 'function'
      ) {
        this.tryAutoSendRuntimeQueuedMessages('watch-runtime-guidance-fallback');
      }
    }
  },
  currentConversationTitle(newVal, oldVal) {
    const target = (newVal && newVal.trim()) || '';
    if (this.suppressTitleTyping) {
      this.titleTypingText = target;
      this.titleTypingTarget = target;
      return;
    }
    const previous =
      (oldVal && oldVal.trim()) || (this.titleTypingText && this.titleTypingText.trim()) || '';
    // 默认标题双语判等：zh '新对话' / en 'New Chat'（后端 modules/i18n.py conversation.default_title；\u 转义仅过审计）
    const isPlaceholderTitle = (v: string) => v === '\u65b0\u5bf9\u8bdd' || v === 'New Chat';
    const placeholderPrev = !previous || isPlaceholderTitle(previous);
    const placeholderTarget = !target || isPlaceholderTitle(target);
    const animate = placeholderPrev && !placeholderTarget; // 仅从空/占位切换到真实标题时动画
    this.startTitleTyping(target, { animate });
  },
  currentConversationId: {
    immediate: false,
    handler(newValue, oldValue) {
      // 【合并自原同名函数 watcher】对象字面量中两个 currentConversationId
      // 键会互相覆盖（后者覆盖前者），原函数版 watcher 从未生效，导致
      // /new 页面残留上一对话的快捷窗口待办。逻辑合并到此处统一执行。
      if (newValue !== oldValue) {
        // 同步到 conversationStore（QuickDock 等组件监听 store 侧的 id）
        useConversationStore().setCurrentConversationId(newValue || null);
        if (!newValue) {
          // 空对话态：对话类型复位（进入对话时由 enterConversation 从 metadata 落地）
          this.currentConversationType = null;
          useConversationStore().$patch({ multiAgentMode: false });
        }
        const quickDock = useQuickDockStore();
        // 关闭详情/预览/菜单等瞬态（必须立即）
        quickDock.resetTransient();
        if (!newValue) {
          // /new 等无对话场景：不会有 bootstrap 回填，立即清空让列折叠
          quickDock.setEditedFiles([]);
          useFileStore().setTodoList(null);
        }
        // 切到另一对话：不清列表 —— 旧内容短暂保留，由 bootstrap/fetch 回填自然覆盖；
        // 新对话无内容时窗口在回填后才消失，避免列「先收起再展开」的闪烁。
      }
      debugLog('currentConversationId 变化', {
        oldValue,
        newValue,
        skipConversationHistoryReload: this.skipConversationHistoryReload
      });
      traceLog('watch:currentConversationId', {
        oldValue,
        newValue,
        skipConversationHistoryReload: this.skipConversationHistoryReload,
        historyLoading: this.historyLoading,
        historyLoadingFor: this.historyLoadingFor,
        historyLoadSeq: this.historyLoadSeq
      });
      this.refreshBlankHeroState();
      this.logMessageState('watch:currentConversationId', {
        oldValue,
        newValue,
        skipConversationHistoryReload: this.skipConversationHistoryReload
      });
      if (
        oldValue !== newValue &&
        !this.taskInProgress &&
        !this.composerBusy &&
        typeof this.restoreComposerDraftState === 'function'
      ) {
        this.restoreComposerDraftState(
          `watch-conversation-id:${oldValue || 'none'}->${newValue || 'none'}`
        );
      }
      if (!newValue || typeof newValue !== 'string' || newValue.startsWith('temp_')) {
        this.versioningEnabled = false;
        this.versioningTrackingMode = 'conversation_only';
        return;
      }
      this.fetchVersioningStatus(newValue, { silent: true });
      if (this.skipConversationHistoryReload) {
        this.skipConversationHistoryReload = false;
        return;
      }
      if (oldValue && newValue === oldValue) {
        return;
      }
      this.fetchAndDisplayHistory();
      this.fetchConversationTokenStatistics();
      this.updateCurrentContextTokens();
    }
  },
  fileTree: {
    immediate: true,
    handler(newValue) {
      this.monitorSyncDesktop(newValue);
    }
  }
};
