// @ts-nocheck
import { usePersonalizationStore } from '../../stores/personalization';
import { usePolicyStore } from '../../stores/policy';
import {
  getToolIcon,
  getToolAnimationClass,
  getToolStatusText as baseGetToolStatusText,
  getToolDescription,
  cloneToolArguments,
  buildToolLabel,
  formatSearchTopic,
  formatSearchTime,
  formatSearchDomains,
  getLanguageClass
} from '../../utils/chatDisplay';
import { debugLog } from './common';
import { t } from '@/locales';

export const toolingMethods = {
  toolCategoryIcon(categoryId) {
    if (typeof categoryId === 'string' && categoryId.startsWith('mcp_server__')) {
      return this.toolCategoryIcons.mcp || 'settings';
    }
    return this.toolCategoryIcons[categoryId] || 'settings';
  },

  findMessageByAction(action) {
    if (!action) {
      return null;
    }
    for (const message of this.messages) {
      if (!message.actions) {
        continue;
      }
      if (message.actions.includes(action)) {
        return message;
      }
    }
    return null;
  },

  cleanupStaleToolActions() {
    this.messages.forEach((msg) => {
      if (!msg.actions) {
        return;
      }
      msg.actions.forEach((action) => {
        if (action.type !== 'tool' || !action.tool) {
          return;
        }
        if (['running', 'preparing'].includes(action.tool.status)) {
          action.tool.status = 'stale';
          action.tool.message = action.tool.message || t('appTasks.interruptedByNewResponse');
          this.toolUnregisterAction(action);
        }
      });
    });
    this.preparingTools.clear();
    this.toolActionIndex.clear();
  },

  clearPendingTools(reason = 'unspecified') {
    this.messages.forEach((msg) => {
      if (!msg.actions) {
        return;
      }
      msg.actions.forEach((action) => {
        if (action.type !== 'tool' || !action.tool) {
          return;
        }
        const status =
          typeof action.tool.status === 'string' ? action.tool.status.toLowerCase() : '';
        if (!status || ['preparing', 'running', 'pending', 'queued', 'stale', 'awaiting_user_answer'].includes(status)) {
          action.tool.status = 'cancelled';
          action.tool.message = action.tool.message || t('appTasks.stopped');
        }
      });
    });
    if (this.preparingTools && this.preparingTools.clear) {
      this.preparingTools.clear();
    }
    if (this.activeTools && this.activeTools.clear) {
      this.activeTools.clear();
    }
    if (this.toolActionIndex && this.toolActionIndex.clear) {
      this.toolActionIndex.clear();
    }
    if (this.toolStacks && this.toolStacks.clear) {
      this.toolStacks.clear();
    }
    if (typeof this.toolResetTracking === 'function') {
      this.toolResetTracking();
    }
    debugLog('清理待处理工具', { reason });
  },

  hasPendingToolActions() {
    const mapHasEntries = (map) => map && typeof map.size === 'number' && map.size > 0;
    if (mapHasEntries(this.preparingTools) || mapHasEntries(this.activeTools)) {
      return true;
    }
    if (!Array.isArray(this.messages)) {
      return false;
    }
    return this.messages.some((msg) => {
      if (!msg || msg.role !== 'assistant' || !Array.isArray(msg.actions)) {
        return false;
      }
      return msg.actions.some((action) => {
        if (!action || action.type !== 'tool' || !action.tool) {
          return false;
        }
        if (action.tool.awaiting_content) {
          return true;
        }
        const status =
          typeof action.tool.status === 'string' ? action.tool.status.toLowerCase() : '';
        return !status || ['preparing', 'running', 'pending', 'queued', 'awaiting_user_answer'].includes(status);
      });
    });
  },

  maybeResetStreamingState(reason = 'unspecified') {
    if (!this.streamingMessage) {
      return false;
    }
    if (this.hasPendingToolActions()) {
      return false;
    }
    this.streamingMessage = false;
    this.stopRequested = false;
    debugLog('流式状态已结束', { reason });
    return true;
  },

  applyToolSettingsSnapshot(categories) {
    if (!Array.isArray(categories)) {
      console.warn('[ToolSettings] Snapshot skipped: categories not array', categories);
      return;
    }
    const normalized = categories.map((item) => ({
      id: item.id,
      label: item.label || item.id,
      enabled: !!item.enabled,
      tools: Array.isArray(item.tools) ? item.tools : [],
      locked: !!item.locked,
      locked_state: typeof item.locked_state === 'boolean' ? item.locked_state : null
    }));

    let ordered = normalized;
    const mcpServerCategories = normalized
      .filter((item) => typeof item.id === 'string' && item.id.startsWith('mcp_server__'))
      .sort((a, b) => String(a.label || '').localeCompare(String(b.label || '')));
    if (mcpServerCategories.length > 0) {
      const nonMcpServer = normalized.filter(
        (item) => !(typeof item.id === 'string' && item.id.startsWith('mcp_server__'))
      );
      const mcpIndex = nonMcpServer.findIndex((item) => item.id === 'mcp');
      if (mcpIndex >= 0) {
        ordered = [
          ...nonMcpServer.slice(0, mcpIndex + 1),
          ...mcpServerCategories,
          ...nonMcpServer.slice(mcpIndex + 1)
        ];
      } else {
        ordered = [...mcpServerCategories, ...nonMcpServer];
      }
    }

    debugLog('[ToolSettings] Snapshot applied', {
      received: categories.length,
      normalized: ordered,
      anyEnabled: ordered.some((cat) => cat.enabled),
      toolExamples: ordered.slice(0, 3)
    });
    this.toolSetSettings(ordered);
    this.toolSetSettingsLoading(false);
  },

  async loadToolSettings(force = false) {
    if (!this.isConnected && !force) {
      debugLog('[ToolSettings] Skip load: disconnected & not forced');
      return;
    }
    if (this.toolSettingsLoading) {
      debugLog('[ToolSettings] Skip load: already loading');
      return;
    }
    if (!force && this.toolSettings.length > 0) {
      debugLog('[ToolSettings] Skip load: already have settings');
      return;
    }
    debugLog('[ToolSettings] Fetch start', { force, hasConnection: this.isConnected });
    this.toolSetSettingsLoading(true);
    try {
      const response = await fetch('/api/tool-settings');
      const data = await response.json();
      debugLog('[ToolSettings] Fetch response', { status: response.status, data });
      if (response.ok && data.success && Array.isArray(data.categories)) {
        this.applyToolSettingsSnapshot(data.categories);
      } else {
        console.warn('获取工具设置失败:', data);
        this.toolSetSettingsLoading(false);
      }
    } catch (error) {
      console.error('获取工具设置异常:', error);
      this.toolSetSettingsLoading(false);
    }
  },

  async updateToolCategory(categoryId, enabled) {
    if (!this.isConnected) {
      return;
    }
    if (this.toolSettingsLoading) {
      return;
    }
    const policyStore = usePolicyStore();
    if (policyStore.isCategoryLocked(categoryId)) {
      this.uiPushToast({
        title: t('appTasks.cannotModify'),
        message: t('appTasks.categoryEnforcedByAdmin'),
        type: 'warning'
      });
      return;
    }
    const previousSnapshot = this.toolSettings.map((item) => ({ ...item }));
    const updatedSettings = this.toolSettings.map((item) => {
      if (item.id === categoryId) {
        return { ...item, enabled };
      }
      return item;
    });
    this.toolSetSettings(updatedSettings);
    try {
      const response = await fetch('/api/tool-settings', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          category: categoryId,
          enabled
        })
      });
      const data = await response.json();
      if (response.ok && data.success && Array.isArray(data.categories)) {
        this.applyToolSettingsSnapshot(data.categories);
      } else {
        console.warn('更新工具设置失败:', data);
        if (data && (data.message || data.error)) {
          this.uiPushToast({
            title: t('appTasks.cannotSwitchTool'),
            message: data.message || data.error,
            type: 'warning'
          });
        }
        this.toolSetSettings(previousSnapshot);
      }
    } catch (error) {
      console.error('更新工具设置异常:', error);
      this.toolSetSettings(previousSnapshot);
    }
    this.toolSetSettingsLoading(false);
  },

  toggleToolMenu() {
    if (!this.isConnected) {
      return;
    }
    if (this.isPolicyBlocked('block_tool_toggle', t('appTasks.toolToggleLockedByAdmin'))) {
      return;
    }
    this.modeMenuOpen = false;
    this.modelMenuOpen = false;
    const nextState = this.inputToggleToolMenu();
    if (nextState) {
      this.inputSetSettingsOpen(false);
      if (!this.quickMenuOpen) {
        this.inputOpenQuickMenu();
      }
      this.loadToolSettings(true);
    } else {
      this.inputSetToolMenuOpen(false);
    }
  },

  getToolIcon,
  getToolAnimationClass,
  getToolStatusText(tool: any) {
    const personalization = usePersonalizationStore();
    const intentEnabled =
      personalization?.form?.tool_intent_enabled ?? personalization?.tool_intent_enabled ?? true;
    return baseGetToolStatusText(tool, { intentEnabled });
  },
  getToolDescription,
  cloneToolArguments,
  buildToolLabel,
  formatSearchTopic,
  formatSearchTime,
  formatSearchDomains,
  getLanguageClass
};
