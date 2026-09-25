import { defineStore } from 'pinia';
import { t } from '@/locales';

export type ModelKey = string;

export interface ReasoningLevel {
  effort: string;
  description: string;
}

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
  /** 'codex' = ChatGPT 订阅通道；'provider' = 提供商同步；其余/缺省为手写自定义 */
  providerType?: string | null;
  /** 协议标识（2026-09-25 泛化）：'chat_completions'（缺省）/ 'responses' / 其他暂不支持 */
  apiProtocol?: string | null;
  /** 提供商同步模型的分组字段（手写 custom / Codex 为 null） */
  providerId?: string | null;
  providerName?: string | null;
  /** Codex 动态模型的 reasoning 档位（用于 EffortSlider 过滤） */
  supportedReasoningLevels?: ReasoningLevel[] | null;
  defaultReasoningLevel?: string | null;
}

interface ModelState {
  currentModelKey: ModelKey;
  /**
   * 全量注册表模型（含被用户隐藏的项）。
   * 内部查表（currentModel / setModel 存在性判断）一律以它为准，
   * 保证「隐藏 = 仅选择器不显示」的语义，不影响已选中的当前模型。
   */
  allModels: ModelOption[];
  /**
   * 选择器可见列表 = allModels 过滤 hiddenModelKeys。
   * 历史字段名 `models` 语义迁移：所有现存选择器/下拉消费方
   * （app computed 的 modelOptions、usePersonalizationContext 的 filteredModelOptions 等）
   * 读的都是它，过滤一处生效全局生效，无需逐个改消费方。
   */
  models: ModelOption[];
  /** 用户级隐藏模型 key 列表（personalization.hidden_models 的本地镜像，由 personalization store 同步） */
  hiddenModelKeys: string[];
}

export const useModelStore = defineStore('model', {
  state: (): ModelState => ({
    currentModelKey: '',
    allModels: [],
    models: [],
    hiddenModelKeys: []
  }),
  getters: {
    /** 选择器可见列表的语义别名（= state.models，供新代码显式引用） */
    visibleModels(state): ModelOption[] {
      return state.models;
    },
    currentModel(state): ModelOption | null {
      // 当前模型允许被用户隐藏（仅影响选择器显示），查表走全量列表
      return (
        state.allModels.find((m) => m.key === state.currentModelKey) || state.models[0] || null
      );
    }
  },
  actions: {
    /** 由 personalization store 在 hidden_models 变化时调用，重算可见列表 */
    setHiddenModels(keys: string[]) {
      this.hiddenModelKeys = Array.isArray(keys)
        ? keys.filter((k): k is string => typeof k === 'string')
        : [];
      this.recomputeVisibleModels();
    },
    recomputeVisibleModels() {
      const hidden = new Set(this.hiddenModelKeys || []);
      this.models = hidden.size
        ? this.allModels.filter((m) => !hidden.has(m.key))
        : [...this.allModels];
    },
    setModels(models: ModelOption[]) {
      if (!Array.isArray(models)) return;
      this.allModels = models;
      this.recomputeVisibleModels();
      const exists =
        this.currentModelKey && this.allModels.some((m) => m.key === this.currentModelKey);
      if (!exists) {
        this.currentModelKey = this.models[0]?.key || '';
      }
    },
    setModel(key: ModelKey) {
      if (this.currentModelKey === key) return;
      const exists = this.allModels.some((m) => m.key === key);
      if (exists) {
        this.currentModelKey = key;
        return;
      }
      // 如果列表中还没有该模型（例如 fetchModels 失败），
      // 临时插入一个 fallback 项，避免首屏显示“未选择模型”
      if (key) {
        this.allModels = [
          ...this.allModels,
          {
            key,
            label: key,
            description: '',
            fastOnly: false,
            supportsThinking: true
          }
        ];
        this.recomputeVisibleModels();
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
            apiProtocol: item.api_protocol || 'chat_completions',
            contextWindow: typeof item.context_window === 'number' ? item.context_window : null,
            maxOutputTokens:
              typeof item.max_output_tokens === 'number' ? item.max_output_tokens : null,
            fastOnly: !!item.fast_only,
            supportsThinking: !!item.supports_thinking,
            thinkingOnly: !!item.thinking_only,
            supportsReasoningEffort: !!item.supports_reasoning_effort,
            providerType: item.provider_type ? String(item.provider_type) : null,
            providerId: item.provider_id ? String(item.provider_id) : null,
            providerName: item.provider_name ? String(item.provider_name) : null,
            supportedReasoningLevels: Array.isArray(item.supported_reasoning_levels)
              ? item.supported_reasoning_levels
              : null,
            defaultReasoningLevel: item.default_reasoning_level
              ? String(item.default_reasoning_level)
              : null
          };
        });
      this.setModels(mapped);
    }
  }
});
