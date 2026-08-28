import { defineStore } from 'pinia';
import { t } from '@/locales';

export interface EffectivePolicy {
  categories: Record<
    string,
    {
      label: string;
      tools: string[];
      default_enabled?: boolean;
      locked?: boolean;
      locked_state?: boolean | null;
    }
  >;
  forced_category_states: Record<string, boolean | null>;
  disabled_models: string[];
  ui_blocks: Record<string, boolean>;
  updated_at?: string;
}

interface PolicyState {
  loaded: boolean;
  loading: boolean;
  error: string;
  policy: EffectivePolicy | null;
}

const DEFAULT_POLICY: EffectivePolicy = {
  categories: {},
  forced_category_states: {},
  disabled_models: [],
  ui_blocks: {}
};

export const usePolicyStore = defineStore('policy', {
  state: (): PolicyState => ({
    loaded: false,
    loading: false,
    error: '',
    policy: null
  }),
  getters: {
    disabledModelSet(state): Set<string> {
      return new Set(state.policy?.disabled_models || []);
    },
    forcedCategoryStates(state): Record<string, boolean | null> {
      return state.policy?.forced_category_states || {};
    },
    uiBlocks(state): Record<string, boolean> {
      return state.policy?.ui_blocks || {};
    }
  },
  actions: {
    async fetchPolicy() {
      if (this.loading) return;
      this.loading = true;
      this.error = '';
      try {
        const resp = await fetch('/api/effective-policy');
        const result = await resp.json();
        if (!resp.ok || !result.success) {
          throw new Error(result.error || t('stores.loadPolicyFailed'));
        }
        this.policy = result.data || DEFAULT_POLICY;
        this.loaded = true;
      } catch (error: any) {
        this.error = error?.message || t('stores.loadPolicyFailed');
      } finally {
        this.loading = false;
      }
    },
    isModelDisabled(key: string): boolean {
      return this.disabledModelSet.has(key);
    },
    isCategoryLocked(id: string): boolean {
      const states = this.forcedCategoryStates;
      return typeof states[id] === 'boolean';
    },
    categoryLockedState(id: string): boolean | null {
      const states = this.forcedCategoryStates;
      if (typeof states[id] === 'boolean') return states[id] as boolean;
      return null;
    },
    isBlocked(action: string): boolean {
      return !!this.uiBlocks[action];
    }
  }
});
