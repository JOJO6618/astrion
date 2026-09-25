<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { t } from '@/locales';
import { storeToRefs } from 'pinia';
import { useModelStore } from '@/stores/model';
import type { ModelOption } from '@/stores/model';
import { usePersonalizationStore } from '@/stores/personalization';
import { useProvidersStore } from '@/stores/providers';
import type { CustomModelEntry } from '@/stores/providers';
import { useSettingsStore } from '@/stores/settings';
import { useUiStore } from '@/stores/ui';
import PillSwitch from './providers/PillSwitch.vue';
import CustomModelDialog from './providers/CustomModelDialog.vue';

defineOptions({ name: 'ModelsTab' });

/**
 * 设置页「模型」分区（所有用户可见）。
 * 按提供商分组的模型列表（每行开关控制用户级可见性，存 personalization.hidden_models）
 * +「自定义」分组（custom_models.json 的 UI 表单增删改，编辑入口仅管理员）。
 */

interface ModelRow {
  key: string;
  name: string;
  sub: string;
  caps: string[];
  /** 自定义分组条目（编辑/删除绑定）；提供商同步/Codex 行为 null */
  custom?: CustomModelEntry | null;
}

interface ModelGroup {
  id: string;
  title: string;
  badge: string;
  rows: ModelRow[];
}

const modelStore = useModelStore();
const personalization = usePersonalizationStore();
const providersStore = useProvidersStore();
const settingsStore = useSettingsStore();
const uiStore = useUiStore();

const { allModels } = storeToRefs(modelStore);
const { customModels } = storeToRefs(providersStore);

const modelsLoading = ref(false);
const modelsError = ref('');
const collapsedGroups = ref<string[]>([]);

// ── 自定义模型弹窗 ──
const customDialogMode = ref<'add' | 'edit'>('add');
const customDialogInitial = ref<CustomModelEntry | null>(null);
const customDialogOpen = ref(false);

const isAdmin = computed(() => settingsStore.isAdmin);
const hiddenSet = computed(() => new Set(personalization.form.hidden_models || []));

const formatContext = (tokens?: number | null): string => {
  if (!tokens || tokens <= 0) return '';
  if (tokens >= 1048576) {
    const m = tokens / 1048576;
    return `${Number.isInteger(m) ? m : m.toFixed(1)}M`;
  }
  if (tokens >= 1024) {
    return `${Math.round(tokens / 1024)}K`;
  }
  return String(tokens);
};

const registryCaps = (model: ModelOption): string[] => {
  const caps: string[] = [];
  if (model.supportsImage) caps.push(t('settings.capImage'));
  if (model.supportsVideo) caps.push(t('settings.capVideo'));
  if (model.supportsThinking) caps.push(t('settings.capThinking'));
  const ctx = formatContext(model.contextWindow);
  if (ctx) caps.push(ctx);
  return caps;
};

const customCaps = (entry: CustomModelEntry): string[] => {
  const caps: string[] = [];
  const multimodal = String(entry.multimodal || 'none');
  if (multimodal === 'image' || multimodal === 'image,video') caps.push(t('settings.capImage'));
  if (multimodal === 'video' || multimodal === 'image,video') caps.push(t('settings.capVideo'));
  if (String(entry.reasoning_capability || '').includes('thinking')) {
    caps.push(t('settings.capThinking'));
  }
  const ctx = formatContext(typeof entry.context_window === 'number' ? entry.context_window : null);
  if (ctx) caps.push(ctx);
  return caps;
};

const urlHost = (raw?: string): string => {
  try {
    return raw ? new URL(raw).hostname : '';
  } catch {
    return String(raw || '');
  }
};

/** 展示用模型 id：剥掉 `{providerId}/` 前缀（注册表 key 形如 deepseek/deepseek-chat） */
const displayModelId = (model: ModelOption): string => {
  const prefix = model.providerId ? `${model.providerId}/` : '';
  return prefix && model.key.startsWith(prefix) ? model.key.slice(prefix.length) : model.key;
};

// ── 分组 ──
const groups = computed<ModelGroup[]>(() => {
  const out: ModelGroup[] = [];
  const providerGroups = new Map<string, ModelGroup>();
  const registryCustomRows: ModelRow[] = [];

  for (const model of allModels.value) {
    const row: ModelRow = {
      key: model.key,
      name: model.label,
      sub: displayModelId(model),
      caps: registryCaps(model)
    };
    if (model.providerType === 'provider' && model.providerId) {
      // openai-codex 泛化后同为 provider 分组（组名 OpenAI（ChatGPT 订阅））
      const gid = model.providerId;
      if (!providerGroups.has(gid)) {
        providerGroups.set(gid, {
          id: `provider-${gid}`,
          title: model.providerName || gid,
          badge: t('settings.modelGroupProviderBadge'),
          rows: []
        });
      }
      providerGroups.get(gid)!.rows.push(row);
    } else {
      registryCustomRows.push(row);
    }
  }
  out.push(...providerGroups.values());

  // 「自定义」分组置底：管理员用 custom_models.json 原始条目（可编辑/删除）；
  // 非管理员端点 403，退回注册表里的手写 custom 模型（只读）。
  if (isAdmin.value && !providersStore.customModelsForbidden) {
    const rows: ModelRow[] = customModels.value.map((entry) => ({
      key: entry.model_name,
      name: entry.display_name || entry.model_name,
      sub: [entry.model_id, urlHost(entry.url)].filter(Boolean).join(' · '),
      caps: customCaps(entry),
      custom: entry
    }));
    if (rows.length) {
      out.push({
        id: 'custom',
        title: t('settings.modelGroupCustom'),
        badge: t('settings.modelGroupCustomBadge'),
        rows
      });
    }
  } else if (registryCustomRows.length) {
    out.push({
      id: 'custom',
      title: t('settings.modelGroupCustom'),
      badge: t('settings.modelGroupCustomBadge'),
      rows: registryCustomRows
    });
  }
  return out;
});

