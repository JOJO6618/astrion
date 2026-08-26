// @ts-nocheck
import { mapState, mapWritableState } from 'pinia';
import { useConnectionStore } from '../stores/connection';
import { useFileStore } from '../stores/file';
import { useUiStore } from '../stores/ui';
import { useConversationStore } from '../stores/conversation';
import { useModelStore } from '../stores/model';
import { useChatStore } from '../stores/chat';
import { useInputStore } from '../stores/input';
import { useToolStore } from '../stores/tool';
import { useResourceStore } from '../stores/resource';
import { useFocusStore } from '../stores/focus';
import { useUploadStore } from '../stores/upload';
import { useMonitorStore } from '../stores/monitor';
import { usePolicyStore } from '../stores/policy';
import { useSubAgentStore } from '../stores/subAgent';
import { useBackgroundCommandStore } from '../stores/backgroundCommand';
import { usePersonalizationStore } from '../stores/personalization';
import { useQuickDockStore } from '../stores/quickDock';
import { getToolStatusText, getToolDescription } from '../utils/chatDisplay';
import { toolFaceKey } from '../utils/avatarFace';

// 取最后一段连续的 tool actions（对应模型一次并行调用的一批工具）
function getLatestToolSegment(actions) {
  if (!Array.isArray(actions)) return [];
  let last = -1;
  for (let i = actions.length - 1; i >= 0; i--) {
    if (actions[i]?.type === 'tool') {
      last = i;
      break;
    }
    if (actions[i]?.type === 'thinking') break;
  }
  if (last < 0) return [];
  let start = last;
  while (start > 0 && actions[start - 1]?.type === 'tool') start--;
  return actions.slice(start, last + 1);
}

const AVATAR_RUNNING_TOOL_STATUS = new Set([
  'preparing',
  'running',
  'pending',
  'queued',
  'awaiting_approval',
  'pending_approval',
  'awaiting_user_answer'
]);
const AVATAR_TERMINAL_STATUS = new Set([
  'completed',
  'failed',
  'timeout',
  'terminated',
  'cancelled'
]);

