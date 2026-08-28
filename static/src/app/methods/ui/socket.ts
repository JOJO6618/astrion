// @ts-nocheck
import { debugLog } from '../common';
import { t } from '@/locales';
import { persistWorkspaceMode } from '../../state';
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

export const socketMethods = {
  async initSocket() {
    // 主网页端已切换为 REST + 轮询模式，这里保留空实现用于兼容旧调用
    this.socket = null;
  },
  async checkConnectionHealth() {
    if (this.connectionHeartbeatInFlight) {
      connectionDiag('log', 'health-skip-inflight', {
        seq: this.connectionHeartbeatSeq,
        isConnected: !!this.isConnected,
        failCount: this.connectionHeartbeatFailCount
      });
      return;
    }
    this.connectionHeartbeatInFlight = true;
    const seq = Number(this.connectionHeartbeatSeq || 0) + 1;
    this.connectionHeartbeatSeq = seq;
    const requestId = `${Date.now()}-${seq}`;
    const startedAt = Date.now();
    const diagEnabled = isConnectionDiagEnabled();
    const healthUrl = diagEnabled ? '/api/health?diag=1' : '/api/health';
    const wasConnected = !!this.isConnected;
    let responseStatus: number | null = null;
    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timeoutMs =
      typeof this.connectionHeartbeatRequestTimeoutMs === 'number' &&
      this.connectionHeartbeatRequestTimeoutMs > 0
        ? this.connectionHeartbeatRequestTimeoutMs
        : 5000;
    const timeoutId = controller ? window.setTimeout(() => controller.abort(), timeoutMs) : null;
    try {
      const response = await fetch(healthUrl, {
        method: 'GET',
        cache: 'no-store',
        headers: {
          'X-Connection-Heartbeat': requestId
        },
        signal: controller?.signal
      });
      responseStatus = response.status;
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const failCountBeforeRecover = this.connectionHeartbeatFailCount || 0;
      this.isConnected = true;
      this.connectionHeartbeatFailCount = 0;
      this.connectionHeartbeatLastLatencyMs = Date.now() - startedAt;
      this.connectionHeartbeatLastStatusCode = responseStatus;
      this.connectionHeartbeatLastError = '';
      if (!wasConnected) {
        this.connectionHeartbeatLastChangeAt = Date.now();
        connectionDiag(
          'warn',
          'health-recovered',
          {
            requestId,
            seq,
            elapsedMs: this.connectionHeartbeatLastLatencyMs,
            failCountBeforeRecover,
            status: responseStatus,
            diagEnabled,
            endpoint: healthUrl,
            conversationId: this.currentConversationId || null,
            taskInProgress: !!this.taskInProgress,
            streamingMessage: !!this.streamingMessage,
            visibility: document?.visibilityState || 'unknown',
            online: typeof navigator !== 'undefined' ? navigator.onLine : null
          }
        );
      } else if (
        isConnectionDiagEnabled() &&
        (this.connectionHeartbeatLastLatencyMs >= 700 || seq <= 3 || seq % 60 === 0)
      ) {
        connectionDiag('log', 'health-ok', {
          requestId,
          seq,
          elapsedMs: this.connectionHeartbeatLastLatencyMs,
          status: responseStatus,
          endpoint: healthUrl,
          visibility: document?.visibilityState || 'unknown'
        });
      }
    } catch (error) {
      const nextFailCount = (this.connectionHeartbeatFailCount || 0) + 1;
      this.connectionHeartbeatFailCount = nextFailCount;
      this.connectionHeartbeatLastLatencyMs = Date.now() - startedAt;
      this.connectionHeartbeatLastStatusCode = responseStatus;
      const errName = error?.name || 'Error';
      const errMessage = error?.message || String(error);
      this.connectionHeartbeatLastError = `${errName}: ${errMessage}`;
      const failThreshold =
        typeof this.connectionHeartbeatFailThreshold === 'number' &&
        this.connectionHeartbeatFailThreshold > 0
          ? this.connectionHeartbeatFailThreshold
          : 3;
      const shouldDisconnect = nextFailCount >= failThreshold;
      // 改为连续失败阈值后再置灰，避免偶发请求超时导致“假断连”
      if (shouldDisconnect) {
        this.isConnected = false;
      }
      if (wasConnected && shouldDisconnect) {
        this.connectionHeartbeatLastChangeAt = Date.now();
      }
      const shouldLogFail =
        wasConnected || nextFailCount <= failThreshold || nextFailCount % 10 === 0;
      connectionDiag(
        shouldLogFail ? 'warn' : 'log',
        'health-failed',
        {
          requestId,
          seq,
          elapsedMs: this.connectionHeartbeatLastLatencyMs,
          failCount: nextFailCount,
          status: responseStatus,
          endpoint: healthUrl,
          diagEnabled,
          wasConnected,
          nowConnected: !!this.isConnected,
          failThreshold,
          shouldDisconnect,
          errorName: errName,
          errorMessage: errMessage,
          timeout: errName === 'AbortError',
          visibility: document?.visibilityState || 'unknown',
          online: typeof navigator !== 'undefined' ? navigator.onLine : null,
          conversationId: this.currentConversationId || null,
          taskInProgress: !!this.taskInProgress,
          streamingMessage: !!this.streamingMessage,
          hasPendingTools:
            typeof this.hasPendingToolActions === 'function' ? this.hasPendingToolActions() : null
        },
        { force: shouldLogFail }
      );
    } finally {
      this.connectionHeartbeatInFlight = false;
      if (timeoutId) {
        clearTimeout(timeoutId);
      }
    }
  },
  startConnectionHeartbeat() {
    if (this.connectionHeartbeatActive) {
      return;
    }
    this.connectionHeartbeatActive = true;
    this.connectionHeartbeatFailCount = 0;
    connectionDiag(
      'log',
      'heartbeat-start',
      {
        connectedIntervalMs: this.connectionHeartbeatIntervalMs,
        disconnectedIntervalMs: this.connectionHeartbeatDisconnectedIntervalMs
      }
    );
    const runHeartbeat = async () => {
      if (!this.connectionHeartbeatActive) {
        return;
      }
      await this.checkConnectionHealth();
      if (!this.connectionHeartbeatActive) {
        return;
      }
      const connectedInterval =
        typeof this.connectionHeartbeatIntervalMs === 'number' &&
        this.connectionHeartbeatIntervalMs > 0
          ? this.connectionHeartbeatIntervalMs
          : 8000;
      const disconnectedInterval =
        typeof this.connectionHeartbeatDisconnectedIntervalMs === 'number' &&
        this.connectionHeartbeatDisconnectedIntervalMs > 0
          ? this.connectionHeartbeatDisconnectedIntervalMs
          : 1000;
      const nextInterval = this.isConnected ? connectedInterval : disconnectedInterval;
      if (
        isConnectionDiagEnabled() &&
        (!this.isConnected || (this.connectionHeartbeatSeq || 0) <= 3)
      ) {
        connectionDiag('log', 'heartbeat-next', {
          seq: this.connectionHeartbeatSeq,
          isConnected: !!this.isConnected,
          failCount: this.connectionHeartbeatFailCount,
          nextInterval
        });
      }
      this.connectionHeartbeatTimer = window.setTimeout(() => {
        runHeartbeat();
      }, nextInterval);
    };

    // 先做一次即时探活，再根据连接状态动态设置下次轮询间隔
    runHeartbeat();
  },
  stopConnectionHeartbeat() {
    this.connectionHeartbeatActive = false;
    if (this.connectionHeartbeatTimer) {
      clearTimeout(this.connectionHeartbeatTimer);
      this.connectionHeartbeatTimer = null;
    }
    connectionDiag('log', 'heartbeat-stop', {});
  },
  // 零工作区降级初始化：/api/status 返回 code=no_workspace 时调用。
  // no_workspace 只会在宿主机模式分支抛出，因此这里可直接确定为 host 模式。
  // 目标只有一个：让侧边栏出现工作区入口，使用户能创建第一个工作区；
  // 创建完成后用户刷新页面即走正常初始化。
  async enterWorkspaceBootstrapMode() {
    debugLog('尚未创建任何工作区，进入工作区引导模式');
    this.versioningHostMode = true;
    this.dockerProjectMode = false;
    persistWorkspaceMode(true);
    this.isConnected = true;
    try {
      await this.fetchHostWorkspaces();
    } catch (err) {
      console.warn('引导模式加载工作区列表失败:', err);
    }
    this.uiPushToast({
      title: t('appUi.workspaceBootstrapTitle'),
      message: t('appUi.workspaceBootstrapMessage'),
      type: 'info'
    });
  },
  async loadInitialData() {
    try {
      debugLog('加载初始数据...');

      // 模型列表与工作区状态解耦：即使 /api/status 不可用（如尚未创建工作区），
      // 也要先加载已配置的模型，保证模型选择器可用
      const modelStore = useModelStore();
      await modelStore.fetchModels().catch((err) => {
        console.warn('加载模型列表失败，使用前端默认列表:', err);
      });

      const statusResponse = await fetch('/api/status');
      if (!statusResponse.ok) {
        // 零工作区的新部署：/api/status 依赖终端资源，会返回 503 code=no_workspace。
        // 此时不能按普通失败中断初始化——host/docker 模式标志只在 status 成功后
        // 才会设置，一旦中断，侧边栏工作区入口永不显示，用户无法创建第一个
        // 工作区（死锁）。改为进入「工作区引导」降级流程后直接返回。
        const errBody = await statusResponse.json().catch(() => ({}));
        if (errBody?.code === 'no_workspace') {
          await this.enterWorkspaceBootstrapMode();
          return;
        }
        throw new Error(t('appUi.statusApiRequestFailed', { status: statusResponse.status }));
      }
      const statusData = await statusResponse.json();
      this.socket = null;
      this.projectPath = statusData.project_path || '';
      this.agentVersion = statusData.version || this.agentVersion;
      this.applyStatusSnapshot(statusData);
      this.fetchWorkMode();
      this.fetchPermissionMode();
      this.fetchExecutionMode();
      this.fetchNetworkPermission();
      this.fetchPendingToolApprovals();
      // 立即更新配额和运行模式，避免等待其他慢接口
      this.fetchUsageQuota();
      if (statusData && typeof statusData.model_key === 'string') {
        modelStore.setModel(statusData.model_key);
        this.currentModelKey = modelStore.currentModelKey;
      }
      // 拉取管理员策略
      const policyStore = usePolicyStore();
      await policyStore.fetchPolicy();
      this.applyPolicyUiLocks();

      // 加载个性化设置
      const personalizationStore = usePersonalizationStore();
      if (!personalizationStore.loaded && !personalizationStore.loading) {
        await personalizationStore.fetchPersonalization().catch((err) => {
          console.warn('加载个性化设置失败:', err);
        });
      }

      // 仅在“无当前对话”的新会话入口应用默认模型/模式；
      // 对于已存在对话（含刷新恢复/URL指定对话），必须保留对话自身设置。
      // 注意：显式新建路由（/new、/multiagent/new）上，后端的“当前对话”
      // 只是上一个对话的残留上下文，不等于本页要创建新对话的意图，
      // 因此此时也必须应用个性化默认值（与“新建空对话”按钮行为一致）。
      const statusConversationIdForDefaults = statusData?.conversation?.current_id;
      const hasActiveConversation =
        typeof statusConversationIdForDefaults === 'string' &&
        statusConversationIdForDefaults.length > 0 &&
        !statusConversationIdForDefaults.startsWith('temp_');
      const isExplicitNewRouteForDefaults =
        typeof this.isExplicitNewConversationRoute === 'function' &&
        this.isExplicitNewConversationRoute();
      if (
        personalizationStore.loaded &&
        !this.currentConversationId &&
        (!hasActiveConversation || isExplicitNewRouteForDefaults)
      ) {
        const defaultRunMode = personalizationStore.form.default_run_mode;
        const defaultModel = personalizationStore.form.default_model;
        const defaultReasoningEffort = personalizationStore.form.default_reasoning_effort;

        if (defaultRunMode) {
          this.runMode = defaultRunMode;
          debugLog('应用默认运行模式:', defaultRunMode);
        }

        this.reasoningEffort = defaultReasoningEffort;
        if (defaultReasoningEffort) {
          debugLog('应用默认推理强度:', defaultReasoningEffort);
        }

        if (defaultModel) {
          modelStore.setModel(defaultModel);
          this.currentModelKey = modelStore.currentModelKey;
          debugLog('应用默认模型:', defaultModel);
        }

        // 根据默认运行模式设置思考模式
        if (defaultRunMode === 'thinking') {
          this.thinkingMode = true;
        } else if (defaultRunMode === 'fast') {
          this.thinkingMode = false;
        }
      }
      this.isConnected = true;

      const focusPromise = this.focusFetchFiles();
      let treePromise: Promise<any> | null = null;
      const isHostMode = statusData?.container?.mode === 'host';
      this.versioningHostMode = !!isHostMode;
      this.dockerProjectMode = !isHostMode;
      persistWorkspaceMode(!!isHostMode);
      if (isHostMode) {
        this.fileMarkTreeUnavailable(t('appUi.hostModeFileTreeUnavailable'));
        await this.fetchHostWorkspaces();
      } else {
        this.fileMarkTreeUnavailable(t('appUi.dockerModeFilesChanged'));
        await this.fetchHostWorkspaces();
        this.hostWorkspaceCreatePath = '';
        this.hostWorkspaceCreateLabel = '';
        this.hostWorkspaceCreateError = '';
        this.hostWorkspaceCreateSubmitting = false;
      }

      // 获取当前对话信息
      const isExplicitNewRoute = this.isExplicitNewConversationRoute();
      const runningConversationId = await this.getRunningTaskConversationId();
      const statusConversationId = statusData.conversation && statusData.conversation.current_id;
      const resumeConversationId = statusConversationId || runningConversationId;
      const currentPath = window.location.pathname.replace(/^\/+/, '');
      const isMultiAgentNewRoute = currentPath === 'multiagent/new' || currentPath === 'multiagent';

      // 显式新建对话路由（/new、/multiagent/new）与独立全屏路由（工作流编辑器等）
      // 永不自动恢复运行中的对话：前者是用户明确要空白页，后者不归对话体系，
      // 运行中任务可从侧边栏随时切回。
      if (
        resumeConversationId &&
        !this.currentConversationId &&
        !isMultiAgentNewRoute &&
        !isExplicitNewRoute &&
        !this.isConversationIndependentRoute()
      ) {
        this.skipConversationHistoryReload = true;
        // 首次从状态恢复对话时，避免 socket 的 conversation_loaded 再次触发历史加载
        this.skipConversationLoadedEvent = true;
        this.suppressTitleTyping = true;
        this.titleReady = false;
        this.currentConversationTitle = '';
        this.titleTypingText = '';
        this.currentConversationId = resumeConversationId;
        const pathFragment = this.stripConversationPrefix(resumeConversationId);
        const currentPath = window.location.pathname.replace(/^\/+/, '');
        if (currentPath !== pathFragment) {
          history.replaceState({ conversationId: resumeConversationId }, '', `/${pathFragment}`);
        }

        // 如果有当前对话，尝试获取标题和历史
        try {
          const convResponse = await fetch(`/api/conversations/current`);
          const convData = await convResponse.json();
          if (convData.success && convData.data) {
            this.currentConversationTitle = convData.data.title;
            this.titleReady = true;
            this.suppressTitleTyping = false;
            this.startTitleTyping(this.currentConversationTitle, { animate: false });
          } else {
            this.titleReady = true;
            this.suppressTitleTyping = false;
            const fallbackTitle = this.currentConversationTitle || t('common.newConversation');
            this.currentConversationTitle = fallbackTitle;
            this.startTitleTyping(fallbackTitle, { animate: false });
          }
          // 初始化时调用一次，因为 skipConversationHistoryReload 会阻止 watch 触发
          if (
            this.lastHistoryLoadedConversationId !== this.currentConversationId ||
            !Array.isArray(this.messages) ||
            this.messages.length === 0
          ) {
            await this.fetchAndDisplayHistory();
          }
          // 获取当前对话的Token统计
          this.fetchConversationTokenStatistics();
          this.updateCurrentContextTokens();
          await this.fetchVersioningStatus(this.currentConversationId, { silent: true });
          this.fetchPendingUserQuestions();
        } catch (e) {
          console.warn('获取当前对话标题失败:', e);
          this.titleReady = true;
          this.suppressTitleTyping = false;
          this.startTitleTyping(this.currentConversationTitle || t('common.newConversation'), '');
        }
      }

      // 待办数据依赖当前会话上下文，放在会话恢复/切换之后拉取，避免初始化早期拿到空快照
      const todoPromise = this.fileFetchTodoList();

      // 等待其他加载项完成（允许部分失败不阻塞模式切换）
      const pendingPromises = [focusPromise, todoPromise];
      if (treePromise) {
        pendingPromises.push(treePromise);
      }
      await Promise.allSettled(pendingPromises);
      await this.loadToolSettings(true);

      // 加载对话列表（修复：初始化时未加载对话列表的bug）
      this.conversationsOffset = 0;
      await this.loadConversationsList();

      debugLog('初始数据加载完成');
    } catch (error) {
      console.error('加载初始数据失败:', error);
      this.isConnected = false;
    }
  }
};
