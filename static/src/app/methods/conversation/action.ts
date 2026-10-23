// @ts-nocheck
import { debugLog } from '../common';
import { t } from '@/locales';
import './shared';
import { currentConversationSession } from './session';
import { useConversationTabsStore } from '../../../stores/conversationTabs';

export const actionMethods = {
  promoteConversationToTop(conversationId) {
    if (!Array.isArray(this.conversations) || !conversationId) {
      return;
    }
    const index = this.conversations.findIndex((conv) => conv && conv.id === conversationId);
    if (index > 0) {
      const [selected] = this.conversations.splice(index, 1);
      this.conversations.unshift(selected);
    }
  },
  async createNewConversation() {
    const workspaceId = String(this.currentHostWorkspaceId || '');
    const tabs = useConversationTabsStore();
    if (tabs.enabled) {
      const workspace = (this.hostWorkspaces || []).find(
        (item) => item.workspace_id === workspaceId
      );
      tabs.addNewTab({ workspaceId, workspaceLabel: workspace?.label || '' });
    }
    return this.enterNewConversationPage({ workspaceId });
  },
  async createWorkspaceConversation(workspaceId: string) {
    if (!workspaceId) return;
    const tabs = useConversationTabsStore();
    if (tabs.enabled) {
      const workspace = (this.hostWorkspaces || []).find(
        (item) => item.workspace_id === workspaceId
      );
      tabs.addNewTab({ workspaceId, workspaceLabel: workspace?.label || '' });
    }
    return this.enterNewConversationPage({ workspaceId });
  },
  async deleteConversation(payload) {
    const conversationId = typeof payload === 'string' ? payload : payload?.id;
    const workspaceId = typeof payload === 'object' ? payload?.workspaceId : undefined;
    if (!conversationId) return;
    const confirmed = await this.confirmAction({
      title: t('appMessages.deleteConversationTitle'),
      message: t('appMessages.deleteConversationConfirmMessage'),
      confirmText: t('common.delete'),
      cancelText: t('common.cancel')
    });
    if (!confirmed) {
      return;
    }

    const waitForAnimation = (durationMs) =>
      new Promise((resolve) => {
        window.setTimeout(resolve, durationMs);
      });

    // 确认弹窗自身有 250ms 退出动画；等弹窗完全消失后，再开始列表删除动画。
    await waitForAnimation(270);

    debugLog('删除对话:', conversationId);
    this.logMessageState('deleteConversation:start', { conversationId });

    try {
      const deleteUrl = workspaceId
        ? `/api/conversations/${conversationId}?workspace_id=${encodeURIComponent(workspaceId)}`
        : `/api/conversations/${conversationId}`;
      const response = await fetch(deleteUrl, {
        method: 'DELETE'
      });

      const result = await response.json();

      if (result.success) {
        debugLog('对话删除成功');

        // 如果删除的是当前对话，清空界面
        if (
          conversationId === this.currentConversationId ||
          currentConversationSession(this)?.conversationId === conversationId
        ) {
          this.leaveConversationView();
          this.clearLocalTaskUiState?.(`delete-conversation:${conversationId}`);
          this.logMessageState('deleteConversation:before-clear', { conversationId });
          this.messages = [];
          this.logMessageState('deleteConversation:after-clear', { conversationId });
          this.currentConversationId = null;
          this.currentConversationTitle = '';
          this.resetAllStates(`deleteConversation:${conversationId}`);
          this.resetTokenStatistics();
          history.replaceState({}, '', '/new');
          await this.enterNewConversationPage();
        }

        // 先给目标项加 deleting 类执行左滑动画，再从本地列表移除。
        // 这比完全依赖 transition-group leave 更稳定：即使列表即将为空或分支切换，也能先播放离场动画。
        this.conversationListAnimationMode = 'delete';
        const deletingIds = Array.isArray(this.pendingDeletingConversationIds)
          ? this.pendingDeletingConversationIds
          : [];
        if (!deletingIds.includes(conversationId)) {
          this.pendingDeletingConversationIds = [...deletingIds, conversationId];
        }
        await this.$nextTick();
        // 分组侧边栏使用 transition-group 离场动画，立即从列表移除
        try {
          const { useConversationStore } = await import('../../../stores/conversation');
          const conversationStore = useConversationStore();
          conversationStore.workspaceGroups.forEach((group: any) => {
            group.conversations.splice(
              0,
              group.conversations.length,
              ...group.conversations.filter((conv: any) => conv.id !== conversationId)
            );
            const maxVisibleOffset = Math.max(0, group.conversations.length - group.visibleLimit);
            if (group.visibleOffset > maxVisibleOffset) {
              group.visibleOffset = maxVisibleOffset;
            }
          });
        } catch (_err) {
          // ignore
        }
        await waitForAnimation(430);

        this.conversations.splice(
          0,
          this.conversations.length,
          ...this.conversations.filter((conversation) => conversation.id !== conversationId)
        );
        this.searchResults = this.searchResults.filter(
          (conversation) => conversation.id !== conversationId
        );
        // 跨工作区搜索结果分组：同步剔除被删对话，空组整体移除
        this.searchGroups = (Array.isArray(this.searchGroups) ? this.searchGroups : [])
          .map((group: any) => ({
            ...group,
            results: (group.results || []).filter((conv: any) => conv.id !== conversationId)
          }))
          .filter((group: any) => group.results.length > 0);
        this.pendingDeletingConversationIds = (
          Array.isArray(this.pendingDeletingConversationIds)
            ? this.pendingDeletingConversationIds
            : []
        ).filter((id) => id !== conversationId);

        // 等待“左滑离场 + 0.1s 后集体上移”动画结束，避免刷新列表打断过渡。
        await waitForAnimation(180);

        // 刷新对话列表
        this.conversationsOffset = 0;
        await this.loadConversationsList();
        this.conversationListAnimationMode = 'idle';
      } else {
        console.error('删除对话失败:', result.message);
        this.uiPushToast({
          title: t('appMessages.deleteConversationFailedTitle'),
          message: result.message || t('appMessages.serverNotSuccessMessage'),
          type: 'error'
        });
      }
    } catch (error) {
      this.pendingDeletingConversationIds = (
        Array.isArray(this.pendingDeletingConversationIds)
          ? this.pendingDeletingConversationIds
          : []
      ).filter((id) => id !== conversationId);
      this.conversationListAnimationMode = 'idle';
      console.error('删除对话异常:', error);
      this.uiPushToast({
        title: t('appMessages.deleteConversationErrorTitle'),
        message: error.message || String(error),
        type: 'error'
      });
    }
  },
  async duplicateConversation(conversationId) {
    debugLog('复制对话:', conversationId);
    try {
      const response = await fetch(`/api/conversations/${conversationId}/duplicate`, {
        method: 'POST'
      });

      const result = await response.json();

      if (response.ok && result.success) {
        const newId = result.duplicate_conversation_id;
        const waitForAnimation = (durationMs) =>
          new Promise((resolve) => {
            window.setTimeout(resolve, durationMs);
          });
        if (newId) {
          const sourceIndex = this.conversations.findIndex(
            (conv) => conv && conv.id === conversationId
          );
          const sourceConversation = sourceIndex >= 0 ? this.conversations[sourceIndex] : null;
          const duplicateTitle =
            result.load_result?.title ||
            sourceConversation?.title ||
            t('appMessages.duplicateConversationTitle');
          const duplicatePlaceholder = {
            id: newId,
            title: duplicateTitle,
            updated_at: new Date().toISOString(),
            total_messages: sourceConversation?.total_messages || 0,
            total_tools: sourceConversation?.total_tools || 0
          };

          this.conversationInsertAnimations = {
            ...this.conversationInsertAnimations,
            [conversationId]: 'duplicateSource',
            [newId]: 'duplicate'
          };
          this.conversationListAnimationMode = 'duplicate';

          const withoutDuplicate = this.conversations.filter((conv) => conv && conv.id !== newId);
          const insertIndex = withoutDuplicate.findIndex(
            (conv) => conv && conv.id === conversationId
          );
          if (insertIndex >= 0) {
            this.conversations.splice(
              0,
              this.conversations.length,
              ...withoutDuplicate.slice(0, insertIndex + 1),
              duplicatePlaceholder,
              ...withoutDuplicate.slice(insertIndex + 1)
            );
          } else {
            this.conversations.splice(
              0,
              this.conversations.length,
              duplicatePlaceholder,
              ...withoutDuplicate
            );
          }

          window.setTimeout(() => {
            const rest = { ...this.conversationInsertAnimations };
            delete rest[conversationId];
            delete rest[newId];
            this.conversationInsertAnimations = rest;
            this.conversationListAnimationMode = 'idle';
          }, 860);

          // 同步插入分组侧边栏对应工作区
          try {
            const { useConversationStore } = await import('../../../stores/conversation');
            const conversationStore = useConversationStore();
            const group = conversationStore.workspaceGroups.find((g: any) =>
              g.conversations.some((conv: any) => conv.id === conversationId)
            );
            if (group) {
              const sourceGroupIndex = group.conversations.findIndex(
                (conv: any) => conv.id === conversationId
              );
              const withoutDuplicate = group.conversations.filter((conv: any) => conv.id !== newId);
              if (sourceGroupIndex >= 0) {
                group.conversations.splice(
                  0,
                  group.conversations.length,
                  ...withoutDuplicate.slice(0, sourceGroupIndex + 1),
                  duplicatePlaceholder,
                  ...withoutDuplicate.slice(sourceGroupIndex + 1)
                );
              } else {
                group.conversations.splice(
                  0,
                  group.conversations.length,
                  duplicatePlaceholder,
                  ...withoutDuplicate
                );
              }
              group.expanded = true;
            }
          } catch (_err) {
            // ignore
          }

          // 先让“目标下方钻出 + 下方整体下移”动画播完，再加载复制出的对话。
          await waitForAnimation(860);
          await this.handleSelectWorkspaceConversation(
            {
              conversationId: String(newId || ''),
              workspaceId: String(this.currentHostWorkspaceId || '')
            },
            { force: true, preserveListPosition: true }
          );
        }
      } else {
        const message = result.message || result.error || t('common.copyFailed');
        this.uiPushToast({
          title: t('appMessages.duplicateConversationFailedTitle'),
          message,
          type: 'error'
        });
      }
    } catch (error) {
      console.error('复制对话异常:', error);
      this.uiPushToast({
        title: t('appMessages.duplicateConversationErrorTitle'),
        message: error.message || String(error),
        type: 'error'
      });
    }
  }
};
