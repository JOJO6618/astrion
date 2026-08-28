// @ts-nocheck
// 运行模式（work_mode：plan/ask/execute）前端方法。
// 与权限/执行环境/网络权限三件套同构：对话级 POST（携带 conversation_id）、
// 空闲才可切换（运行中后端返回 409）、切换结果由后端注入系统通知。
// 联动规则：plan 档锁定权限为只读（UI 禁用 + 后端强制）；
// submit_plan 批准后后端自动切到 execute，前端经 plan_approval_resolved 刷新。
import { t, currentLocale } from '@/locales';

export const workModeMethods = {
  getWorkModeLabel(mode) {
    void currentLocale.value;
    const options = Array.isArray(this.workModeOptions) ? this.workModeOptions : [];
    const hit = options.find((item) => item.value === mode);
    return hit ? hit.label : mode || t('appUi.unknown');
  },
  async fetchWorkMode() {
    try {
      const query = this.currentConversationId
        ? `?conversation_id=${encodeURIComponent(this.currentConversationId)}`
        : '';
      const response = await fetch(`/api/work-mode${query}`);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload?.success) {
        return;
      }
      if (typeof payload.mode === 'string') {
        this.currentWorkMode = payload.mode;
      }
      // plan 档下权限被锁定为只读、执行环境被锁定为沙箱，同步刷新显示
      if (typeof payload.permission_mode === 'string' && payload.permission_mode) {
        this.currentPermissionMode = payload.permission_mode;
      }
      if (typeof payload.execution_mode === 'string' && payload.execution_mode) {
        this.currentExecutionMode = payload.execution_mode;
      }
    } catch (_error) {
      // ignore
    }
  },
  async changeWorkMode(mode) {
    const target = String(mode || '')
      .trim()
      .toLowerCase();
    if (!target) {
      this.closeWorkModeMenu();
      return;
    }
    try {
      const response = await fetch('/api/work-mode', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          mode: target,
          // 对话级隔离：与权限模式一致，携带当前对话 ID；
          // /new 页面无对话时回退到工作区级 terminal（新对话创建时继承）。
          ...(this.currentConversationId ? { conversation_id: this.currentConversationId } : {})
        })
      });
      const payload = await response.json().catch(() => ({}));
      if (response.status === 409) {
        throw new Error(payload?.message || t('appUi.workModeRunningMessage'));
      }
      if (!response.ok || !payload?.success) {
        throw new Error(payload?.message || payload?.error || t('appUi.switchRunModeFailed'));
      }
      if (typeof payload?.mode === 'string') {
        this.currentWorkMode = payload.mode;
      }
      // 联动：plan 锁只读+沙箱 / 离开 plan 恢复，后端返回最新权限与执行环境
      if (typeof payload?.permission_mode === 'string' && payload.permission_mode) {
        this.currentPermissionMode = payload.permission_mode;
      } else {
        this.fetchPermissionMode();
      }
      if (typeof payload?.execution_mode === 'string' && payload.execution_mode) {
        this.currentExecutionMode = payload.execution_mode;
      } else {
        this.fetchExecutionMode();
      }
      const labelMap: Record<string, string> = { plan: t('appUi.workModePlan'), ask: t('appUi.workModeAsk'), execute: t('appUi.workModeExecute') };
      this.uiPushToast({
        title: t('appUi.runModeUpdated'),
        message: payload?.message || t('appUi.switchedToMode', { mode: labelMap[this.currentWorkMode] || this.currentWorkMode }),
        type: 'info',
        duration: 1800
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error || t('appUi.switchRunModeFailed'));
      this.uiPushToast({
        title: t('appUi.switchRunModeFailed'),
        message: msg,
        type: 'error'
      });
    } finally {
      this.closeWorkModeMenu();
    }
  },
  toggleWorkModeMenu() {
    if (!this.isConnected || this.streamingMessage) {
      return;
    }
    const next = !this.workModeMenuOpen;
    this.workModeMenuOpen = next;
    if (next) {
      // 与其他弹出菜单互斥（仿 handleToggleAgentTypeMenu）
      this.permissionMenuOpen = false;
      this.modelMenuOpen = false;
      this.modeMenuOpen = false;
      this.agentTypeMenuOpen = false;
      this.inputCloseMenus?.();
    }
  },
  closeWorkModeMenu() {
    this.workModeMenuOpen = false;
  }
};