// ── 可见性开关（用户级，存 personalization.hidden_models，自动保存） ──
const isVisible = (key: string): boolean => !hiddenSet.value.has(key);

const toggleVisibility = (key: string) => {
  const current = new Set(personalization.form.hidden_models || []);
  if (current.has(key)) {
    current.delete(key);
  } else {
    current.add(key);
  }
  personalization.setHiddenModels([...current]);
};

// ── 分组折叠 ──
const isCollapsed = (id: string): boolean => collapsedGroups.value.includes(id);

const toggleGroup = (id: string) => {
  collapsedGroups.value = isCollapsed(id)
    ? collapsedGroups.value.filter((g) => g !== id)
    : [...collapsedGroups.value, id];
};

// ── 自定义模型增删改 ──
const openAddCustom = () => {
  customDialogMode.value = 'add';
  customDialogInitial.value = null;
  customDialogOpen.value = true;
};

const openEditCustom = (entry: CustomModelEntry) => {
  customDialogMode.value = 'edit';
  customDialogInitial.value = entry;
  customDialogOpen.value = true;
};

const removeCustom = async (entry: CustomModelEntry) => {
  const ok = await uiStore.requestConfirm({
    title: t('settings.customModelDeleteConfirmTitle'),
    message: t('settings.customModelDeleteConfirmMessage', { name: entry.model_name }),
    confirmText: t('common.delete'),
    confirmVariant: 'danger'
  });
  if (!ok) return;
  const result = await providersStore.deleteCustomModel(entry.model_name);
  if (!result.ok) {
    // 后端错误原样展示
    uiStore.pushToast({ message: result.error || t('common.unknownError'), type: 'error' });
    return;
  }
  uiStore.pushToast({ message: t('settings.customModelDeleted'), type: 'success' });
};

const gotoProviders = () => {
  settingsStore.setActiveSection('providers');
};

const loadModels = () => {
  if (!allModels.value.length) {
    modelsLoading.value = true;
  }
  modelsError.value = '';
  modelStore
    .fetchModels()
    .catch((error: any) => {
      modelsError.value = error?.message || t('common.loadFailed');
    })
    .finally(() => {
      modelsLoading.value = false;
    });
};

onMounted(() => {
  // 模型注册表：空则显示加载态；无论如何拉一次保证新鲜（提供商刚变更过）
  loadModels();
  if (isAdmin.value) {
    void providersStore.fetchCustomModels();
  }
});
</script>

