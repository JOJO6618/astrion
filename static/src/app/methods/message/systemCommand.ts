// @ts-nocheck
import { debugLog } from '../common';
import { t } from '@/locales';
import { useTaskStore } from '../../../stores/task';
import {
  extractSkillRefsFromMessage,
  SKILL_MARKDOWN_LINK_RE,
} from './shared';

export const systemCommandMethods = {
  async executeSystemCommand(rawCommand, options = {}) {
    const command = (rawCommand || '').toString().trim();
    if (!command) {
      return { success: false, message: t('appMessages.commandEmpty') };
    }

    if (!this.isConnected) {
      if (options.showToast !== false) {
        this.uiPushToast({
          title: t('appMessages.connectionUnavailable'),
          message: t('appMessages.connectionUnavailableMessage'),
          type: 'error'
        });
      }
      return { success: false, message: t('appMessages.connectionUnavailable') };
    }

    try {
      const response = await fetch('/api/commands', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ command })
      });

      const payload = await response.json().catch(() => ({}));
      const result = {
        command: payload.command || command.replace(/^\//, ''),
        success: !!payload.success,
        message: payload.message,
        data: payload.data
      };
      this.handleSystemCommandResult(result, options);
      return result;
    } catch (error) {
      const message = error instanceof Error ? error.message : t('appMessages.commandExecutionFailed');
      const result = {
        command: command.replace(/^\//, ''),
        success: false,
        message
      };
      this.handleSystemCommandResult(result, options);
      return result;
    }
  },
  handleSystemCommandResult(data, options = {}) {
    const showToast = options.showToast !== false;
    if (data.command === 'clear' && data.success) {
      this.logMessageState?.('command_result-clear', { data });
      this.messages = [];
      this.logMessageState?.('command_result-cleared', { data });
      this.currentMessageIndex = -1;
      this.chatClearExpandedBlocks();
      this.resetTokenStatistics();
      if (showToast) {
        this.uiPushToast({
          title: t('appMessages.clearedTitle'),
          message: data.message || t('appMessages.conversationCleared'),
          type: 'success'
        });
      }
      return;
    }

    if (data.command === 'status' && data.success) {
      this.addSystemMessage(`${t('appMessages.systemStatus')}:\n${JSON.stringify(data.data || {}, null, 2)}`);
      if (showToast) {
        this.uiPushToast({
          title: t('appMessages.statusUpdatedTitle'),
          message: t('appMessages.statusFetched'),
          type: 'success'
        });
      }
      return;
    }

    if (!data.success) {
      this.addSystemMessage(`${t('appMessages.commandFailedLabel')}: ${data.message || t('common.unknownError')}`);
      if (showToast) {
        this.uiPushToast({
          title: t('appMessages.commandExecutionFailed'),
          message: data.message || t('common.retryLater'),
          type: 'error'
        });
      }
      return;
    }

    if (showToast) {
      this.uiPushToast({
        title: t('appMessages.commandExecutedTitle'),
        message: data.command || t('appMessages.commandDone'),
        type: 'success'
      });
    }
  }
};
