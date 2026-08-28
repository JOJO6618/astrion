import { defineStore } from 'pinia';
import { t } from '@/locales';

export type ModelKey = string;

export interface ModelOption {
  key: ModelKey;
  label: string;
  description: string;
  visible?: boolean;
  multimodal?: string;
  supportsImage?: boolean;
  supportsVideo?: boolean;
  contextWindow?: number | null;
  maxOutputTokens?: number | null;
  fastOnly: boolean;
  supportsThinking: boolean;
  thinkingOnly?: boolean;
  supportsReasoningEffort?: boolean;
}

interface ModelState {
  currentModelKey: ModelKey;
  models: ModelOption[];
}

export const useModelStore = defineStore('model', {
  state: (): ModelState => ({
    currentModelKey: '',
    models: []
  }),
  getters: {
    currentModel(state): ModelOption | null {
      return state.models.find((m) => m.key === state.currentModelKey) || state.models[0] || null;
    }
  },
  actions: {
    setModels(models: ModelOption[]) {
      if (!Array.isArray(models)) return;
      this.models = models;
      const exists = this.currentModelKey && this.models.some((m) => m.key === this.currentModelKey);
      if (!exists) {
        this.currentModelKey = this.models[0]?.key || '';
      }
    },
    setModel(key: ModelKey) {
      if (this.currentModelKey === key) return;
      const exists = this.models.some((m) => m.key === key);
      if (exists) {
        this.currentModelKey = key;
        return;
      }
      // 如果列表中还没有该模型（例如 fetchModels 失败），
      // 临时插入一个 fallback 项，避免首屏显示“未选择模型”
      if (key) {
        this.models = [
          ...this.models,
          {
            key,
            label: key,
            description: '',
            fastOnly: false,
            supportsThinking: true
          }
        ];
        this.currentModelKey = key;
      }
    },
    async fetchModels() {
      const resp = await fetch('/api/v1/models', { cache: 'no-store' });
      const data = await resp.json();
      if (!resp.ok || !data?.success || !Array.isArray(data.items)) {
        throw new Error(data?.error || t('stores.loadModelListFailed'));
      }
      const mapped: ModelOption[] = data.items
        .filter((item: any) => item && item.model_key)
        .map((item: any) => {
          const multimodal = String(item.multimodal || 'none');
          const supportsImage = multimodal === 'image' || multimodal === 'image,video';
          const supportsVideo = multimodal === 'video' || multimodal === 'image,video';
          return {
            key: String(item.model_key),
            label: String(item.name || item.model_key),
            description: String(item.description || ''),
            visible: item.visible !== false,
            multimodal,
            supportsImage,
            supportsVideo,
            contextWindow: typeof item.context_window === 'number' ? item.context_window : null,
            maxOutputTokens:
              typeof item.max_output_tokens === 'number' ? item.max_output_tokens : null,
            fastOnly: !!item.fast_only,
            supportsThinking: !!item.supports_thinking,
            thinkingOnly: !!item.thinking_only,
            supportsReasoningEffort: !!item.supports_reasoning_effort
          };
        });
      this.setModels(mapped);
    }
  }
});
