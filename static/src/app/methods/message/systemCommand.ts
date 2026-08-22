// @ts-nocheck
import { debugLog } from '../common';
import { useTaskStore } from '../../../stores/task';
import {
  extractSkillRefsFromMessage,
  SKILL_MARKDOWN_LINK_RE,
} from './shared';

export const systemCommandMethods = {
  async executeSystemCommand(rawCommand, options = {}) {
    const command = (rawCommand || '').toString().trim();
    if (!command) {
      return { success: false, message: '命令不能为空' };
    }

    if (!this.isConnected) {
      if (options.showToast !== false) {
        this.uiPushToast({
          title: '连接不可用',
          message: '当前无法执行命令，请稍后重试。',
          type: 'error'
        });
      }
      return { success: false, message: '连接不可用' };
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
      const message = error instanceof Error ? error.message : '命令执行失败';
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
          title: '已清除',
          message: data.message || '对话已清除',
          type: 'success'
        });
      }
      return;
    }

    if (data.command === 'status' && data.success) {
      this.addSystemMessage(`系统状态:\n${JSON.stringify(data.data || {}, null, 2)}`);
      if (showToast) {
        this.uiPushToast({
          title: '状态已更新',
          message: '已获取系统状态',
          type: 'success'
        });
      }
      return;
    }

    if (!data.success) {
      this.addSystemMessage(`命令失败: ${data.message || '未知错误'}`);
      if (showToast) {
        this.uiPushToast({
          title: '命令执行失败',
          message: data.message || '请稍后重试',
          type: 'error'
        });
      }
      return;
    }

    if (showToast) {
      this.uiPushToast({
        title: '命令已执行',
        message: data.command || '完成',
        type: 'success'
      });
    }
  }
};
