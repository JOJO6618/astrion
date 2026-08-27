// @ts-nocheck
import { debugLog } from '../common';
import { persistNewConversationType } from '../../state';
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

// 推理强度滑块是高频交互（后端限制 20 次/分）：档位变化只乐观更新 UI，
// 停止操作 600ms 后才把最终值发一次请求；序列开始时的对话 id 随请求带上，
// 即使 debounce 期间切换对话也能存到正确对话
const effortSave = {
  timer: null as ReturnType<typeof setTimeout> | null,
  rollback: undefined as string | null | undefined, // undefined = 无 pending
  latest: undefined as string | null | undefined,
  conversationId: null as string | null
};

export const modeMethods = {
  handleQuickModeToggle() {
    if (!this.isConnected || this.streamingMessage) {
      return;
    }
    this.handleCycleRunMode();
  },
  toggleQuickMenu() {
    if (!this.isConnected) {
      return;
    }
    const opened = this.inputToggleQuickMenu();
    if (!opened) {
      this.modeMenuOpen = false;
      this.modelMenuOpen = false;
    }
  },
  closeQuickMenu() {
    this.inputCloseMenus();
    this.modeMenuOpen = false;
    this.modelMenuOpen = false;
    this.agentTypeMenuOpen = false;
    this.workModeMenuOpen = false;
  },
  handleToggleGoalMode() {
    if (!this.isConnected) {
      return;
    }
    if (this.goalRunning) {
      // 运行中点击 → 打开进度弹窗，而非切换
      this.goalDialogOpen = true;
      this.inputCloseMenus();
      return;
    }
    const goalStatus = String(this.goalProgress?.status || '').toLowerCase();
    if (goalStatus === 'done' || goalStatus === 'stopped') {
      this.goalProgress = null;
      this.goalDialogOpen = false;
    }
    this.inputToggleGoalArmed();
    this.inputCloseMenus();
    this.modeMenuOpen = false;
    this.modelMenuOpen = false;
  },
  /**
   * 工作流激活成功（slash 菜单）：空对话激活时后端已自动创建对话并派发任务，
   * 前端进入该对话（bootstrap 会回放运行中的任务）。已有对话激活时 id 相同，无需跳转。
   */
  async handleWorkflowActivated(conversationId) {
    const normalized = String(conversationId || '').trim();
    if (!normalized || normalized === this.currentConversationId) {
      return;
    }
    // 跳转前把当前输入内容（slash 触发符已被 deleteSlashToken 删除）立即落盘。
    // 草稿是全局单一份（/api/input-draft 不按对话隔离），而 enterConversation 会经
    // currentConversationId watcher 触发 restoreComposerDraftState；若只等 1s debounce，
    // 恢复拿到的仍是删除前的旧草稿，已删除的 "/" 会被重新写回输入框。
    try {
      await this.persistComposerDraftNow({ reason: 'workflow-activated', force: true });
    } catch (_e) {
      // 落盘失败不阻断进入对话
    }
    // 侧边栏列表先插入占位（对齐 send.ts 首条消息创建对话的行为），否则列表要待下次刷新才出现
    try {
      const newPlaceholder = {
        id: normalized,
        title: '新对话',
        updated_at: new Date().toISOString(),
        total_messages: 0,
        total_tools: 0
      };
      if (Array.isArray(this.conversations)) {
        this.conversations.splice(
          0,
          this.conversations.length,
          newPlaceholder,
          ...this.conversations.filter((conv) => conv && conv.id !== normalized)
        );
      }
    } catch (_e) {
      // 占位失败不阻断进入对话
    }
    try {
      await this.enterConversation(normalized, { source: 'sidebar', urlMode: 'push' });
    } catch (error) {
      console.warn('[Workflow] 激活后进入新对话失败:', error);
    }
  },
  // 输入栏「智能体/多智能体」类型选择器：空对话态可选（写 newConversationType），
  // 已有对话为禁用展示态（类型创建时确定、不可变）。
  handleToggleAgentTypeMenu() {
    if (!this.isConnected || this.currentConversationId) {
      return;
    }
    const next = !this.agentTypeMenuOpen;
    this.agentTypeMenuOpen = next;
    if (next) {
      this.permissionMenuOpen = false;
      this.workModeMenuOpen = false;
      this.modelMenuOpen = false;
      this.modeMenuOpen = false;
      this.inputCloseMenus?.();
    }
  },
  async handleSelectNewConversationType(type) {
    const normalized = type === 'multi_agent' ? 'multi_agent' : 'agent';
    this.newConversationType = normalized;
    persistNewConversationType(normalized);
    this.agentTypeMenuOpen = false;
    // 选择即联动：同步切换侧边栏「智能体/多智能体」过滤器（纯本地引用交换、零请求）。
    // 选择器仅在空对话可用（有对话时禁用展示态），此处置空判断作防御；
    // 进入已有对话后两个状态保持解耦，互不干扰。
    if (!this.currentConversationId) {
      try {
        const { useConversationStore } = await import('../../../stores/conversation');
        useConversationStore().setSidebarConversationType(
          normalized === 'multi_agent' ? 'multi_agent' : 'normal'
        );
      } catch (_e) {
        // ignore
      }
    }
  },
  toggleModeMenu() {
    if (!this.isConnected || this.streamingMessage) {
      return;
    }
    const next = !this.modeMenuOpen;
    this.modeMenuOpen = next;
    if (next) {
      this.modelMenuOpen = false;
    }
    if (next) {
      this.inputSetToolMenuOpen(false);
      this.inputSetSettingsOpen(false);
      if (!this.quickMenuOpen) {
        this.inputOpenQuickMenu();
      }
    }
  },
  async handleModeSelect(mode) {
    if (!this.isConnected || this.streamingMessage) {
      return;
    }
    await this.setRunMode(mode);
  },
  async handleHeaderRunModeSelect(mode) {
    await this.handleModeSelect(mode);
    this.closeHeaderMenu();
  },
  async handleCycleRunMode() {
    const modes: Array<'fast' | 'thinking'> = ['fast', 'thinking'];
    const currentMode = this.resolvedRunMode;
    const currentIndex = modes.indexOf(currentMode);
    const nextMode = modes[(currentIndex + 1) % modes.length];
    await this.setRunMode(nextMode);
  },
  async setRunMode(mode, options = {}) {
    if (!this.isConnected || this.streamingMessage) {
      this.modeMenuOpen = false;
      return;
    }
    // 历史值 deep 映射为 thinking
    if (mode === 'deep') {
      mode = 'thinking';
    }
    const modelStore = useModelStore();
    const fastOnly = modelStore.currentModel?.fastOnly;
    const thinkingOnly = modelStore.currentModel?.thinkingOnly;
    if (fastOnly && mode !== 'fast') {
      if (!options.suppressToast) {
        this.uiPushToast({
          title: '模式不可用',
          message: '当前模型仅支持快速模式',
          type: 'warning'
        });
      }
      this.modeMenuOpen = false;
      this.inputCloseMenus();
      return;
    }
    if (thinkingOnly && mode !== 'thinking') {
      if (!options.suppressToast) {
        this.uiPushToast({
          title: '模式不可用',
          message: '当前模型仅支持思考模式',
          type: 'warning'
        });
      }
      this.modeMenuOpen = false;
      this.inputCloseMenus();
      return;
    }
    if (mode === this.resolvedRunMode) {
      this.modeMenuOpen = false;
      this.closeQuickMenu();
      return;
    }
    try {
      const response = await fetch('/api/thinking-mode', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ mode })
      });
      const payload = await response.json();
      if (!response.ok || !payload.success) {
        throw new Error(payload.message || payload.error || '切换失败');
      }
      const data = payload.data || {};
      this.thinkingMode =
        typeof data.thinking_mode === 'boolean' ? data.thinking_mode : mode !== 'fast';
      this.runMode = data.mode || mode;
    } catch (error) {
      console.error('切换运行模式失败:', error);
      const message = error instanceof Error ? error.message : String(error || '未知错误');
      this.uiPushToast({
        title: '切换思考模式失败',
        message: message || '请稍后重试',
        type: 'error'
      });
    } finally {
      this.modeMenuOpen = false;
      this.inputCloseMenus();
    }
  },
  async toggleThinkingMode() {
    await this.handleCycleRunMode();
  },
  async setReasoningEffort(effort) {
    if (effortSave.rollback === undefined) {
      // 序列开始：记录回滚目标与所属对话
      effortSave.rollback = this.reasoningEffort;
      effortSave.conversationId = this.currentConversationId || null;
    }
    effortSave.latest = effort;
    // 乐观更新（UI 与后续创建对话 body 都读这个值，立即生效）
    this.reasoningEffort = effort;
    if (effortSave.timer) clearTimeout(effortSave.timer);
    effortSave.timer = setTimeout(() => {
      effortSave.timer = null;
      this.flushReasoningEffortSave();
    }, 600);
  },
  // 立即执行 pending 的推理强度保存（debounce 兜底：切换/新建对话前调用）
  async flushReasoningEffortSave() {
    if (effortSave.timer) {
      clearTimeout(effortSave.timer);
      effortSave.timer = null;
    }
    if (effortSave.rollback === undefined) return;
    const effort = effortSave.latest;
    const rollback = effortSave.rollback;
    const targetConversationId = effortSave.conversationId;
    effortSave.rollback = undefined;
    effortSave.latest = undefined;
    effortSave.conversationId = null;
    try {
      const response = await fetch('/api/reasoning-effort', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ effort, conversation_id: targetConversationId })
      });
      const payload = await response.json();
      if (!response.ok || !payload.success) {
        throw new Error(payload.message || payload.error || '设置失败');
      }
      const data = payload.data || {};
      // 仅当 UI 仍停留在该序列的值时才用服务端回写确认
      //（切对话后 UI 已恢复为新对话的值，不得覆盖）
      if (
        Object.prototype.hasOwnProperty.call(data, 'reasoning_effort') &&
        this.reasoningEffort === effort
      ) {
        this.reasoningEffort =
          typeof data.reasoning_effort === 'string' ? data.reasoning_effort : null;
      }
    } catch (error) {
      // 仅当 UI 仍停留在该序列的值时才回滚
      if (this.reasoningEffort === effort) {
        this.reasoningEffort = rollback;
      }
      console.error('设置推理强度失败:', error);
      const message = error instanceof Error ? error.message : String(error || '未知错误');
      this.uiPushToast({
        title: '设置推理强度失败',
        message: message || '请稍后重试',
        type: 'error'
      });
    }
  },
  async handleStopAllSubAgents() {
    const isMultiAgent = this.currentConversationType === 'multi_agent';
    const mode = isMultiAgent ? 'soft_stop' : 'terminate';
    const title = isMultiAgent ? '是否暂停所有子智能体？' : '是否终结所有子智能体？';
    const message = isMultiAgent
      ? '所有正在运行的子智能体将停止工作并变为空闲状态。取消表示不执行操作。'
      : '所有后台子智能体将被强制终止。取消表示不执行操作。';
    const confirmText = isMultiAgent ? '暂停' : '终结';
    let proceed = false;
    try {
      const uiStore = (await import('../../../stores/ui')).useUiStore();
      proceed = await uiStore.requestConfirm({
        title,
        message,
        confirmText,
        cancelText: '取消',
        confirmVariant: 'danger',
        closeOnBackdrop: true
      });
    } catch (error) {
      console.error('[stopAllSubAgents] 弹窗异常:', error);
      return;
    }
    if (!proceed) return;
    try {
      const { useSubAgentStore } = await import('../../../stores/subAgent');
      const store = useSubAgentStore();
      const result = await store.stopAllAgents(mode);
      if (!result.success) {
        this.uiPushToast({
          title: '停止子智能体失败',
          message: result.error || '请重试',
          type: 'error'
        });
        return;
      }
      this.uiPushToast({
        title: isMultiAgent ? '子智能体已暂停' : '子智能体已终结',
        message: `已处理 ${result.stoppedCount || 0} 个子智能体`,
        type: 'info'
      });
    } catch (error: any) {
      console.error('[stopAllSubAgents] 调用失败:', error);
      this.uiPushToast({
        title: '停止子智能体失败',
        message: error?.message || String(error),
        type: 'error'
        });
    }
  }
};
