// @ts-nocheck
import { ICONS, TOOL_CATEGORY_ICON_MAP } from '../utils/icons';

/* host/docker 模式缓存：模式是部署级属性几乎不变，首屏用缓存值渲染
   工作区入口按钮（按钮显隐依赖这两个 flag），socket 连接后校正并回写，
   避免按钮在初始化完成前「延迟零点几秒才出现」。 */
const WORKSPACE_MODE_STORAGE_KEY = 'agents_workspace_mode';

const loadCachedWorkspaceMode = (): 'host' | 'docker' | null => {
  try {
    const v = window.localStorage.getItem(WORKSPACE_MODE_STORAGE_KEY);
    return v === 'host' || v === 'docker' ? v : null;
  } catch {
    return null;
  }
};

export const persistWorkspaceMode = (isHostMode: boolean): void => {
  try {
    window.localStorage.setItem(WORKSPACE_MODE_STORAGE_KEY, isHostMode ? 'host' : 'docker');
  } catch {
    /* ignore */
  }
};

/* 待创建对话类型缓存：/new 页输入栏「智能体/多智能体」选择器的上次选择，
   刷新后恢复，避免每次新建都要重新选。 */
const NEW_CONVERSATION_TYPE_STORAGE_KEY = 'agents_new_conversation_type';

const loadNewConversationType = (): 'agent' | 'multi_agent' => {
  try {
    const v = window.localStorage.getItem(NEW_CONVERSATION_TYPE_STORAGE_KEY);
    return v === 'multi_agent' ? 'multi_agent' : 'agent';
  } catch {
    return 'agent';
  }
};

export const persistNewConversationType = (type: string): void => {
  try {
    window.localStorage.setItem(NEW_CONVERSATION_TYPE_STORAGE_KEY, type);
  } catch {
    /* ignore */
  }
};

const cachedWorkspaceMode = loadCachedWorkspaceMode();
const cachedNewConversationType = loadNewConversationType();

