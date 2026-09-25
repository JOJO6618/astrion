import { defineStore } from 'pinia';
import { t } from '@/locales';
import { useModelStore } from './model';

/**
 * 提供商目录 + 连接状态 + 自定义模型（custom_models.json）管理。
 *
 * 数据契约见 docs/providers_api.md（唯一权威），后端为
 * server/providers.py + modules/provider_manager.py。全部端点仅管理员可用。
 *
 * 注意：
 * - auth=codex_oauth 的目录条目（OpenAI · ChatGPT 订阅）不走 /api/providers/connect，
 *   其「连接」复用 /api/codex/login/* 链路（见 providers/useCodexLogin.ts），
 *   「刷新」「断开」映射到 /api/codex/models/refresh 与 /api/codex/logout。
 * - 任何会改变模型注册表的操作（连接/断开/刷新/自定义模型增删改）成功后，
 *   统一刷新 model store，让选择器立即反映最新注册表。
 */

export interface ProviderCatalogEntry {
  id: string;
  name: string;
  base_url: string;
  icon?: string | null;
  icon_url?: string | null;
  icon_mono?: boolean;
  auth: string; // 'api_key' | 'none' | 'codex_oauth'
  key_url?: string | null;
  badge?: string | null;
  local?: boolean;
  protocol_note?: string | null; // 'multi_protocol_partial' 等
  connected: boolean;
  models_count?: number;
  models_fetched_at?: string | null;
  models_error?: string | null;
  unsupported_protocol_count?: number; // 多协议网关中协议暂不支持调用的模型数
}

export interface CustomModelEntry {
  model_name: string;
  display_name?: string;
  description?: string;
  url: string;
  apikey: string;
  model_id: string;
  visible?: boolean;
  multimodal?: string;
  reasoning_capability?: string;
  reasoning_effort?: boolean;
  context_window?: number | null;
  max_output_tokens?: number | null;
  thinkmode_status?: Record<string, any> | null;
  extra_parameter?: Record<string, any> | null;
  [key: string]: any;
}

export interface ProviderActionResult {
  ok: boolean;
  /** 后端返回的错误码/消息（原样展示，不做 i18n） */
  error?: string;
  modelsCount?: number;
  /** 连接/刷新成功但 /models 拉取失败时的告警（凭证已保存） */
  modelsError?: string;
}

interface ProvidersState {
  catalog: ProviderCatalogEntry[];
  loading: boolean;
  loaded: boolean;
  loadError: string;
  /** 行级忙碌态：provider id → 正在执行的动作名（connect/refresh/disconnect） */
  busyMap: Record<string, string>;
  customModels: CustomModelEntry[];
  customModelsLoaded: boolean;
  customModelsLoading: boolean;
  /** /api/custom-models 对非管理员 403：标记后 UI 隐藏编辑入口 */
  customModelsForbidden: boolean;
  customModelsError: string;
}

const parseJson = async (resp: Response): Promise<any> => {
  try {
    return await resp.json();
  } catch {
    return null;
  }
};

