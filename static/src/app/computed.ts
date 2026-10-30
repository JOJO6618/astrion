// @ts-nocheck
import { t, currentLocale } from '@/locales';
import { groupModelOptions, filterModelGroups } from '@/utils/modelGroups';
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
import { resolveAvatarStatus } from '../utils/avatarStatus';

export const computed = {
  ...mapWritableState(useConnectionStore, [
    'isConnected',
    'stopRequested',
    'projectPath',
    'agentVersion',
    'thinkingMode',
    'runMode',
    'reasoningEffort',
    'chatgptFastMode',
    'chatgptFastModeSaving'
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
    'collapsingBlocks',
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
    void currentLocale.value;
    return [
      { value: 'fast', label: t('appCore.fastMode'), desc: t('appCore.fastModeDesc') },
      { value: 'thinking', label: t('input.thinkingMode'), desc: t('appCore.thinkingModeDesc') }
    ];
  },
  // 推理强度滑块 + 顶部栏强度标签：仅思考模式 + 当前模型支持推理强度参数时显示
  showEffortSlider() {
    const modelStore = useModelStore();
    return (
      this.resolvedRunMode === 'thinking' && !!modelStore.currentModel?.supportsReasoningEffort
    );
  },
  isChatgptModel() {
    return !!useModelStore().currentModel?.isChatgptModel;
  },
  supportsChatgptFastMode() {
    return !!useModelStore().currentModel?.supportsChatgptFastMode;
  },
  // 顶部栏强度标签：与滑块同条件（思考模式 + 模型支持）；档位沿用滑块原文
  // low/medium/high/xhigh/max，未设置（null）显示 default；
  // 不满足条件返回空串，顶部栏只显示模型名
  headerEffortLabel() {
    if (!this.showEffortSlider) {
      return '';
    }
    const effort = String(this.reasoningEffort ?? '')
      .trim()
      .toLowerCase();
    return effort || 'default';
  },
  currentModelLabel() {
    const modelStore = useModelStore();
    void currentLocale.value;
    return modelStore.currentModel?.label || t('appCore.modelNotSelected');
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
    // 协议级互切锁定（2026-09-25 泛化）：对话创建后 chat_completions ↔ responses
    // 禁切（/new 页不受限，首个任务落盘时模型即对话绑定）；同协议内互切放行
    const lockActive = !!this.currentConversationId;
    const curProto =
      (options.find((o) => o.key === this.currentModelKey) as any)?.apiProtocol ||
      'chat_completions';
    return options.map((opt) => {
      const locked =
        lockActive && String((opt as any).apiProtocol || 'chat_completions') !== curProto;
      return {
        ...opt,
        disabled: disabledSet.has(opt.key) || locked
      };
    });
  },
  // 模型菜单分组（opencode 式）：可见模型按提供商分组，组顺序=注册表首次出现顺序。
  // 分组键规则：provider 同步模型用 providerId/providerName（openai-codex 归 OpenAI 组）；
  // 其余（手写 custom_models）归「自定义」组。
  // 分组/过滤实现与设置页模型选择下拉共用（utils/modelGroups.ts）。
  groupedModelOptions() {
    return groupModelOptions(this.modelOptions || [], {
      custom: t('appCore.modelGroupCustom')
    });
  },
  // 模型菜单搜索过滤：只匹配菜单里显示出来的名字（label 小写子串），
  // 不匹配 key/模型 id（前缀会污染：搜 "co" 命中全部 codex/*），也不匹配组名。
  // 空组剔除；无搜索词时原样返回。
  filteredGroupedModelOptions() {
    return filterModelGroups(this.groupedModelOptions || [], this.modelMenuSearchQuery);
  },
  // 是否已配置任何模型（全量含隐藏）：用于模型菜单区分「搜索无结果」
  // 与「新装应用真空态未配置」两种空态，后者显示添加提供商引导
  hasAnyModels() {
    const modelStore = useModelStore();
    return Array.isArray(modelStore.allModels) && modelStore.allModels.length > 0;
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
    // 压缩活动按对话隔离，未知归属不影响当前页面。
    if (!this.compressionInProgress && !this.compressing) {
      return false;
    }
    const cid = this.compressionConversationId;
    return !!cid && cid === this.currentConversationId;
  },
  displayLockEngaged() {
    // 主任务及压缩期间均允许输入、排队和停止。
    // 空态锁定：host 模式下尚未创建/选择工作区时，发送必然失败，输入栏整体锁定
    // （textarea/语音/发送均吃 inputLocked，引导用户先创建工作区）。
    if (this.versioningHostMode && !this.currentHostWorkspaceId) {
      return true;
    }
    return false;
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
    return (
      this.streamingMessage ||
      this.hasPendingToolActions() ||
      this.compressionActiveForCurrentConversation
    );
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
  /** 当前对话保留的子实例数，与后端容量限制一致：只有 terminated 不占名额。 */
  subAgentCount() {
    const subAgentStore = useSubAgentStore();
    const list = Array.isArray(subAgentStore.subAgents) ? subAgentStore.subAgents : [];
    return list.filter((agent: any) => {
      return agent && String(agent.status || '').toLowerCase() !== 'terminated';
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
    return resolveAvatarStatus({
      messages: this.messages,
      taskInProgress: this.taskInProgress,
      streamingMessage: this.streamingMessage,
      waitingForSubAgent: this.waitingForSubAgent,
      waitingForBackgroundCommand: this.waitingForBackgroundCommand,
      stopRequested: this.stopRequested,
      apiRequestPending: this.apiRequestPending,
      preparingTools: this.preparingTools,
      activeTools: this.activeTools,
      subAgents: useSubAgentStore().subAgents,
      backgroundCommands: useBackgroundCommandStore().commands,
      toolIntentEnabled: !!usePersonalizationStore().form?.tool_intent_enabled
    });
  }
};