export function dataState() {
  return {
    // 路由相关
    initialRouteResolved: false,
    dropToolEvents: false,
    // 工作流编辑器 demo 路由（'workflows' | 'workflow/<name>'，空串 = 不在 demo 视图）
    workflowDemoRoute: '',
    // 当前打开对话的类型（'normal' | 'multi_agent'，创建时确定、不可变）；
    // 空对话态为 null。权威来源是对话 metadata.multi_agent_mode。
    currentConversationType: null,
    // /new 页输入栏选择器的待创建类型（'agent' | 'multi_agent'，localStorage 持久化）
    newConversationType: cachedNewConversationType,
    // 输入栏类型选择器菜单开关
    agentTypeMenuOpen: false,
    // 多智能体索引重建触发去重（每次页面加载最多一次）
    multiAgentIndexRebuildTriggered: false,

    // 轮询模式标志（禁用 WebSocket 事件处理）
    usePollingMode: true,
    // 后台子智能体等待状态
    waitingForSubAgent: false,
    // 后台 run_command 等待状态（用于文案区分）
    waitingForBackgroundCommand: false,
    // 是否在对话区展示 system 消息
    hideSystemMessages: true,

    // 工具状态跟踪
    preparingTools: new Map(),
    activeTools: new Map(),
    toolActionIndex: new Map(),
    toolStacks: new Map(),
    // 当前任务是否仍在进行中（用于保持输入区的"停止"状态）
    taskInProgress: false,
    // 对话运行状态对账定时器（事件为主、2.5s 对账纠偏，冲突以对账为准）
    runningStateReconcileTimer: null,
    // 对账清理方向的连续空闲确认计数（防 notice/idle dispatch 间隙误清）
    _runningStateIdleStreak: 0,
    // 宿主机多工作区任务列表后台刷新定时器（用于当前未查看运行对话时同步完成态）
    runningWorkspaceTasksRefreshTimer: null,
    // 运行期消息堆积（提前发送 / 引导对话）
    runtimeQueuedMessages: [],
    runtimeGuidanceFallbackQueue: [],
    runtimeQueueSuppressedMessageIds: new Set(),
    runtimeGuidanceSuppressedTextCounts: {},
    runtimeQueueLimit: 5,
    runtimeQueueAutoSendInProgress: false,
    runtimeQueueSyncLockKey: '',
    runtimeQueueSyncLockUntil: 0,
    projectGitSummary: null,
    projectGitSummaryRefreshTimer: null,
    terminalCountRefreshTimer: null,
    projectGitSummaryRefreshing: false,
    gitChangesPanelOpen: false,
    gitChangesLoading: false,
    gitChangesError: '',
    gitChangesContext: 3,
    gitChangesFileContexts: {},
    gitChangesFoldContexts: {},
    gitChangesDiff: null,
    // 实时终端面板
    terminalPanelOpen: false,
    terminalSessions: {} as Record<string, { working_dir?: string; shell?: string }>,
    terminalActiveSession: '',
    terminalSessionLogs: {} as Record<string, string>,
    terminalHydrated: {} as Record<string, boolean>,
    terminalCount: 0,
    // 上一次检测到的终端数量，用于判断 0→>0（自动开）和 >0→0（自动关）
    previousTerminalCount: 0,
    // 右侧面板上下分割比例（终端占比 0.15~0.85）
    rightSplitRatio: 0.5,
    // 输入区动态保留高度（用于同步扩大消息区可滚动范围）
    composerReservedHeight: 80,
    // 记录上一次成功加载历史的对话ID，防止初始化阶段重复加载导致动画播放两次
    lastHistoryLoadedConversationId: null,

    // ==========================================
    // 对话管理相关状态
    // ==========================================

    // 搜索功能
    // ==========================================
    searchRequestSeq: 0,
    searchActiveQuery: '',

    // Token统计相关状态（修复版）
    // ==========================================

    // 对话压缩状态
    compressing: false,
    compressionInProgress: false,
    // 正在压缩的对话 id：压缩锁只作用于该对话本身，不影响其他对话与 /new 新建页
    // （多对话独立运行后，压缩不再是对话级全局锁）。
    compressionConversationId: null,
    compressionMode: '',
    compressionStage: '',
    compressionError: '',
    compressionToastId: null,
    skipConversationLoadedEvent: false,
    skipConversationHistoryReload: false,
    _scrollListenerReady: false,
    historyLoading: false,
    historyLoadingFor: null,
    historyLoadSeq: 0,
    blankHeroActive: false,
    blankHeroExiting: false,
    blankWelcomeText: '',
    lastBlankConversationId: null,
    // 对话标题打字效果
    titleTypingText: '',
    titleTypingTarget: '',
    titleTypingTimer: null,
    titleReady: false,
    suppressTitleTyping: false,
    headerMenuOpen: false,
    blankWelcomePool: [
      '有什么可以帮忙的？',
      '想了解些热点吗？',
      '要我帮你完成作业吗？',
      '整点代码？',
      '随便聊点什么？',
      '想让我帮你整理一下思路吗？',
      '要不要我帮你写个小工具？',
      '发我一句话，我来接着做。',
      '我来给你画幅画？',
      '想看小猫吗？',
      '有什么难题丢过来吧。',
      '想不想看个有意思的？',
      '来，说说你的需求~'
    ],
    mobileViewportQuery: null,
    modeMenuOpen: false,
    modelMenuOpen: false,
    permissionMenuOpen: false,
    currentPermissionMode: 'unrestricted',
    pendingPermissionMode: '',
    executionModeEnabled: false,
    currentExecutionMode: 'sandbox',
    pendingExecutionMode: '',
    networkPermissionEnabled: false,
    currentNetworkPermission: 'restricted',
    pendingNetworkPermission: '',
    networkPermissionOptions: [
      {
        value: 'restricted',
        label: '受限',
        description: '仅允许本地回环访问，外部网络不可用'
      },
      {
        value: 'full',
        label: '完全开放',
        description: '允许所有出站和入站网络连接'
      }
    ],
    workModeMenuOpen: false,
    currentWorkMode: 'plan',
    workModeOptions: [
      {
        value: 'plan',
        label: '计划',
        description: '只制定计划，批准后执行'
      },
      {
        value: 'ask',
        label: '询问',
        description: '先讨论确认，再开工'
      },
      {
        value: 'execute',
        label: '执行',
        description: '自行补全细节，直接开工'
      }
    ],
    pathAuthorizationDialogOpen: false,
    pathAuthorizationMode: 'writable',
    pathAuthorizationWritableDraft: '',
    pathAuthorizationReadableDraft: '',
    pathAuthorizationDraft: '',
    pathAuthorizationSaving: false,
    versioningHostMode: cachedWorkspaceMode === 'host',
    dockerProjectMode: cachedWorkspaceMode === 'docker',
    hostWorkspaces: [],
    currentHostWorkspaceId: '',
    defaultHostWorkspaceId: '',
    hostWorkspaceSwitching: false,
    hostWorkspaceCreatePath: '',
    hostWorkspaceCreateLabel: '',
    hostWorkspaceCreateSubmitting: false,
    hostWorkspaceCreateError: '',
    hostWorkspaceManageSubmitting: false,
    versioningEnabled: false,
    versioningTrackingMode: 'conversation_only',
    versioningMode: 'overwrite',
    versioningDialogOpen: false,
    versioningLoading: false,
    versioningCheckpoints: [],
    versioningSelectedSeq: null,
    versioningSelectedDetail: null,
    versioningDetailLoading: false,
    versioningRestoring: false,
    versioningRestoreMode: 'overwrite',
    versioningInitializingBackupToastId: null,
    permissionModeOptions: [
      {
        value: 'readonly',
        label: '只读',
        description: '仅允许读取/搜索类工具，禁止修改工作区'
      },
      {
        value: 'approval',
        label: '批准',
        description: '对工作区文件进行修改的工具需用户批准后才会执行'
      },
      {
        value: 'auto_approval',
        label: '自动审核',
        description: '工作区内写入直通，高风险操作由后台审核智能体自动审批'
      },
      {
        value: 'unrestricted',
        label: '无限制',
        description: '保持当前默认行为，不额外拦截'
      }
    ],
    executionModeOptions: [
      {
        value: 'sandbox',
        label: '沙箱',
        description: '所有指令会在系统沙箱中执行'
      },
      {
        value: 'direct',
        label: '完全访问权限',
        description: '所有指令会在宿主机直接执行'
      }
    ],
    pendingToolApprovals: [],
    decidingApprovalIds: [],
    pendingUserQuestions: [],
    pendingPlanApprovals: [],
    answeringPlanApprovalIds: [],
    userQuestionDialogVisible: false,
    userQuestionMinimized: false,
    userQuestionActiveIndex: 0,
    answeringUserQuestionIds: [],
    userQuestionOriginalTitle: '',
    userQuestionTitleBlinkTimer: null,
    userQuestionTitleBlinkRed: true,
    autoApprovalFeedLines: [],
    autoApprovalFinalMessage: '',
    autoApprovalTitle: '自动审批记录',
    approvalAutoCloseTimer: null,
    imageEntries: [],
    imageLoading: false,
    videoEntries: [],
    videoLoading: false,
    conversationHasImages: false,
    conversationHasVideos: false,
    conversationListRequestSeq: 0,
    conversationListRefreshToken: 0,
    connectionHeartbeatTimer: null,
    connectionHeartbeatActive: false,
    connectionHeartbeatFailCount: 0,
    connectionHeartbeatSeq: 0,
    connectionHeartbeatLastLatencyMs: 0,
    connectionHeartbeatLastError: '',
    connectionHeartbeatLastStatusCode: null,
    connectionHeartbeatLastChangeAt: 0,
    connectionHeartbeatInFlight: false,
    connectionHeartbeatFailThreshold: 3,
    connectionHeartbeatRequestTimeoutMs: 5000,
    connectionHeartbeatIntervalMs: 8000,
    connectionHeartbeatDisconnectedIntervalMs: 1000,
    composerDraftSaveTimer: null,
    composerDraftDirty: false,
    composerDraftLastSyncedContent: '',
    composerDraftFetchSeq: 0,

    // 工具控制菜单
    icons: ICONS,
    toolCategoryIcons: TOOL_CATEGORY_ICON_MAP,

    // 对话回顾
    reviewDialogOpen: false,
    reviewSelectedConversationId: null,
    reviewSubmitting: false,
    reviewPreviewLines: [],
    reviewPreviewLoading: false,
    reviewPreviewError: null,
    reviewPreviewLimit: 20,
    reviewSendToModel: true,
    reviewGeneratedPath: null,
    // 回顾弹窗独立的对话列表（只含有内容的对话，不污染侧边栏）
    reviewConversations: [],
    reviewListLoading: false,
    reviewListLoadingMore: false,
    reviewListHasMore: false,
    reviewListOffset: 0,
    reviewListLimit: 20,

    // 新手教程首次引导弹窗
    tutorialPromptVisible: false,
    tutorialPromptLoading: false,
    tutorialPromptUsername: '',

    // 拖拽上传状态
    dragOverActive: false,

    // 拖拽事件绑定函数（用于正确移除监听）
    _boundDragEnter: null,
    _boundDragOver: null,
    _boundDragLeave: null,
    _boundDrop: null,
    _manualScrollSuppressUntil: 0,
    _escapedByUserScroll: false,
    _autoRelockCooldownUntil: 0,

    // stick-to-bottom 状态（用于“回到底部”按钮显隐）
    stickIsAtBottom: true,
    stickIsNearBottom: true
  };
}