export const useProvidersStore = defineStore('providers', {
  state: (): ProvidersState => ({
    catalog: [],
    loading: false,
    loaded: false,
    loadError: '',
    busyMap: {},
    customModels: [],
    customModelsLoaded: false,
    customModelsLoading: false,
    customModelsForbidden: false,
    customModelsError: ''
  }),
  getters: {
    connectedEntries(state): ProviderCatalogEntry[] {
      return state.catalog.filter((entry) => entry.connected);
    },
    disconnectedEntries(state): ProviderCatalogEntry[] {
      return state.catalog.filter((entry) => !entry.connected);
    }
  },
  actions: {
    _setBusy(id: string, action: string) {
      this.busyMap = { ...this.busyMap, [id]: action };
    },
    _clearBusy(id: string) {
      const next = { ...this.busyMap };
      delete next[id];
      this.busyMap = next;
    },
    async fetchCatalog(force = false) {
      if (this.loaded && !force) {
        return;
      }
      this.loading = true;
      this.loadError = '';
      try {
        const resp = await fetch('/api/providers/catalog', { cache: 'no-store' });
        const data = await parseJson(resp);
        if (!resp.ok || !data?.success || !Array.isArray(data.catalog)) {
          throw new Error(data?.error || t('common.loadFailed'));
        }
        this.catalog = data.catalog;
        this.loaded = true;
      } catch (error: any) {
        this.loadError = error?.message || t('common.loadFailed');
      } finally {
        this.loading = false;
      }
    },
    /** 变更后统一善后：刷新目录 + 刷新模型注册表 */
    async _afterMutation() {
      await this.fetchCatalog(true);
      await useModelStore()
        .fetchModels()
        .catch(() => {});
    },
    /** 连接目录条目（api_key 传密钥；auth=none 本地条目无需密钥） */
    async connectCatalog(entry: ProviderCatalogEntry, apiKey = ''): Promise<ProviderActionResult> {
      this._setBusy(entry.id, 'connect');
      try {
        const payload: Record<string, any> = { catalog_id: entry.id };
        if (apiKey.trim()) {
          payload.api_key = apiKey.trim();
        }
        const resp = await fetch('/api/providers/connect', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await parseJson(resp);
        if (!resp.ok || !data?.success) {
          return { ok: false, error: data?.error || `http_${resp.status}` };
        }
        await this._afterMutation();
        return {
          ok: true,
          modelsCount: typeof data.models_count === 'number' ? data.models_count : 0,
          modelsError: data.models_error || ''
        };
      } catch (error: any) {
        return { ok: false, error: String(error?.message || error) };
      } finally {
        this._clearBusy(entry.id);
      }
    },
    /** 连接自定义提供商（OpenAI 兼容） */
    async connectCustom(payload: {
      provider_id: string;
      name: string;
      base_url: string;
      api_key?: string;
      headers?: Record<string, string>;
    }): Promise<ProviderActionResult> {
      const id = payload.provider_id.trim();
      this._setBusy(id || '__custom__', 'connect');
      try {
        const resp = await fetch('/api/providers/connect', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await parseJson(resp);
        if (!resp.ok || !data?.success) {
          return { ok: false, error: data?.error || `http_${resp.status}` };
        }
        await this._afterMutation();
        return {
          ok: true,
          modelsCount: typeof data.models_count === 'number' ? data.models_count : 0,
          modelsError: data.models_error || ''
        };
      } catch (error: any) {
        return { ok: false, error: String(error?.message || error) };
      } finally {
        this._clearBusy(id || '__custom__');
      }
    },
    /** 刷新模型列表；codex_oauth 条目映射到 Codex 模型刷新端点 */
    async refreshProvider(entry: ProviderCatalogEntry): Promise<ProviderActionResult> {
      this._setBusy(entry.id, 'refresh');
      try {
        const url =
          entry.auth === 'codex_oauth'
            ? '/api/codex/models/refresh'
            : `/api/providers/${encodeURIComponent(entry.id)}/refresh`;
        const resp = await fetch(url, { method: 'POST' });
        const data = await parseJson(resp);
        if (!resp.ok || !data?.success) {
          return { ok: false, error: data?.error || data?.models_error || `http_${resp.status}` };
        }
        await this._afterMutation();
        return {
          ok: true,
          modelsCount: typeof data.models_count === 'number' ? data.models_count : undefined,
          modelsError: data.models_error || ''
        };
      } catch (error: any) {
        return { ok: false, error: String(error?.message || error) };
      } finally {
        this._clearBusy(entry.id);
      }
    },
    /** 断开连接；codex_oauth 条目映射到 Codex 登出 */
    async disconnectProvider(entry: ProviderCatalogEntry): Promise<ProviderActionResult> {
      this._setBusy(entry.id, 'disconnect');
      try {
        const url =
          entry.auth === 'codex_oauth'
            ? '/api/codex/logout'
            : `/api/providers/${encodeURIComponent(entry.id)}`;
        const resp = await fetch(url, { method: entry.auth === 'codex_oauth' ? 'POST' : 'DELETE' });
        const data = await parseJson(resp);
        if (!resp.ok || !data?.success) {
          return { ok: false, error: data?.error || `http_${resp.status}` };
        }
        await this._afterMutation();
        return { ok: true };
      } catch (error: any) {
        return { ok: false, error: String(error?.message || error) };
      } finally {
        this._clearBusy(entry.id);
      }
    },

    // ---------------------------------------------------------- 自定义模型

    async fetchCustomModels(force = false) {
      if (this.customModelsLoaded && !force) {
        return;
      }
      this.customModelsLoading = true;
      this.customModelsError = '';
      try {
        const resp = await fetch('/api/custom-models', { cache: 'no-store' });
        if (resp.status === 403) {
          this.customModelsForbidden = true;
          this.customModels = [];
          this.customModelsLoaded = true;
          return;
        }
        const data = await parseJson(resp);
        if (!resp.ok || !data?.success || !Array.isArray(data.models)) {
          throw new Error(data?.error || t('common.loadFailed'));
        }
        this.customModels = data.models;
        this.customModelsForbidden = false;
        this.customModelsLoaded = true;
      } catch (error: any) {
        this.customModelsError = error?.message || t('common.loadFailed');
      } finally {
        this.customModelsLoading = false;
      }
    },
    async createCustomModel(item: CustomModelEntry): Promise<ProviderActionResult> {
      try {
        const resp = await fetch('/api/custom-models', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(item)
        });
        const data = await parseJson(resp);
        if (!resp.ok || !data?.success) {
          return { ok: false, error: data?.error || `http_${resp.status}` };
        }
        await this.fetchCustomModels(true);
        await useModelStore()
          .fetchModels()
          .catch(() => {});
        return { ok: true };
      } catch (error: any) {
        return { ok: false, error: String(error?.message || error) };
      }
    },
    async updateCustomModel(
      modelName: string,
      item: CustomModelEntry
    ): Promise<ProviderActionResult> {
      try {
        const resp = await fetch(`/api/custom-models/${encodeURIComponent(modelName)}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(item)
        });
        const data = await parseJson(resp);
        if (!resp.ok || !data?.success) {
          return { ok: false, error: data?.error || `http_${resp.status}` };
        }
        await this.fetchCustomModels(true);
        await useModelStore()
          .fetchModels()
          .catch(() => {});
        return { ok: true };
      } catch (error: any) {
        return { ok: false, error: String(error?.message || error) };
      }
    },
    async deleteCustomModel(modelName: string): Promise<ProviderActionResult> {
      try {
        const resp = await fetch(`/api/custom-models/${encodeURIComponent(modelName)}`, {
          method: 'DELETE'
        });
        const data = await parseJson(resp);
        if (!resp.ok || !data?.success) {
          return { ok: false, error: data?.error || `http_${resp.status}` };
        }
        await this.fetchCustomModels(true);
        await useModelStore()
          .fetchModels()
          .catch(() => {});
        return { ok: true };
      } catch (error: any) {
        return { ok: false, error: String(error?.message || error) };
      }
    }
  }
});
