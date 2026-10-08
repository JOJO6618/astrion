// @ts-nocheck
import { debugLog } from '../common';
import { t } from '@/locales';
import { useModelStore } from '../../../stores/model';
import { usePersonalizationStore } from '../../../stores/personalization';
import { extractSkillRefsFromMessage } from './shared';
import { usePreviewStore } from '../../../stores/preview';
import { createOwnedMessageTask, ensureMessageSession } from './ownership';
import { messageControlMethods } from './controls';
import {
  beginConversationSubmission,
  endConversationSubmission,
  ownsConversationSession
} from '../conversation/session';

export { createOwnedMessageTask, ensureMessageSession } from './ownership';

export const sendMethods = {
  ...messageControlMethods,
  async sendMessage(options = {}) {
    const session = ensureMessageSession(this);
    const submission = beginConversationSubmission(session);
    if (!submission) return false;
    try {
      return await this.sendMessageInSession(options, session);
    } finally {
      endConversationSubmission(session, submission);
      if (ownsConversationSession(this, session) && this.currentConversationId) {
        this.startRunningStateReconcile?.();
      }
    }
  },
  async sendMessageInSession(options, session) {
    const presetText = typeof options?.presetText === 'string' ? options.presetText : null;
    const usePresetText = presetText !== null;
    const owns = () => ownsConversationSession(this, session);
    usePreviewStore().resetAutoOpen();

    if (this.compressionActiveForCurrentConversation) {
      return this.enqueueRuntimeQueuedMessage(
        usePresetText ? presetText : this.inputMessage,
        options?.files || this.selectedFiles || []
      );
    }
    if (this.streamingUi && !usePresetText) {
      return false;
    }
    if (!this.isConnected) {
      this.uiPushToast({
        title: t('appMessages.connectionLostTitle'),
        message: t('appMessages.connectionLostMessage'),
        type: 'warning'
      });
      return false;
    }
    if (this.mediaUploading && !usePresetText) {
      this.uiPushToast({
        title: t('appMessages.uploadingTitle'),
        message: t('appMessages.uploadingMessage'),
        type: 'info'
      });
      return false;
    }

    let text = ((usePresetText ? presetText : this.inputMessage) || '').trim();
    let preparedSkillRefs = [];
    if (!usePresetText) {
      const composerRef =
        typeof this.getInputComposerRef === 'function' ? this.getInputComposerRef() : null;
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
        title: t('appMessages.modelNoImageTitle'),
        message: t('appMessages.modelNoImageMessage'),
        type: 'error'
      });
      return false;
    }

    if (hasVideos && !currentModel?.supportsVideo) {
      this.uiPushToast({
        title: t('appMessages.modelNoVideoTitle'),
        message: t('appMessages.modelNoVideoMessage'),
        type: 'error'
      });
      return false;
    }

    if (hasVideos && hasImages) {
      this.uiPushToast({
        title: t('appMessages.noMixedMediaTitle'),
        message: t('appMessages.noMixedMediaMessage'),
        type: 'warning'
      });
      return false;
    }

    if (hasVideos) {
      this.uiPushToast({
        title: t('appMessages.videoProcessingTitle'),
        message: t('appMessages.videoProcessingMessage'),
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
        if (!owns()) return;
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
            title: t('appMessages.initializingBackupTitle'),
            message: t('appMessages.initializingBackupMessage'),
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
          ? JSON.stringify({
              preserve_mode: true,
              thinking_mode: this.thinkingMode,
              mode: this.runMode,
              reasoning_effort: this.reasoningEffort
            })
          : JSON.stringify({
              thinking_mode: this.thinkingMode,
              mode: this.runMode,
              reasoning_effort: this.reasoningEffort
            });
        const createResp = await fetch(createUrl, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json'
          },
          body: createBody,
          signal: session.controller.signal
        });
        const createResult = await createResp.json().catch(() => ({}));
        if (!owns()) return false;
        if (!createResp.ok || !createResult?.success || !createResult?.conversation_id) {
          throw new Error(
            createResult?.message ||
              createResult?.error ||
              t('appMessages.createConversationFailed')
          );
        }
        targetConversationId = createResult.conversation_id;
        // 创建即定型：落地对话类型（与后端 metadata 保持一致）
        this.currentConversationType = isMultiAgent ? 'multi_agent' : 'normal';
        session.conversationId = targetConversationId;
        this.currentConversationId = targetConversationId;
        this.currentConversationTitle = t('common.newConversation');
        const newPlaceholder = {
          id: targetConversationId,
          title: t('common.newConversation'),
          updated_at: new Date().toISOString(),
          total_messages: 0,
          total_tools: 0
        };
        // 新建对话的类型同时决定侧边栏过滤器目标类型
        const targetType = isMultiAgent ? 'multi_agent' : 'normal';
        try {
          const { useConversationStore } = await import('../../../stores/conversation');
          if (!owns()) return false;
          const conversationStore = useConversationStore();
          conversationStore.$patch({ multiAgentMode: isMultiAgent });

          // 占位对话按「新建类型」插入对应过滤器缓存（而非当前过滤器类型的缓存），
          // 并同步切换侧边栏「智能体/多智能体」过滤器，避免创建后错位显示（需刷新才归位）。
          const targetCache = conversationStore.conversationsCache[targetType];
          targetCache.list.splice(
            0,
            targetCache.list.length,
            newPlaceholder,
            ...targetCache.list.filter((conv: any) => conv && conv.id !== targetConversationId)
          );
          if (conversationStore.sidebarConversationType !== targetType) {
            conversationStore.setSidebarConversationType(targetType);
          }

          // 分组视图下同步到当前工作区（同样按目标类型缓存落位）
          try {
            const currentWorkspaceId = this.currentHostWorkspaceId;
            if (currentWorkspaceId) {
              conversationStore.ensureWorkspaceGroup(currentWorkspaceId);
              const group = conversationStore.workspaceGroups.find(
                (g: any) => g.workspaceId === currentWorkspaceId
              );
              if (group) {
                const groupList = group.conversationsByType?.[targetType];
                if (groupList) {
                  groupList.splice(
                    0,
                    groupList.length,
                    newPlaceholder,
                    ...groupList.filter((conv: any) => conv && conv.id !== targetConversationId)
                  );
                }
                group.expanded = true;
                group.visibleOffset = 0;
                group.visibleLimit = 5;
              }
            }
          } catch (_err) {
            // ignore
          }

          // 目标类型缓存从未加载过时走切换补载：placeholder 会被真实列表全量覆盖，
          // 新创建对话在后端 updated_at 最新仍在顶部，不会重复也不会丢失。
          if (!conversationStore.conversationsCache[targetType].loaded) {
            if (typeof this.handleSidebarConversationTypeChange === 'function') {
              this.handleSidebarConversationTypeChange(targetType).catch(() => {});
            }
          }
        } catch (_e) {
          // ignore
        }

        const pathFragment = this.stripConversationPrefix(targetConversationId);
        // 对话类型不再是路由概念，统一裸路径 /<id>
        history.replaceState({ conversationId: targetConversationId }, '', `/${pathFragment}`);

        // 桌面端标签条：首条消息落地为真实对话，把当前激活的 new 标签转换为 conv 标签
        try {
          const { useConversationTabsStore } = await import('../../../stores/conversationTabs');
          if (!owns()) return false;
          const tabsStore = useConversationTabsStore();
          if (tabsStore.enabled) {
            tabsStore.convertActiveNewTab({
              conversationId: targetConversationId,
              title: t('common.newConversation')
            });
          }
        } catch (_tabsErr) {
          // ignore
        }
      } catch (error) {
        if (!owns()) return false;
        this.uiPushToast({
          title: t('appMessages.sendFailedTitle'),
          message: error?.message || t('appMessages.createNewConversationFailedMessage'),
          type: 'error'
        });
        return false;
      } finally {
        if (owns() && backupToastId) {
          this.uiDismissToast(backupToastId);
          this.versioningInitializingBackupToastId = null;
        }
      }
    }

    if (!owns()) return false;

    // 对账在发送受理结束后恢复，不能用受理前的空快照覆盖乐观消息。
    this.taskInProgress = true;
    const localMessageSource = usePresetText
      ? options?.source === 'runtime_queue_manual_guide'
        ? 'guidance'
        : 'presend'
      : 'user';
    const optimisticUser = this.chatAddUserMessage(
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
    const optimisticAssistant = this.messages[this.currentMessageIndex];
    this.stopRequested = false;
    if (typeof this.monitorShowPendingReply === 'function') {
      this.monitorShowPendingReply();
    }
    if (this.autoScrollEnabled) {
      this.scrollToBottom();
    }

    // 使用 REST API 创建任务（轮询模式）
    try {
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

      const created = await createOwnedMessageTask(
        this,
        session,
        optimisticUser,
        message,
        images,
        videos,
        targetConversationId,
        {
          model_key: this.currentModelKey,
          run_mode: this.runMode,
          thinking_mode: this.thinkingMode,
          message_source: localMessageSource,
          queued_message_id: options?.queuedMessageId || undefined,
          goal_mode: startingGoalMode,
          skill_refs: skillRefs,
          files
        },
        optimisticAssistant
      );

      if (!created || !owns()) return false;
      debugLog('[Message] 任务已创建，开始轮询');
      await this.refreshRunningWorkspaceTasks?.();
      if (!owns()) return false;
    } catch (error) {
      if (!owns()) return false;
      console.error('[Message] 创建任务失败:', error);
      this.uiPushToast({
        title: t('appMessages.sendFailedTitle'),
        message: error.message || t('appMessages.createTaskFailedMessage'),
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
      if (owns()) {
        this.updateCurrentContextTokens();
      }
    }, 1000);
    return true;
  }
};
