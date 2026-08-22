// @ts-nocheck
import { debugLog, traceLog } from '../common';
import { useQuickDockStore } from '../../../stores/quickDock';
import { useConversationStore } from '../../../stores/conversation';
import { useWorkflowStore } from '../../../stores/workflow';

/**
 * 统一加载协议（方案 B）：进入对话的单一入口。
 *
 * 一次 GET bootstrap 拿「元数据 + 文件态历史 + 运行状态 + 任务重放决策」，
 * 替代旧的「PUT load + GET messages + GET tasks + 历史死等重试」串行链。
 * 后端接口纯只读，不切换任何 terminal 上下文，天然规避 safe_nav 双轨问题。
 *
 * 设计文档：docs/conversation_load_unification_plan.md
 */
export const bootstrapMethods = {
  /**
   * @param conversationId 对话 ID（可带或不带 conv_ 前缀）
   * @param options.source 'refresh' | 'sidebar'（仅用于日志追踪）
   * @param options.workspaceId 目标工作区（host/docker 多工作区场景）
   * @param options.urlMode 'push' | 'replace' | 'none'（缺省 push）
   * @param options.preserveListPosition 保持侧边栏列表位置不置顶
   * @param options.resetUI 渲染前 resetAllStates（sidebar 切换保留「清空→填入」语义）
   */
  async enterConversation(conversationId, options = {}) {
    const {
      source = 'sidebar',
      workspaceId = '',
      urlMode = 'push',
      preserveListPosition = false,
      resetUI = false
    } = options;

    const isHostLikeMode = Boolean(this.versioningHostMode || this.dockerProjectMode);
    const requestUrl = workspaceId && isHostLikeMode
      ? `/api/conversations/${conversationId}/bootstrap?workspace_id=${encodeURIComponent(workspaceId)}`
      : `/api/conversations/${conversationId}/bootstrap`;

    traceLog('enterConversation:start', { conversationId, source, urlMode });
    const response = await fetch(requestUrl);
    const result = await response.json();
    if (!result.success) {
      debugLog('enterConversation:failed', { conversationId, error: result.error || result.message });
      return result;
    }

    const data = result.data || {};
    const meta = data.meta || {};
    const normalizedId = data.conversation_id || conversationId;

    // 1. 应用模式/模型（与旧 PUT load 响应处理对齐）
    if (typeof meta.run_mode === 'string') {
      // 历史值 deep 映射为 thinking
      this.runMode = (meta.run_mode === 'deep' ? 'thinking' : meta.run_mode) as 'fast' | 'thinking';
      this.thinkingMode =
        typeof meta.thinking_mode === 'boolean' ? meta.thinking_mode : meta.run_mode !== 'fast';
    } else if (typeof meta.thinking_mode === 'boolean') {
      this.thinkingMode = meta.thinking_mode;
      this.runMode = meta.thinking_mode ? 'thinking' : 'fast';
    }
    if (typeof meta.model_key === 'string' && meta.model_key) {
      this.modelSet(meta.model_key);
    }
    // 恢复会话级推理强度档位（null = 默认，不传参）
    this.reasoningEffort = typeof meta.reasoning_effort === 'string' ? meta.reasoning_effort : null;
    // 对话类型从 metadata 落地（创建时确定、不可变）
    this.currentConversationType = meta.multi_agent_mode === true ? 'multi_agent' : 'normal';
    // conversationStore.multiAgentMode 语义 = 「当前对话是否多智能体」（subAgent store 读取）
    useConversationStore().$patch({ multiAgentMode: meta.multi_agent_mode === true });

    // 2. 当前对话状态（skip 标记阻止 currentConversationId watch 重复拉历史）
    this.skipConversationHistoryReload = true;
    this.currentConversationId = normalizedId;
    this.refreshProjectGitSummary?.();
    this.fetchTerminalCount();
    if (!preserveListPosition) {
      this.promoteConversationToTop(normalizedId);
    }
    if (urlMode !== 'none') {
      // 对话类型不再是路由概念，统一裸路径 /<id>
      const stateMethod = urlMode === 'replace' ? 'replaceState' : 'pushState';
      history[stateMethod](
        { conversationId: normalizedId },
        '',
        `/${this.stripConversationPrefix(normalizedId)}`
      );
    }
    this.skipConversationLoadedEvent = true;

    // 3. 重置 UI（sidebar 切换保留「清空→填入」语义；刷新路径不传以避免闪烁）
    if (resetUI) {
      this.resetAllStates(`enterConversation:${normalizedId}`);
    }

    // 4. 渲染文件态历史（复用现有渲染器；设置防重标记避免 loadInitialData 二次拉取）
    const messages = Array.isArray(data.messages) ? data.messages : [];
    this.logMessageState?.('enterConversation:before-render', {
      conversationId: normalizedId,
      count: messages.length
    });
    this.messages = [];
    if (messages.length > 0) {
      this.renderHistoryMessages(messages);
      // 与 fetchAndDisplayHistory 一致：隐藏期内等待动态高度稳定再滚到底
      if (typeof this.settleHistoryRenderAndScroll === 'function') {
        await this.settleHistoryRenderAndScroll();
      } else {
        await this.$nextTick();
        this.scrollHistoryToBottomInstant();
      }
    }
    this.lastHistoryLoadedConversationId = normalizedId;
    this.refreshBlankHeroState();

    // 4.5 快捷窗口：回填本次对话编辑/创建文件记录。
    // 必须先等 currentConversationId 的级联 watcher（app watcher 清空 → store 同步 →
    // QuickDock 内清空）全部执行完，否则回填数据会被 watcher 覆盖。
    // setTimeout(0) 走宏任务，比 $nextTick 的 microtask 更保险。
    await new Promise((resolve) => setTimeout(resolve, 0));
    useQuickDockStore().setEditedFiles(
      Array.isArray(data.edited_files) ? data.edited_files : []
    );

    // 4.6 快捷窗口：回填工作流运行状态（静态呈现，不播动画）。
    // 失败不影响对话进入。
    try {
      const wfResp = await fetch(`/api/workflow/status?conversation_id=${encodeURIComponent(normalizedId)}`);
      if (wfResp.ok) {
        const wfData = await wfResp.json();
        useWorkflowStore().setWorkflow(wfData?.snapshot, false);
      }
    } catch (wfErr) {
      debugLog('enterConversation:workflow-status-failed', { conversationId: normalizedId, error: String(wfErr || '') });
    }

    // 5. 运行中任务快速恢复（任务/事件/判据已由 bootstrap 聚合，
    //    免去 GET /api/tasks + 历史死等 + GET /api/tasks/{id} 三次请求）
    const running = data.running || {};
    if (running.is_main_running && data.task_replay) {
      await this.restoreTaskState({ bootstrapReplay: data.task_replay });
    }

    traceLog('enterConversation:done', {
      conversationId: normalizedId,
      messagesCount: messages.length,
      isTrulyActive: !!running.is_truly_active,
      needsRebuild: data.task_replay?.needs_rebuild
    });

    return {
      success: true,
      title: meta.title || '',
      run_mode: meta.run_mode,
      thinking_mode: meta.thinking_mode,
      model_key: meta.model_key,
      multi_agent_mode: meta.multi_agent_mode,
      conversation_id: normalizedId
    };
  }
};
