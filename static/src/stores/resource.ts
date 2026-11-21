import {
  beginAuxiliaryStoreRequest,
  invalidateAuxiliaryRequest
} from '../app/methods/auxiliaryOwnership';
import { defineStore } from 'pinia';
import type { ConversationCosts } from '@/components/token/costTypes';
import { workspaceScopedFetch as fetch } from '../app/methods/workspaceRequestScope';

interface ConversationTokens {
  costs?: ConversationCosts | null;
  cumulative_input_tokens: number;
  cumulative_output_tokens: number;
  cumulative_total_tokens: number;
  cumulative_cached_input_tokens: number;
  cache_exempt_input_tokens: number;
}

/**
 * 资源状态（Host-only）。
 *
 * 主仓不再有多用户配额、容器指标和项目配额，因此这里只保留对话级 token 统计。
 * 容器/项目/配额轮询已随 Docker 分支一并移除。
 */
export const useResourceStore = defineStore('resource', {
  state: () => ({
    tokenPanelCollapsed: true,
    currentContextTokens: 0,
    // 对话固定开销基线（自适应压缩阈值：分母 = 配置阈值 + 基线）
    contextBaselineTokens: 0,
    // 对话级深压缩阈值（创建时快照，圆环卡片可改；null=老对话未快照）
    compressLimitTokens: null as number | null,
    compressLimitSource: 'missing' as 'snapshot' | 'custom' | 'missing',
    currentConversationTokens: {
      cumulative_input_tokens: 0,
      cumulative_output_tokens: 0,
      cumulative_total_tokens: 0,
      cumulative_cached_input_tokens: 0,
      cache_exempt_input_tokens: 0
    } as ConversationTokens
  }),
  actions: {
    resetTokenStatistics() {
      invalidateAuxiliaryRequest(this, 'token-statistics');
      invalidateAuxiliaryRequest(this, 'context-tokens');
      this.currentContextTokens = 0;
      this.contextBaselineTokens = 0;
      this.compressLimitTokens = null;
      this.compressLimitSource = 'missing';
      this.currentConversationTokens = {
        cumulative_input_tokens: 0,
        cumulative_output_tokens: 0,
        cumulative_total_tokens: 0,
        cumulative_cached_input_tokens: 0,
        cache_exempt_input_tokens: 0
      };
    },
    setCurrentContextTokens(value: number) {
      invalidateAuxiliaryRequest(this, 'context-tokens');
      this.currentContextTokens = value || 0;
    },
    setContextBaselineTokens(value: number) {
      this.contextBaselineTokens = Number(value) > 0 ? Number(value) : 0;
    },
    setCompressLimit(tokens: number | null, source: string) {
      this.compressLimitTokens = Number(tokens) > 0 ? Number(tokens) : null;
      this.compressLimitSource = source === 'custom' || source === 'snapshot' ? source : 'missing';
    },
    toggleTokenPanel() {
      this.tokenPanelCollapsed = !this.tokenPanelCollapsed;
    },
    async updateCurrentContextTokens(conversationId: string | null) {
      const owns = beginAuxiliaryStoreRequest(this, 'context-tokens', conversationId);
      if (!conversationId) {
        this.currentContextTokens = 0;
        return;
      }
      try {
        const response = await fetch(`/api/conversations/${conversationId}/tokens`);
        const data = await response.json();
        if (!owns()) return;
        if (data.success && data.data) {
          this.currentContextTokens = data.data.total_tokens || 0;
        } else {
          this.currentContextTokens = 0;
        }
      } catch (error) {
        if (!owns()) return;
        console.warn('获取当前上下文Token异常:', error);
        this.currentContextTokens = 0;
      }
    },
    async fetchConversationTokenStatistics(conversationId: string | null) {
      const owns = beginAuxiliaryStoreRequest(this, 'token-statistics', conversationId);
      if (!conversationId) {
        this.resetTokenStatistics();
        return;
      }
      try {
        const response = await fetch(`/api/conversations/${conversationId}/token-statistics`);
        const data = await response.json();
        if (!owns()) return;
        if (data.success && data.data) {
          this.currentConversationTokens.cumulative_input_tokens =
            data.data.total_input_tokens || 0;
          this.currentConversationTokens.cumulative_output_tokens =
            data.data.total_output_tokens || 0;
          this.currentConversationTokens.cumulative_total_tokens = data.data.total_tokens || 0;
          this.currentConversationTokens.cumulative_cached_input_tokens =
            data.data.total_cached_input_tokens || 0;
          this.currentConversationTokens.cache_exempt_input_tokens =
            data.data.cache_exempt_input_tokens || 0;
          this.currentConversationTokens.costs = data.data.costs || null;
          if (typeof data.data.current_context_tokens === 'number') {
            this.currentContextTokens = data.data.current_context_tokens;
          }
        }
      } catch (error) {
        if (!owns()) return;
        console.warn('获取Token统计异常:', error);
      }
    }
  }
});
