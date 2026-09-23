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
  // 模型菜单分页切换时高度动画：grid 同格堆叠容器的高度由当前页决定，
  // 常规模型页与 Codex 子页项数不同，切换时高度突变——记录切换前高度，
  // DOM 更新后量新页高度，inline height 过渡（220ms，与 slide 动画同步）。
  // 清理必须等 leave 页真正移除（模板 @after-leave 调 clearModelMenuPaneHeight）：
  // 过早清 inline 时 leave 页仍在 DOM，行高 = max(两页) 会瞬间弹回旧高度——
  // 高→矮切换时这就是用户看到的「弹一下/闪一下」（矮→高因行高本就被新页
  // 撑到目标值而无感，方向不对称的根因）。此 timeout 仅为事件丢失的保底。
  headerModelMenuPage() {
    const panes = Array.from(document.querySelectorAll('.model-menu-panes')).filter(
      (el) => el instanceof HTMLElement && el.getClientRects().length > 0
    );
    if (!panes.length) return;
    const startHeights = panes.map((el) => el.offsetHeight);
    this.$nextTick(() => {
      panes.forEach((el, i) => {
        const startHeight = startHeights[i];
        const target = el.querySelector('.model-menu-pane:not([class*="-leave-active"])');
        const endHeight = target ? target.offsetHeight : startHeight;
        if (Math.abs(endHeight - startHeight) < 2) return;
        el.style.height = `${startHeight}px`;
        el.style.transition = 'none';
        void el.offsetHeight; // 强制 reflow 让起始值生效
        el.style.transition = 'height 220ms ease';
        el.style.height = `${endHeight}px`;
        // 序号防快速来回切换时旧 timeout 误清新一轮动画的 inline 样式；
        // 1000ms 是保底（正常路径由 @after-leave 立即清理并作废本 timeout）
        const seq = (el.__heightAnimSeq = (el.__heightAnimSeq || 0) + 1);
        window.setTimeout(() => {
          if (el.__heightAnimSeq !== seq) return;
          el.style.transition = '';
          el.style.height = '';
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
