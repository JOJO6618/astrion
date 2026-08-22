// @ts-nocheck
import { debugLog, goalModeDebugLog } from '../common';
import { useTaskStore } from '../../../stores/task';
import { useModelStore } from '../../../stores/model';
import { usePersonalizationStore } from '../../../stores/personalization';
import {
  extractSkillRefsFromMessage,
  SKILL_MARKDOWN_LINK_RE,
} from './shared';

export const sendMethods = {
  async handleSendOrStop() {
    if (this.compressionActiveForCurrentConversation) {
      this.uiPushToast({
        title: '对话自动压缩中',
        message: '当前不可发送/停止，请等待压缩完成',
        type: 'warning'
      });
      return;
    }
    const hasText = !!((this.inputMessage || '').trim().length > 0);
    const hasMedia =
      (Array.isArray(this.selectedImages) && this.selectedImages.length > 0) ||
      (Array.isArray(this.selectedVideos) && this.selectedVideos.length > 0);
    const hasFiles = Array.isArray(this.selectedFiles) && this.selectedFiles.length > 0;
    // 文件只是路径引用，不能单独构成一条消息，必须随文字/媒体一起发送。
    // 注意：仅在主对话空闲时拦截；运行中该按钮是「停止」语义，不能影响停止功能。
    if (hasFiles && !hasText && !hasMedia && !this.composerBusy) {
      this.uiPushToast({
        title: '需要文字消息',
        message: '附加文件需随文字消息一起发送',
        type: 'warning'
      });
      return;
    }
    // 主对话空闲但 composerBusy=true：composerBusy 只因后台子智能体在跑而保持。
    // 传统模式：waitingForSubAgent=true（taskInProgress=true 。多智能体模式：has_running_multi_agent=true。
    // 此时新文本消息应直接发送，触发主智能体下一轮工作，而不是被进队列等任务结束。
    const mainIdle = typeof this.mainChatIdle === 'function' ? this.mainChatIdle : (
      !this.streamingUi && !this.stopRequested && !this.compressionActiveForCurrentConversation
    );
    if (this.composerBusy && mainIdle && hasText) {
      // 如果有 pending 问题（子智能体询问主智能体），仍走问答路径，不走直接发送
      if (Array.isArray(this.pendingUserQuestions) && this.pendingUserQuestions.length > 0) {
        const answered = await this.answerUserQuestionFromComposer(this.inputMessage);
        if (answered) {
          this.inputClearMessage();
          this.inputSetLineCount(1);
          this.inputSetMultiline(false);
          this.autoResizeInput();
        }
        return;
      }
      // 主对话空闲但后台任务在跑：直接发送新消息触发主智能体下一轮
      this.sendMessage();
      return;
    }
    if (this.composerBusy && mainIdle && hasMedia) {
      this.uiPushToast({
        title: '后台子智能体运行中',
        message: '请等待后台任务结束后再发送图片/视频',
        type: 'warning'
      });
      return;
    }
    if (this.composerBusy) {
      if (hasText) {
        if (Array.isArray(this.pendingUserQuestions) && this.pendingUserQuestions.length > 0) {
          const answered = await this.answerUserQuestionFromComposer(this.inputMessage);
          if (answered) {
            this.inputClearMessage();
            this.inputSetLineCount(1);
            this.inputSetMultiline(false);
            this.autoResizeInput();
          }
          return;
        }
        const queued = await this.enqueueRuntimeQueuedMessage(
          this.inputMessage,
          Array.isArray(this.selectedFiles) ? [...this.selectedFiles] : []
        );
        if (queued) {
          this.inputClearMessage();
          this.inputClearSelectedFiles();
          this.inputSetLineCount(1);
          this.inputSetMultiline(false);
          this.autoResizeInput();
        }
        return;
      }
      if (hasMedia) {
        this.uiPushToast({
          title: '运行中仅支持文本',
          message: '图片/视频请等待当前任务结束后发送',
          type: 'warning'
        });
        return;
      }
      this.stopTask();
    } else {
      // 对账安全网：REST 对账显示当前对话仍在运行、但本地流式状态尚未恢复（如刚刷新页面）时，
      // 阻止直接发起新任务（后端同对话互斥会 409）。排队/停止走上方 composerBusy 分支，不受此守卫影响。
      if (this.currentWorkspaceHasRunningTask) {
        this.uiPushToast({
          title: '当前对话正在运行',
          message: '请等待当前对话任务完成后再发送新消息；同工作区的其他对话可正常并行。',
          type: 'warning'
        });
        return;
      }
      this.sendMessage();
    }
  },
  async sendMessage(options = {}) {
    const presetText = typeof options?.presetText === 'string' ? options.presetText : null;
    const usePresetText = presetText !== null;

    if (this.compressionActiveForCurrentConversation) {
      this.uiPushToast({
        title: '对话自动压缩中',
        message: '压缩完成后才能继续发送消息',
        type: 'warning'
      });
      return false;
    }
    if (this.streamingUi && !usePresetText) {
      return false;
    }
    if (!this.isConnected) {
      this.uiPushToast({
        title: '连接已断开',
        message: '当前无法发送消息，请等待连接恢复后重试',
        type: 'warning'
      });
      return false;
    }
    if (this.mediaUploading && !usePresetText) {
      this.uiPushToast({
        title: '上传中',
        message: '请等待图片/视频上传完成后再发送',
        type: 'info'
      });
      return false;
    }

    let text = ((usePresetText ? presetText : this.inputMessage) || '').trim();
    let preparedSkillRefs = [];
    if (!usePresetText) {
      const composerRef = typeof this.getInputComposerRef === 'function' ? this.getInputComposerRef() : null;
      const prepared =
        composerRef && typeof composerRef.prepareMessageForSend === 'function'
          ? composerRef.prepareMessageForSend(this.inputMessage)
          : null;
      if (prepared && typeof prepared.message === 'string') {
        text = prepared.message.trim();
        preparedSkillRefs = Array.isArray(prepared.skillRefs) ? prepared.skillRefs : [];
      }
    }
    const images = usePresetText
      ? []
      : Array.isArray(this.selectedImages)
        ? this.selectedImages.slice(0, 9)
        : [];
    const videos = usePresetText
      ? []
      : Array.isArray(this.selectedVideos)
        ? this.selectedVideos.slice(0, 1)
        : [];
    // 附加文件：普通发送取输入栏选择；队列自动续发（presetText）从队列条目携带
    const files = usePresetText
      ? Array.isArray(options?.files)
        ? options.files.slice(0, 9)
        : []
      : Array.isArray(this.selectedFiles)
        ? this.selectedFiles.slice(0, 9)
        : [];
    const hasText = text.length > 0;
    const hasImages = images.length > 0;
    const hasVideos = videos.length > 0;
    const hasFiles = files.length > 0;

    if (!hasText && !hasImages && !hasVideos) {
      return false;
    }

    const quotaType = this.thinkingMode ? 'thinking' : 'fast';
    if (this.isQuotaExceeded(quotaType)) {
      this.showQuotaToast({ type: quotaType });
      return false;
    }

    const modelStore = useModelStore();
    const currentModel = modelStore.models.find((m) => m.key === this.currentModelKey);
    if (hasImages && !currentModel?.supportsImage) {
      this.uiPushToast({
        title: '当前模型不支持图片',
        message: '请切换到支持图片输入的模型再发送图片',
        type: 'error'
      });
      return false;
    }

    if (hasVideos && !currentModel?.supportsVideo) {
      this.uiPushToast({
        title: '当前模型不支持视频',
        message: '请切换到支持视频输入的模型后再发送视频',
        type: 'error'
      });
      return false;
    }

    if (hasVideos && hasImages) {
      this.uiPushToast({
        title: '请勿同时发送',
        message: '视频与图片需分开发送，每条仅包含一种媒体',
        type: 'warning'
      });
      return false;
    }

    if (hasVideos) {
      this.uiPushToast({
        title: '视频处理中',
        message: '读取视频需要较长时间，请耐心等待',
        type: 'info',
        duration: 5000
      });
    }

    const message = text;
    const skillRefs = usePresetText
      ? []
      : preparedSkillRefs.length
        ? preparedSkillRefs
        : extractSkillRefsFromMessage(message);

    const wasBlank = this.isConversationBlank();
    if (wasBlank) {
      this.blankHeroExiting = true;
      this.blankHeroActive = true;
      setTimeout(() => {
        this.blankHeroExiting = false;
        this.blankHeroActive = false;
      }, 320);
    }

    let targetConversationId = this.currentConversationId;
    let backupToastId = null;
    if (!targetConversationId) {
      try {
        const personalizationStore = usePersonalizationStore();
        const shouldShowBackupToast =
          this.versioningHostMode &&
          personalizationStore.form.versioning_enabled_by_default &&
          personalizationStore.form.versioning_backup_mode === 'full';
        if (shouldShowBackupToast) {
          backupToastId = this.uiPushToast({
            title: '正在初始化备份',
            message: '正在创建完整工作区快照，请稍候…',
            type: 'info',
            duration: null,
            closable: false
          });
          this.versioningInitializingBackupToastId = backupToastId;
        }

        // 首条消息创建：类型来自输入栏「智能体/多智能体」选择器（newConversationType）
        const isMultiAgent = this.newConversationType === 'multi_agent';
        const createUrl = isMultiAgent ? '/api/multiagent/conversations' : '/api/conversations';
        // reasoning_effort 随创建权威写入新对话 meta：/new 页面用户可能已手动调整档位，
        // 不能依赖后端 terminal 值（会被 status 轮询的 prefs 应用覆盖）
        const createBody = isMultiAgent
          ? JSON.stringify({ preserve_mode: true, thinking_mode: this.thinkingMode, mode: this.runMode, reasoning_effort: this.reasoningEffort })
          : JSON.stringify({ thinking_mode: this.thinkingMode, mode: this.runMode, reasoning_effort: this.reasoningEffort });
        const createResp = await fetch(createUrl, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json'
          },
          body: createBody
        });
        const createResult = await createResp.json().catch(() => ({}));
        if (!createResp.ok || !createResult?.success || !createResult?.conversation_id) {
          throw new Error(createResult?.message || createResult?.error || '创建对话失败');
        }
        targetConversationId = createResult.conversation_id;
        // 创建即定型：落地对话类型（与后端 metadata 保持一致）
        this.currentConversationType = isMultiAgent ? 'multi_agent' : 'normal';
        try {
          const { useConversationStore } = await import('../../../stores/conversation');
          useConversationStore().$patch({ multiAgentMode: isMultiAgent });
        } catch (_e) {
          // ignore
        }
        this.skipConversationHistoryReload = true;
        this.currentConversationId = targetConversationId;
        this.currentConversationTitle = '新对话';
        const newPlaceholder = {
          id: targetConversationId,
          title: '新对话',
          updated_at: new Date().toISOString(),
          total_messages: 0,
          total_tools: 0
        };
        this.conversations.splice(
          0,
          this.conversations.length,
          newPlaceholder,
          ...this.conversations.filter((conv) => conv && conv.id !== targetConversationId)
        );

        // 分组视图下同步到当前工作区
        try {
          const { useConversationStore } = await import('../../../stores/conversation');
          const conversationStore = useConversationStore();
          const currentWorkspaceId = this.currentHostWorkspaceId;
          if (currentWorkspaceId) {
            conversationStore.ensureWorkspaceGroup(currentWorkspaceId);
            const group = conversationStore.workspaceGroups.find(
              (g: any) => g.workspaceId === currentWorkspaceId
            );
            if (group) {
              group.conversations.splice(
                0,
                group.conversations.length,
                newPlaceholder,
                ...group.conversations.filter((conv: any) => conv.id !== targetConversationId)
              );
              group.expanded = true;
              group.visibleOffset = 0;
              group.visibleLimit = 5;
            }
          }
        } catch (_err) {
          // ignore
        }

        const pathFragment = this.stripConversationPrefix(targetConversationId);
        // 对话类型不再是路由概念，统一裸路径 /<id>
        history.replaceState({ conversationId: targetConversationId }, '', `/${pathFragment}`);
      } catch (error) {
        this.uiPushToast({
          title: '发送失败',
          message: error?.message || '创建新对话失败，请重试',
          type: 'error'
        });
        return false;
      } finally {
        if (backupToastId) {
          this.uiDismissToast(backupToastId);
          this.versioningInitializingBackupToastId = null;
        }
      }
    }

    // 标记任务进行中，直到任务完成或用户手动停止
    this.taskInProgress = true;
    // 启动运行状态对账循环（幂等）：发送消息后确保对账兜底在运行
    if (typeof this.startRunningStateReconcile === 'function') {
      this.startRunningStateReconcile();
    }
    const localMessageSource = usePresetText ? (options?.source === 'runtime_queue_manual_guide' ? 'guidance' : 'presend') : 'user';
    this.chatAddUserMessage(
      message,
      images,
      videos,
      [],
      localMessageSource,
      hasFiles ? { files: [...files] } : {}
    );
    // 关键体验修复：用户发送后立刻显示 assistant 头部 + 工作中计时 + 等待提示，
    // 不等待 createTask / 轮询首事件返回。
    this.chatStartAssistantMessage();
    this.stopRequested = false;
    if (typeof this.monitorShowPendingReply === 'function') {
      this.monitorShowPendingReply();
    }
    if (this.autoScrollEnabled) {
      this.scrollToBottom();
    }

    // 使用 REST API 创建任务（轮询模式）
    try {
      const { useTaskStore } = await import('../../../stores/task');
      const taskStore = useTaskStore();
      if (typeof this.clearProcessedEvents === 'function') {
        this.clearProcessedEvents();
      }
      const startingGoalMode = this.goalModeArmed === true;
      if (startingGoalMode) {
        this.goalModeArmed = false;
        this.goalRunning = true;
        this.goalProgress = {
          goal: message,
          status: 'running',
          turn_count: 0,
          tokens_used: 0,
          tool_calls: 0,
          duration_seconds: 0
        };
      }

      await taskStore.createTask(message, images, videos, targetConversationId, {
        model_key: this.currentModelKey,
        run_mode: this.runMode,
        thinking_mode: this.thinkingMode,
        message_source: localMessageSource,
        goal_mode: startingGoalMode,
        skill_refs: skillRefs,
        files,
        eventHandler: (event: any) => this.handleTaskEvent(event)
      });

      debugLog('[Message] 任务已创建，开始轮询');
      await this.refreshRunningWorkspaceTasks?.();
    } catch (error) {
      console.error('[Message] 创建任务失败:', error);
      this.uiPushToast({
        title: '发送失败',
        message: error.message || '创建任务失败，请重试',
        type: 'error'
      });
      this.streamingMessage = false;
      this.taskInProgress = false;
      if (typeof this.cleanupTrailingEmptyAssistantPlaceholder === 'function') {
        this.cleanupTrailingEmptyAssistantPlaceholder('create_task_failed');
      }
      if (typeof this.forceUnlockMonitor === 'function') {
        this.forceUnlockMonitor('create_task_failed');
      }
      return false;
    }

    if (!usePresetText) {
      this.inputClearMessage();
      this.inputClearSelectedImages();
      this.inputClearSelectedVideos();
      this.inputClearSelectedFiles();
      this.inputSetImagePickerOpen(false);
      this.inputSetVideoPickerOpen(false);
      this.inputSetLineCount(1);
      this.inputSetMultiline(false);
      this.persistComposerDraftNow({
        reason: 'send-message-cleared',
        force: true,
        keepalive: true
      }).catch(() => {});
    }
    if (hasImages) {
      this.conversationHasImages = true;
      this.conversationHasVideos = false;
    }
    if (hasVideos) {
      this.conversationHasVideos = true;
      this.conversationHasImages = false;
    }
    if (this.autoScrollEnabled) {
      this.scrollToBottom();
    }
    this.autoResizeInput();

    // 发送消息后延迟更新当前上下文Token（关键修复：恢复原逻辑）
    setTimeout(() => {
      if (this.currentConversationId) {
        this.updateCurrentContextTokens();
      }
    }, 1000);
    return true;
  },
  async stopTask() {
    if (this._stopTaskRunning) {
      goalModeDebugLog('stopTask:debounce-rejected', { stopRequested: this.stopRequested });
      return;
    }
    this._stopTaskRunning = true;
    if (this.compressionActiveForCurrentConversation) {
      this._stopTaskRunning = false;
      this.uiPushToast({
        title: '对话自动压缩中',
        message: '压缩进行中，当前不可停止任务',
        type: 'warning'
      });
      return;
    }

    // 停止按钮现在只停主智能体，与后台任务无关。
    // canStop 判断只看主智能体是否在 streaming。多次点击被 stopRequested 抖动拦截。
    const canStop = this.streamingUi && !this.stopRequested;

    goalModeDebugLog('stopTask:entry', {
      composerBusy: this.composerBusy,
      stopRequested: this.stopRequested,
      taskInProgress: this.taskInProgress,
      streamingUi: this.streamingUi,
      canStop,
      currentTaskId: this.currentTaskId,
    });
    if (!canStop) {
      this._stopTaskRunning = false;
      return;
    }

    const shouldDropToolEvents = this.streamingUi;
    if (typeof this.markRuntimeQueueSuppressedByManualStop === 'function') {
      this.markRuntimeQueueSuppressedByManualStop();
    }
    this.stopRequested = true;
    this.dropToolEvents = shouldDropToolEvents;
    if (this.goalRunning || this.goalModeArmed) {
      this.goalRunning = false;
      this.goalModeArmed = false;
      this.goalProgress = null;
      this.goalDialogOpen = false;
    }

    try {
      const { useTaskStore } = await import('../../../stores/task');
      const taskStore = useTaskStore();

      if (taskStore.currentTaskId) {
        await taskStore.cancelTask();
      }

      // 等待后端确认；轮询继续，task_stopped 事件到达后会由 taskStore 自动停止轮询
      await new Promise((resolve) => setTimeout(resolve, 300));

      const shouldKeepBusy =
        ['running', 'pending', 'cancel_requested', 'canceled'].includes(String(taskStore.taskStatus));

      goalModeDebugLog('stopTask:try-end', {
        currentTaskId: taskStore.currentTaskId,
        taskStatus: taskStore.taskStatus,
        shouldKeepBusy,
        streamingMessage: this.streamingMessage,
      });

      // 清理前端状态
      this.clearPendingTools('user_stop');
      this.streamingMessage = false;
      // 若后台已回传停止事件，不要再次把输入区锁回“停止中”
      this.taskInProgress = shouldKeepBusy;
      this.forceUnlockMonitor('user_stop');
      if (typeof this.clearProcessedEvents === 'function') {
        this.clearProcessedEvents();
      }

      // 清理assistant消息的等待动画状态
      const lastMessage = this.messages[this.messages.length - 1];
      if (lastMessage && lastMessage.role === 'assistant') {
        lastMessage.awaitingFirstContent = false;
        lastMessage.generatingLabel = '';
      }
    } catch (error) {
      console.error('[Message] 取消任务失败:', error);
      const { useTaskStore } = await import('../../../stores/task');
      const taskStore = useTaskStore();
      const shouldKeepBusy =
        ['running', 'pending', 'cancel_requested', 'canceled'].includes(String(taskStore.taskStatus));

      goalModeDebugLog('stopTask:catch', {
        error: String(error),
        currentTaskId: taskStore.currentTaskId,
        taskStatus: taskStore.taskStatus,
        shouldKeepBusy,
      });

      // 即使失败也清理状态
      this.clearPendingTools('user_stop');
      this.streamingMessage = false;
      // 如果任务其实已结束，允许按钮恢复发送态
      this.taskInProgress = shouldKeepBusy;
      this.forceUnlockMonitor('user_stop');
      if (typeof this.clearProcessedEvents === 'function') {
        this.clearProcessedEvents();
      }

      // 清理assistant消息的等待动画状态
      const lastMessage = this.messages[this.messages.length - 1];
      if (lastMessage && lastMessage.role === 'assistant') {
        lastMessage.awaitingFirstContent = false;
        lastMessage.generatingLabel = '';
      }

      this.uiPushToast({
        title: '停止请求已发送',
        message: '若主对话未停止，请稍候；后台任务可通过状态栏单独停止',
        type: 'info'
      });
    } finally {
      // 确保清除 dropToolEvents 和 stopRequested 标志
      this.dropToolEvents = false;
      this.stopRequested = false;
      this._stopTaskRunning = false;
    }
  }
};