<template>
  <section class="settings-page">
    <!-- 操作行（仅管理员） -->
    <div v-if="isAdmin" class="models-actions">
      <button type="button" class="models-btn models-btn--primary" @click="openAddCustom">
        {{ $t('settings.addCustomModel') }}
      </button>
      <button type="button" class="models-btn" @click="gotoProviders">
        {{ $t('settings.addCustomProvider') }}
      </button>
    </div>

    <div v-if="modelsLoading" class="models-state">{{ $t('common.loading') }}</div>
    <div v-else-if="modelsError" class="models-state models-state--error">
      <span>{{ modelsError }}</span>
      <button type="button" class="models-btn" @click="loadModels">
        {{ $t('common.retry') }}
      </button>
    </div>
    <div v-else-if="!groups.length" class="models-state">{{ $t('settings.modelsEmpty') }}</div>

    <template v-else>
      <div v-for="group in groups" :key="group.id" class="models-group">
        <button
          type="button"
          class="models-group-title"
          :aria-expanded="!isCollapsed(group.id)"
          @click="toggleGroup(group.id)"
        >
          <svg
            class="models-group-chevron"
            :class="{ collapsed: isCollapsed(group.id) }"
            width="12"
            height="12"
            viewBox="0 0 16 16"
            fill="none"
            aria-hidden="true"
          >
            <path
              d="M4 6l4 4 4-4"
              stroke="currentColor"
              stroke-width="1.5"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
          <span class="models-group-title__text">{{ group.title }}</span>
          <span class="models-badge">{{ group.badge }}</span>
          <span class="models-group-count">{{ group.rows.length }}</span>
        </button>
        <div v-show="!isCollapsed(group.id)" class="models-list">
          <div v-for="row in group.rows" :key="row.key" class="models-row">
            <div class="models-row__main">
              <div class="models-row__name">{{ row.name }}</div>
              <div class="models-row__id">{{ row.sub }}</div>
            </div>
            <div class="models-row__caps">
              <span v-for="cap in row.caps" :key="cap" class="models-cap">{{ cap }}</span>
            </div>
            <PillSwitch
              :on="isVisible(row.key)"
              :label="$t('settings.modelVisibleSwitch')"
              @toggle="toggleVisibility(row.key)"
            />
            <template v-if="row.custom && isAdmin">
              <button
                type="button"
                class="models-icon-btn"
                :title="$t('settings.editLabel')"
                :aria-label="$t('settings.editLabel')"
                @click="openEditCustom(row.custom)"
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                  <path
                    d="M11 2.5l2.5 2.5L5 13.5H2.5V11L11 2.5z"
                    stroke="currentColor"
                    stroke-width="1.3"
                    stroke-linejoin="round"
                  />
                </svg>
              </button>
              <button
                type="button"
                class="models-icon-btn"
                :title="$t('common.delete')"
                :aria-label="$t('common.delete')"
                @click="removeCustom(row.custom)"
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                  <path
                    d="M3 4.5h10M6.5 4V3h3v1.5M4.5 4.5l.5 8.5h6l.5-8.5"
                    stroke="currentColor"
                    stroke-width="1.2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  />
                </svg>
              </button>
            </template>
          </div>
        </div>
      </div>

      <div class="models-hint">{{ $t('settings.modelsEditHint') }}</div>
    </template>

    <CustomModelDialog
      v-if="customDialogOpen"
      :mode="customDialogMode"
      :initial="customDialogInitial"
      @close="customDialogOpen = false"
    />
  </section>
</template>

<style scoped>
.models-actions {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}

.models-btn {
  height: 32px;
  padding: 0 14px;
  border-radius: 6px;
  font-size: 13px;
  border: 1px solid var(--border-default);
  color: var(--text-primary);
  background: var(--surface-raised);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  white-space: nowrap;
  flex-shrink: 0;
  cursor: pointer;
}

.models-btn:hover {
  background: var(--surface-soft);
}

.models-btn--primary {
  background: var(--accent);
  border-color: transparent;
  color: var(--on-accent);
}

.models-btn--primary:hover {
  background: var(--accent-hover);
}

.models-state {
  padding: 14px 2px;
  border-top: 1px solid var(--border-default);
  border-bottom: 1px solid var(--border-default);
  font-size: 13px;
  color: var(--text-tertiary);
  display: flex;
  align-items: center;
  gap: 10px;
}

.models-state--error {
  color: var(--state-danger);
}

/* ── 分组（可折叠） ── */
.models-group-title {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  height: 40px;
  padding: 0 2px;
  margin-top: 10px;
  border: 0;
  background: transparent;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
  cursor: pointer;
  text-align: left;
}

.models-group-title__text {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.models-group-chevron {
  flex-shrink: 0;
  color: var(--text-tertiary);
  transition: transform 0.15s ease;
}

.models-group-chevron.collapsed {
  transform: rotate(-90deg);
}

.models-group-count {
  margin-left: auto;
  font-size: 11px;
  font-weight: 400;
  color: var(--text-muted);
  flex-shrink: 0;
}

.models-badge {
  font-size: 11px;
  color: var(--text-tertiary);
  background: var(--badge-bg);
  border-radius: 4px;
  padding: 1px 6px;
  line-height: 16px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  font-weight: 400;
  flex-shrink: 0;
}

/* ── 模型行 ── */
.models-list {
  border-top: 1px solid var(--border-default);
}

.models-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 11px 2px;
  border-bottom: 1px solid var(--border-default);
  min-height: 56px;
}

.models-row__main {
  flex: 1;
  min-width: 0;
}

.models-row__name {
  font-size: 13.5px;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.models-row__id {
  font-size: 12px;
  color: var(--text-tertiary);
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-top: 1px;
}

.models-row__caps {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}

.models-cap {
  font-size: 11px;
  color: var(--text-tertiary);
  border: 1px solid var(--border-default);
  border-radius: 4px;
  padding: 0 5px;
  line-height: 16px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  white-space: nowrap;
}

.models-icon-btn {
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: 6px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  flex-shrink: 0;
  opacity: 0;
  transition: opacity 0.12s ease;
}

.models-row:hover .models-icon-btn,
.models-icon-btn:focus-visible {
  opacity: 1;
}

.models-icon-btn:hover {
  background: var(--hover-bg);
  color: var(--text-primary);
}

.models-hint {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-top: 12px;
  line-height: 1.6;
}

/* 触屏无 hover：编辑按钮常显但弱化 */
@media (hover: none) {
  .models-icon-btn {
    opacity: 0.6;
  }
}
</style>