export const computed = {
  ...mapWritableState(useConnectionStore, [
    'isConnected',
    'socket',
    'stopRequested',
    'projectPath',
    'agentVersion',
    'thinkingMode',
    'runMode',
    'reasoningEffort'
  ]),
  ...mapState(useFileStore, ['contextMenu', 'fileTree', 'expandedFolders', 'todoList']),
  ...mapState(useQuickDockStore, {
    qdHasContent: 'hasContent',
    qdExpanded: 'expanded'
  }),
  ...mapWritableState(useQuickDockStore, {
    qdUserCollapsed: 'userCollapsed'
  }),
  ...mapWritableState(useUiStore, [
    'sidebarCollapsed',
    'chatDisplayMode',
    'rightWidth',
    'rightCollapsed',
    'isResizing',
    'resizingPanel',
    'minPanelWidth',
    'maxPanelWidth',
    'quotaToast',
    'toastQueue',
    'confirmDialog',
    'easterEgg',
    'isMobileViewport',
    'mobileOverlayMenuOpen',
    'activeMobileOverlay'
  ]),
  ...mapWritableState(useConversationStore, [
    'conversations',
    'conversationsLoading',
    'hasMoreConversations',
    'loadingMoreConversations',
    'currentConversationId',
    'currentConversationTitle',
    'searchQuery',
    'searchTimer',
    'searchResults',
    'searchGroups',
    'conversationInsertAnimations',
    'conversationListAnimationMode',
    'pendingDeletingConversationIds',
    'searchActive',
    'searchInProgress',
    'searchMoreAvailable',
    'searchOffset',
    'searchTotal',
    'conversationsOffset',
    'conversationsLimit',
    'runningWorkspaceTasks',
    'acknowledgedCompletedTaskIds',
    'workspaceGroups'
  ]),
  ...mapWritableState(useModelStore, ['currentModelKey']),
  ...mapState(useModelStore, ['models']),
  ...mapWritableState(useChatStore, [
    'messages',
    'currentMessageIndex',
    'streamingMessage',
    'expandedBlocks',
    'autoScrollEnabled',
    'userScrolling',
    'thinkingScrollLocks'
  ]),
  ...mapWritableState(useInputStore, [
    'inputMessage',
    'inputLineCount',
    'inputIsMultiline',
    'inputIsFocused',
    'quickMenuOpen',
    'toolMenuOpen',
    'settingsOpen',
    'imagePickerOpen',
    'videoPickerOpen',
    'selectedImages',
    'selectedVideos',
    'selectedFiles',
    'goalModeArmed',
    'goalRunning',
    'goalProgress',
    'goalDialogOpen'
  ]),
  resolvedRunMode() {
    // 历史值 deep 映射为 thinking
    const raw = (this.runMode as string) === 'deep' ? 'thinking' : this.runMode;
    const allowed = ['fast', 'thinking'];
    if (allowed.includes(raw)) {
      return raw;
    }
    return this.thinkingMode ? 'thinking' : 'fast';
  },
  headerRunModeOptions() {
    return [
      { value: 'fast', label: '快速模式', desc: '低思考，响应更快' },
      { value: 'thinking', label: '思考模式', desc: '持续推理，适合复杂任务' }
    ];
  },
  headerRunModeLabel() {
    const current = this.headerRunModeOptions.find((o) => o.value === this.resolvedRunMode);
    return current ? current.label : '快速模式';
  },
  // 推理强度滑块：仅思考模式 + 当前模型支持推理强度参数时显示
  showEffortSlider() {
    const modelStore = useModelStore();
    return this.resolvedRunMode === 'thinking' && !!modelStore.currentModel?.supportsReasoningEffort;
  },
  currentModelLabel() {
    const modelStore = useModelStore();
    return modelStore.currentModel?.label || '未选择模型';
  },
  policyUiBlocks() {
    const store = usePolicyStore();
    return store.uiBlocks || {};
  },
  adminDisabledModels() {
    const store = usePolicyStore();
    return store.disabledModelSet;
  },
  modelOptions() {
    const disabledSet = this.adminDisabledModels || new Set();
    const options = this.models || [];
    return options.map((opt) => ({
      ...opt,
      disabled: disabledSet.has(opt.key)
    }));
  },
  titleRibbonVisible() {
    return !this.isMobileViewport && this.chatDisplayMode === 'chat';
  },
  chatContainerStyle() {
    const minHeight = 90;
    const raw = Number(this.composerReservedHeight || 0);
    const height = Number.isFinite(raw) ? Math.max(minHeight, Math.round(raw)) : minHeight;
    const growth = Math.max(0, height - minHeight);
    return {
      '--composer-base-height': `calc(${minHeight}px + var(--app-bottom-inset, 0px))`,
      '--composer-reserved-height': `calc(${height}px + var(--app-bottom-inset, 0px))`,
      '--composer-growth-height': `${growth}px`
    };
  },
  ...mapWritableState(useToolStore, [
    'preparingTools',
    'activeTools',
    'toolActionIndex',
    'toolStacks',
    'toolSettings',
    'toolSettingsLoading'
  ]),
  ...mapWritableState(useResourceStore, [
    'tokenPanelCollapsed',
    'currentContextTokens',
    'currentConversationTokens',
    'projectStorage',
    'containerStatus',
    'containerNetRate',
    'usageQuota'
  ]),
  ...mapWritableState(useFocusStore, ['focusedFiles']),
  ...mapWritableState(useUploadStore, ['uploading', 'mediaUploading']),
  ...mapState(useMonitorStore, {
    monitorIsLocked: (store) => store.isLocked
  }),
  displayModeSwitchDisabled() {
    return !!this.policyUiBlocks.block_virtual_monitor;
  },
  compressionActiveForCurrentConversation() {
    // 压缩锁按对话隔离：只有「正在查看的对话就是正在压缩的对话」时才锁；
    // 其他对话与 /new 新建页（currentConversationId 为空）不受影响。
    if (!this.compressionInProgress && !this.compressing) {
      return false;
    }
    const cid = this.compressionConversationId;
    if (!cid) {
      // 未记录来源（旧数据/异常路径兑底）：只锁有打开对话的页面，/new 不锁
      return !!this.currentConversationId;
    }
    return cid === this.currentConversationId;
  },
  displayLockEngaged() {
    // 对话级并行后：运行中的任务不再硬锁输入栏——当前对话需要保持可输入以排队/停止，
    // 其他对话需要保持可输入以并行运行。仅当前对话压缩期间保持锁定。
    return this.compressionActiveForCurrentConversation;
  },
  currentWorkspaceHasRunningTask() {
    // 对话级隔离后：仅当「当前对话」有运行中任务时才拦截发送；
    // 同工作区其他对话的任务不再阻塞本对话（多对话并行）。
    if (!(this.versioningHostMode || this.dockerProjectMode) || !this.currentHostWorkspaceId) {
      return false;
    }
    const tasks = Array.isArray(this.runningWorkspaceTasks) ? this.runningWorkspaceTasks : [];
    return tasks.some((task: any) => {
      const status = String(task?.status || '');
      return (
        task?.workspace_id === this.currentHostWorkspaceId &&
        task?.conversation_id === this.currentConversationId &&
        ['pending', 'running', 'cancel_requested'].includes(status)
      );
    });
  },
  streamingUi() {
    return this.streamingMessage || this.hasPendingToolActions();
  },
  composerBusy() {
    const monitorLock = this.monitorIsLocked && this.chatDisplayMode === 'monitor';
    return (
      this.streamingUi ||
      this.taskInProgress ||
      monitorLock ||
      this.stopRequested ||
      this.compressionActiveForCurrentConversation
    );
  },
  /**
   * 子智能体非终态数（包含 running + idle，不含 failed/completed/terminated/timeout）。
   * 用于状态栏的小按钮显示 “x 个子智能体运行中”。
   */
  activeSubAgentCount() {
    const subAgentStore = useSubAgentStore();
    const list = Array.isArray(subAgentStore.subAgents) ? subAgentStore.subAgents : [];
    const TERMINAL = new Set(['completed', 'failed', 'timeout', 'terminated']);
    return list.filter((s: any) => {
      const status = String(s?.status || '').toLowerCase();
      return !TERMINAL.has(status);
    }).length;
  },
  /**
   * 主对话是否空闲。
   * 与 composerBusy 的区别：composerBusy=true 可能是“主对话空闲但后台子智能体在跑”
   * （传统模式 waitingForSubAgent / 多智能体模式 has_running_multi_agent），此时
   * 用户应当可以再发新消息触发主智能体下一轮工作。
   * mainChatIdle 只看主对话自身的活动状态：是否还在 streaming、是否正在停中、
   * 是否被 monitor 锁住、是否压缩中。
   */
  mainChatIdle() {
    return (
      !this.streamingUi &&
      !this.stopRequested &&
      !(this.monitorIsLocked && this.chatDisplayMode === 'monitor') &&
      !this.compressionActiveForCurrentConversation
    );
  },
  composerStreamingForInput() {
    return this.composerBusy && !this.displayLockEngaged;
  },
  composerHeroActive() {
    return (this.blankHeroActive || this.blankHeroExiting) && !this.composerInteractionActive;
  },
  showStatusAvatar() {
    return usePersonalizationStore().form?.show_status_avatar !== false;
  },
  composerInteractionActive() {
    const hasText = !!(this.inputMessage && this.inputMessage.trim().length > 0);
    const hasImages = Array.isArray(this.selectedImages) && this.selectedImages.length > 0;
    const hasFiles = Array.isArray(this.selectedFiles) && this.selectedFiles.length > 0;
    return this.quickMenuOpen || hasText || hasImages || hasFiles;
  },
  showScrollToBottomButton() {
    return this.chatDisplayMode === 'chat' && !this.stickIsNearBottom;
  },
  rightPanelsStyle() {
    const w = Math.min(this.rightWidth || 420, 600);
    const t = this.terminalPanelOpen;
    const g = this.gitChangesPanelOpen;
    const ratio = this.rightSplitRatio ?? 0.5;
    const r = Math.max(0.15, Math.min(0.85, Number(ratio) || 0.5));
    let rows: string;
    if (t && g) rows = `${r}fr 2px ${1 - r}fr`;
    else if (t) rows = '1fr 0px 0fr';
    else if (g) rows = '0fr 0px 1fr';
    else rows = '0fr 0px 0fr';
    return {
      width: w + 'px',
      gridTemplateRows: rows
    };
  },
  // 状态形象：驱动 StatusAvatar（欢迎页 + 对话末尾指示器）
  avatarStatus() {
    const messages = Array.isArray(this.messages) ? this.messages : [];
    const lastAssistant = (() => {
      for (let i = messages.length - 1; i >= 0; i--) {
        if (messages[i]?.role === 'assistant') return messages[i];
      }
      return null;
    })();

    // 后台运行计数（用于 work 文案）
    const subAgentStore = useSubAgentStore();
    const bgStore = useBackgroundCommandStore();
    const countRunning = (arr) =>
      (Array.isArray(arr) ? arr : []).filter((item) => {
        if (!item) return false;
        const status = String(item?.status || '').toLowerCase();
        return status ? !AVATAR_TERMINAL_STATUS.has(status) : true;
      }).length;
    const agentCount = countRunning(subAgentStore.subAgents);
    const cmdCount = countRunning(bgStore.commands);
    const bgText = (() => {
      const parts = [];
      if (cmdCount > 0) parts.push(`${cmdCount}个后台指令`);
      if (agentCount > 0) parts.push(`${agentCount}个后台智能体`);
      return parts.length ? `${parts.join('，')}运行中...` : '';
    })();

    const intentEnabled = !!usePersonalizationStore().form?.tool_intent_enabled;

    // 1) 思考中（防御：仅在任务仍运行时采信流式字段——异常中断后字段可能残留，
    //    虽有各终结路径的统一清理兑底，这里再加一层保险避免永久卡「思考中」）
    const isThinking =
      !!lastAssistant &&
      (this.taskInProgress || this.streamingMessage) &&
      (lastAssistant.currentStreamingType === 'thinking' || lastAssistant.activeThinkingId != null);

    // 2) 工具执行中（取最后一段并行工具，过滤未完成的）
    let runningTools = [];
    if (lastAssistant) {
      const segment = getLatestToolSegment(lastAssistant.actions || []);
      runningTools = segment.filter((a) => {
        const s = String(a?.tool?.status || '').toLowerCase();
        return a?.tool?.awaiting_content || !s || AVATAR_RUNNING_TOOL_STATUS.has(s);
      });
    }
    if (runningTools.length === 0) {
      const mapActions = [
        ...Array.from(this.preparingTools?.values?.() || []),
        ...Array.from(this.activeTools?.values?.() || [])
      ];
      runningTools = mapActions
        .map((item) => (item?.type === 'tool' ? item : { type: 'tool', tool: item }))
        .filter((a) => {
          const tool = a?.tool;
          if (!tool) return false;
          const s = String(tool?.status || '').toLowerCase();
          return tool?.awaiting_content || !s || AVATAR_RUNNING_TOOL_STATUS.has(s);
        });
    }
    const hasMapTools =
      (this.preparingTools && this.preparingTools.size > 0) ||
      (this.activeTools && this.activeTools.size > 0);

    // 3) 运行/等待态标志
    const running =
      this.streamingMessage ||
      this.taskInProgress ||
      this.waitingForSubAgent ||
      this.waitingForBackgroundCommand ||
      this.stopRequested ||
      hasMapTools ||
      runningTools.length > 0 ||
      agentCount > 0 ||
      cmdCount > 0;

    // ---- 决策 ----
    if (isThinking) {
      return { mode: 'think', toolKeys: [], toolTexts: [], text: '思考中...', tracking: false };
    }
    if (runningTools.length > 0) {
      const keys = runningTools.map((a) => toolFaceKey(a?.tool?.name));
      const getFinalIntentText = (tool: any) => {
        if (!tool) return '';
        const full = tool.intent_full || '';
        const rendered = tool.intent_rendered || '';
        // 只在 intent 打字效果完成后才显示完整文案，避免头像上出现逐字动画
        return full && rendered === full ? full : '';
      };
      const toolTexts = runningTools.map((a) => {
        return intentEnabled ? getFinalIntentText(a?.tool) : '';
      });
      let text = toolTexts[0] || '';
      if (runningTools.length > 1) {
        text = toolTexts[0] || '';
      } else if (!text) {
        text = intentEnabled ? getFinalIntentText(runningTools[0]?.tool) : '';
      }
      return { mode: 'tool', toolKeys: keys, toolTexts, text, tracking: false };
    }
    if (running) {
      // 工作态：等 API / 后台运行 / 流式输出正文
      let text = bgText;
      // 「等待 API 响应…」优先于随机等待文案：后端已发出请求、尚未开始回复
      // （api_request_start 事件驱动，覆盖首轮与工具轮次间的每一次等待）
      if (!text && this.apiRequestPending) {
        return { mode: 'work', toolKeys: [], toolTexts: [], text: '等待 API 响应…', tracking: false, apiWaiting: true };
      }
      if (!text) {
        const awaitingMsg = lastAssistant && lastAssistant.awaitingFirstContent ? lastAssistant : null;
        if (awaitingMsg) {
          text =
            (typeof awaitingMsg.generatingLabel === 'string' && awaitingMsg.generatingLabel.trim()) ||
            '';
        }
      }
      return { mode: 'work', toolKeys: [], toolTexts: [], text, tracking: false };
    }
    // 空闲
    return { mode: 'idle', toolKeys: [], toolTexts: [], text: '', tracking: true };
  }
};
