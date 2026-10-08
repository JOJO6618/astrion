// @ts-nocheck
import { debugLog } from '../common';
import { useConversationStore } from '../../../stores/conversation';
import { useConversationTabsStore } from '../../../stores/conversationTabs';
import { beginAuxiliaryRequest } from '../auxiliaryOwnership';
import { currentConversationSession, ownsConversationSession } from '../conversation/session';

export const titleMethods = {
  applyConversationTitleUpdate(conversationId: string, title: string, source = 'unknown') {
    const normalizedTitle = String(title || '').trim();
    const normalizedConversationId = String(conversationId || '').trim();
    if (!normalizedConversationId || this.isPlaceholderConversationTitle(normalizedTitle)) {
      return false;
    }

    let changed = false;
    if (normalizedConversationId === this.currentConversationId) {
      if (this.currentConversationTitle !== normalizedTitle) {
        // 确保 watcher 能从“新对话”切到真实标题并播放现有标题打字动画。
        this.suppressTitleTyping = false;
        this.titleReady = true;
        this.currentConversationTitle = normalizedTitle;
        changed = true;
      }
    }

    changed =
      useConversationStore().updateConversationTitle(normalizedConversationId, normalizedTitle) ||
      changed;
    useConversationTabsStore().updateTitle(normalizedConversationId, normalizedTitle);

    if (changed) {
      debugLog('[TaskPolling] 已同步生成后的对话标题:', {
        conversationId: normalizedConversationId,
        title: normalizedTitle,
        source
      });
      this.$forceUpdate();
    }
    return changed;
  },
  scheduleGeneratedTitleRefresh(reason = 'unknown', options: any = {}) {
    const conversationId = String(
      options.conversationId || this.currentConversationId || ''
    ).trim();
    const session = currentConversationSession(this);
    if (!conversationId || !session) return;
    const owns = () =>
      ownsConversationSession(this, session) &&
      this.currentConversationId === conversationId &&
      this.generatedTitleRefreshSeq === seq;

    // 标题模型是后台独立请求，慢时可能在主任务完成后几十秒才落盘。
    // 这里用“总等待窗口”而不是少量固定次数，避免刚好错过最终 title_locked=true 的标题。
    const deadlineMs = Math.max(15000, Number(options.deadlineMs || 90000));
    const maxAttempts = Math.max(1, Number(options.maxAttempts || 60));
    const delays = Array.isArray(options.delays)
      ? options.delays
      : [0, 800, 1200, 1600, 2200, 3000, 4000, 5000, 6000];
    const startedAt = Date.now();
    const seq = Number(this.generatedTitleRefreshSeq || 0) + 1;
    this.generatedTitleRefreshSeq = seq;
    if (this.generatedTitleRefreshTimer) {
      clearTimeout(this.generatedTitleRefreshTimer);
      this.generatedTitleRefreshTimer = null;
    }

    const run = async (attempt: number) => {
      if (!owns()) return;
      this.generatedTitleRefreshTimer = null;
      const updated = await this.fetchGeneratedConversationTitle(
        conversationId,
        `${reason}:attempt-${attempt}`
      );
      if (!owns() || updated) return;
      const elapsedMs = Date.now() - startedAt;
      if (attempt >= maxAttempts || elapsedMs >= deadlineMs) {
        debugLog('[TaskPolling] 停止等待生成标题', {
          reason,
          conversationId,
          attempt,
          elapsedMs,
          deadlineMs
        });
        return;
      }
      const delay = Number(delays[Math.min(attempt, delays.length - 1)] || 1000);
      const nextDelay = Math.min(delay, Math.max(0, deadlineMs - elapsedMs));
      this.generatedTitleRefreshTimer = window.setTimeout(() => {
        void run(attempt + 1);
      }, nextDelay);
    };

    void run(1);
  },
  async fetchGeneratedConversationTitle(conversationId: string, source = 'unknown') {
    const normalizedConversationId = String(conversationId || '').trim();
    if (!normalizedConversationId || normalizedConversationId !== this.currentConversationId) {
      return false;
    }
    const session = currentConversationSession(this);
    if (!session || !ownsConversationSession(this, session)) return false;
    const owns = beginAuxiliaryRequest(this, 'generated-title');
    const workspaceId = session.workspaceId || this.currentHostWorkspaceId || '';
    const query = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : '';
    try {
      const response = await fetch(
        `/api/conversations/${encodeURIComponent(normalizedConversationId)}/metadata${query}`,
        { cache: 'no-store', signal: session.controller.signal }
      );
      if (!owns()) return false;
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const result = await response.json();
      if (!owns()) return false;
      const data = result?.data || {};
      if (!result?.success || data.id !== normalizedConversationId) {
        return false;
      }
      const normalizedTitle = String(data.title || '').trim();
      const titleLocked = Boolean(data.title_locked || data.metadata?.title_locked);
      if (this.isPlaceholderConversationTitle(normalizedTitle) || !titleLocked) {
        // Unlocked titles are usually first-message fallback titles; keep waiting for the final generated title.
        return false;
      }
      this.applyConversationTitleUpdate(normalizedConversationId, normalizedTitle, source);
      return true;
    } catch (error) {
      if (owns() && error?.name !== 'AbortError') {
        console.warn('[TaskPolling] 同步生成后的对话标题失败:', error);
      }
      return false;
    }
  }
};
