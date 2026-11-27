<template>
  <div class="policy-page">
    <SecondaryGate
      v-if="!secondaryVerified"
      :configured="secondaryConfigured"
      :loading="secondaryLoading"
      :error="secondaryError"
      :description="$t('adminPolicy.gateDescription')"
      @verify="handleVerifySecondary"
      @recheck="checkSecondary"
    />
    <header class="policy-header">
      <div>
        <h1>{{ $t('adminPolicy.title') }}</h1>
        <p>{{ $t('adminPolicy.subtitle', { time: lastUpdated || '—' }) }}</p>
      </div>
      <div class="header-actions">
        <button type="button" class="ghost-btn" @click="loadScope">
          {{ $t('adminPolicy.load') }}
        </button>
        <button type="button" class="primary" :disabled="saving" @click="savePolicy">
          {{ saving ? $t('common.saving') : $t('common.save') }}
        </button>
      </div>
    </header>

    <transition name="fade">
      <div v-if="banner.message" class="banner" :class="banner.type">
        <span>{{ banner.message }}</span>
        <CloseButton :label="$t('common.close')" @click="banner.message = ''" />
      </div>
    </transition>

    <section class="panel">
      <div class="panel-title">
        <h2>{{ $t('adminPolicy.categorySection') }}</h2>
        <button type="button" @click="addCategory">{{ $t('adminPolicy.addCategory') }}</button>
      </div>
      <div class="category-table">
        <div class="category-row category-head">
          <span>{{ $t('adminPolicy.catId') }}</span>
          <span>{{ $t('adminPolicy.catName') }}</span>
          <span>{{ $t('adminPolicy.catTools') }}</span>
          <span>{{ $t('adminPolicy.catDefaultEnabled') }}</span>
          <span>{{ $t('adminPolicy.catForced') }}</span>
          <span>{{ $t('adminPolicy.tableActions') }}</span>
        </div>
        <div class="category-row" v-for="cat in visibleCategories" :key="cat.id">
          <input v-model="cat.id" class="id-input" />
          <input v-model="cat.label" />
          <div class="tool-select" :class="{ open: openToolMenu === cat.id }">
            <button type="button" class="tool-select-trigger" @click.stop="toggleToolMenu(cat.id)">
              <span v-if="cat.tools.length" class="tool-badges">
                <span class="tool-badge" v-for="tool in cat.tools" :key="tool">{{ tool }}</span>
              </span>
              <span v-else class="muted">{{ $t('adminPolicy.selectTools') }}</span>
              <span class="caret">▾</span>
            </button>
            <div class="tool-select-menu" v-if="openToolMenu === cat.id" @click.stop>
              <div class="tool-select-search">
                <input
                  v-model="toolSearch"
                  type="text"
                  :placeholder="$t('adminPolicy.toolSearchPlaceholder')"
                />
              </div>
              <div class="tool-select-options">
                <label v-for="tool in filteredToolOptions(cat)" :key="tool">
                  <input
                    type="checkbox"
                    :checked="cat.tools.includes(tool)"
                    @change="toggleToolInCategory(cat, tool)"
                  />
                  <span>{{ tool }}</span>
                </label>
                <p v-if="!filteredToolOptions(cat).length" class="muted tiny">
                  {{ $t('adminPolicy.noMatchingTool') }}
                </p>
              </div>
            </div>
          </div>
          <label class="toggle-row compact">
            <input
              type="checkbox"
              :checked="getCategoryDefault(cat.id)"
              @change="setCategoryDefault(cat.id, $event.target.checked)"
            />
            <FancyCheck :checked="getCategoryDefault(cat.id)" accent-checked />
            <span>{{
              cat.default_enabled ? $t('adminPolicy.enabledOn') : $t('adminPolicy.enabledOff')
            }}</span>
          </label>
          <div class="dropdown" :class="{ open: openForceMenu === cat.id }">
            <button type="button" class="ghost-btn" @click="toggleForceMenu(cat.id)">
              <span>{{ forcedLabel(cat.forced) }}</span>
              <span class="caret">▾</span>
            </button>
            <div class="dropdown-menu" v-if="openForceMenu === cat.id">
              <button type="button" @click="setForced(cat.id, null)">
                {{ $t('adminPolicy.forcedNone') }}
              </button>
              <button type="button" @click="setForced(cat.id, true)">
                {{ $t('adminPolicy.forcedEnable') }}
              </button>
              <button type="button" @click="setForced(cat.id, false)">
                {{ $t('adminPolicy.forcedDisable') }}
              </button>
            </div>
          </div>
          <button type="button" class="link danger" @click="removeCategory(cat.id)">
            {{ $t('common.delete') }}
          </button>
        </div>
      </div>
    </section>

    <section class="panel grid-2">
      <div>
        <div class="panel-title">
          <h2>{{ $t('adminPolicy.modelDisable') }}</h2>
        </div>
        <div class="toggle-grid">
          <label v-for="model in defaults.models" :key="model" class="toggle-row">
            <input type="checkbox" :checked="isModelDisabled(model)" @change="toggleModel(model)" />
            <FancyCheck :checked="isModelDisabled(model)" accent-checked />
            <span>{{ model }}</span>
          </label>
        </div>
      </div>
      <div>
        <div class="panel-title">
          <h2>{{ $t('adminPolicy.uiBlockSection') }}</h2>
        </div>
        <div class="toggle-grid">
          <label v-for="key in defaults.ui_block_keys" :key="key" class="toggle-row">
            <input
              type="checkbox"
              :checked="!!form.config.ui_blocks[key]"
              @change="toggleUiBlock(key, $event)"
            />
            <FancyCheck :checked="!!form.config.ui_blocks[key]" accent-checked />
            <span>{{ uiBlockLabel(key) }}</span>
          </label>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-title">
        <h2>{{ $t('adminPolicy.removedCategories') }}</h2>
      </div>
      <div class="chips">
        <span v-if="!visibleRemovedCategories.length" class="muted">{{
          $t('adminPolicy.none')
        }}</span>
        <span v-for="cid in visibleRemovedCategories" :key="cid" class="chip">
          {{ cid }}
          <button type="button" @click="undoRemove(cid)">×</button>
        </span>
      </div>
    </section>

    <section class="panel muted-info">
      <p>{{ $t('adminPolicy.notesLabel') }}</p>
      <ul>
        <li>{{ $t('adminPolicy.notePriority') }}</li>
        <li>{{ $t('adminPolicy.noteForced') }}</li>
        <li>{{ $t('adminPolicy.noteUiBlocks') }}</li>
      </ul>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, onMounted, onBeforeUnmount, watch } from 'vue';
import { useSecondaryPass } from './useSecondaryPass';
import SecondaryGate from './SecondaryGate.vue';
import FancyCheck from '@/components/common/FancyCheck.vue';
import CloseButton from '@/components/common/CloseButton.vue';
import { t } from '@/locales';

interface RawPolicy {
  updated_at?: string;
  global?: any;
}

interface CategoryFormItem {
  id: string;
  label: string;
  tools: string[];
  default_enabled: boolean;
  forced: boolean | null;
}

const defaults = reactive({
  categories: {} as Record<string, any>,
  models: [] as string[],
  ui_block_keys: [] as string[]
});

const {
  verified: secondaryVerified,
  configured: secondaryConfigured,
  loading: secondaryLoading,
  error: secondaryError,
  check: checkSecondary,
  verify: verifySecondary
} = useSecondaryPass();

const form = reactive({
  config: {
    category_overrides: {} as Record<string, any>,
    remove_categories: [] as string[],
    forced_category_states: {} as Record<string, boolean>,
    disabled_models: [] as string[],
    ui_blocks: {} as Record<string, boolean>
  }
});

const lastUpdated = ref<string | null>(null);
const saving = ref(false);
const policyCache = ref<RawPolicy | null>(null);
const openForceMenu = ref<string | null>(null);
const openToolMenu = ref<string | null>(null);
const toolSearch = ref('');

const banner = reactive({ message: '', type: 'info' as 'info' | 'success' | 'error' });

const toolOptions = computed(() => {
  const set = new Set<string>();
  const addList = (list: any) => {
    if (!Array.isArray(list)) return;
    list.forEach((tool) => {
      if (typeof tool !== 'string') return;
      const name = tool.trim();
      if (name && name !== 'list_mcp_servers' && !name.startsWith('mcp__')) set.add(name);
    });
  };
  Object.values(defaults.categories || {}).forEach((cat: any) => addList(cat?.tools));
  return Array.from(set).sort();
});

const toolAssignments = computed<Record<string, string[]>>(() => {
  const map: Record<string, string[]> = {};
  categoryList.value.forEach((cat) => {
    (cat.tools || []).forEach((tool) => {
      if (!map[tool]) map[tool] = [];
      map[tool].push(cat.id);
    });
  });
  return map;
});

const optionsForCategory = (cat: CategoryFormItem) => {
  return toolOptions.value.filter((tool) => {
    const owners = toolAssignments.value[tool] || [];
    if (!owners.length) return true;
    return owners.length === 1 && owners[0] === cat.id;
  });
};

const filteredToolOptions = (cat: CategoryFormItem) => {
  const q = toolSearch.value.trim().toLowerCase();
  const base = optionsForCategory(cat);
  if (!q) return base;
  return base.filter((item) => item.toLowerCase().includes(q));
};

const toggleForceMenu = (id: string) => {
  openForceMenu.value = openForceMenu.value === id ? null : id;
};

const forcedLabel = (value: boolean | null) => {
  if (value === true) return t('adminPolicy.forcedEnable');
  if (value === false) return t('adminPolicy.forcedDisable');
  return t('adminPolicy.forcedNone');
};

const setForced = (id: string, value: boolean | null) => {
  const map = { ...form.config.forced_category_states };
  if (value === null) {
    delete map[id];
  } else {
    map[id] = value;
  }
  form.config.forced_category_states = map;
  openForceMenu.value = null;
};

const toggleToolMenu = (id: string) => {
  openToolMenu.value = openToolMenu.value === id ? null : id;
  toolSearch.value = '';
};

const toggleToolInCategory = (cat: CategoryFormItem, tool: string) => {
  const owners = toolAssignments.value[tool] || [];
  const conflict = owners.find((id) => id !== cat.id);
  if (conflict) {
    banner.message = t('adminPolicy.toolConflict', { tool, category: conflict });
    banner.type = 'error';
    return;
  }
  const set = new Set(cat.tools || []);
  if (set.has(tool)) {
    set.delete(tool);
  } else {
    set.add(tool);
  }
  cat.tools = Array.from(set);
};

const handleDocClick = (event: MouseEvent) => {
  const target = event.target as HTMLElement | null;
  if (!target) return;
  if (!target.closest('.tool-select')) {
    openToolMenu.value = null;
  }
};

const categoryList = computed<CategoryFormItem[]>(() => {
  const map = form.config.category_overrides || {};
  return Object.keys(map)
    .filter((id) => id !== 'custom')
    .map((id) => ({
      id,
      label: map[id]?.label || id,
      tools: Array.isArray(map[id]?.tools) ? [...map[id].tools] : [],
      default_enabled: map[id]?.default_enabled !== false,
      forced: form.config.forced_category_states[id] ?? null
    }));
});

const isNormalCategory = (id: string) =>
  id !== 'custom' && id !== 'mcp' && !id.startsWith('mcp_server__');
const visibleCategories = computed(() =>
  categoryList.value.filter((cat) => isNormalCategory(cat.id))
);
const visibleRemovedCategories = computed(() =>
  form.config.remove_categories.filter(isNormalCategory)
);

const getCategoryDefault = (id: string): boolean => {
  const map = form.config.category_overrides || {};
  if (map[id] && typeof map[id].default_enabled !== 'undefined') {
    return !!map[id].default_enabled;
  }
  const base = defaults.categories?.[id];
  return base ? !!base.default_enabled : true;
};

const setCategoryDefault = (id: string, enabled: boolean) => {
  const current = form.config.category_overrides || {};
  const base = current[id] || defaults.categories?.[id] || { label: id, tools: [] };
  form.config.category_overrides = {
    ...current,
    [id]: { ...base, default_enabled: enabled }
  };
};

function uiBlockLabel(key: string) {
  const map: Record<string, string> = {
    collapse_workspace: 'uiBlockCollapseWorkspace',
    block_file_manager: 'uiBlockFileManager',
    block_personal_space: 'uiBlockPersonalSpace',
    block_upload: 'uiBlockUpload',
    block_conversation_review: 'uiBlockConversationReview',
    block_tool_toggle: 'uiBlockToolToggle',
    block_realtime_terminal: 'uiBlockRealtimeTerminal',
    block_focus_panel: 'uiBlockFocusPanel',
    block_token_panel: 'uiBlockTokenPanel',
    block_compress_conversation: 'uiBlockCompressConversation',
    block_virtual_monitor: 'uiBlockVirtualMonitor'
  };
  return map[key] ? t(`adminPolicy.${map[key]}`) : key;
}

function toggleUiBlock(key: string, event: Event) {
  event?.stopPropagation?.();
  form.config.ui_blocks = {
    ...form.config.ui_blocks,
    [key]: !form.config.ui_blocks[key]
  };
}

function isModelDisabled(key: string) {
  return (form.config.disabled_models || []).includes(key);
}

function toggleModel(key: string) {
  const set = new Set(form.config.disabled_models || []);
  if (set.has(key)) {
    set.delete(key);
  } else {
    set.add(key);
  }
  form.config.disabled_models = Array.from(set);
}

function addCategory() {
  const id = `custom_${Date.now().toString(36)}`;
  form.config.category_overrides[id] = {
    label: id,
    tools: [],
    default_enabled: true
  };
}

function removeCategory(id: string) {
  delete form.config.category_overrides[id];
  form.config.forced_category_states[id] && delete form.config.forced_category_states[id];
  if (!form.config.remove_categories.includes(id)) {
    form.config.remove_categories.push(id);
  }
}

function undoRemove(id: string) {
  form.config.remove_categories = form.config.remove_categories.filter((item) => item !== id);
}

function rebuildCategoryOverrides() {
  const map: Record<string, any> = {};
  const legacyCategory = form.config.category_overrides?.custom;
  if (legacyCategory) map.custom = legacyCategory;
  categoryList.value.forEach((item) => {
    if (!item.id) return;
    map[item.id] = {
      label: item.label || item.id,
      tools: (item.tools || []).map((s) => (typeof s === 'string' ? s.trim() : '')).filter(Boolean),
      default_enabled: !!item.default_enabled
    };
    if (item.forced === true || item.forced === false) {
      form.config.forced_category_states[item.id] = item.forced;
    } else {
      delete form.config.forced_category_states[item.id];
    }
  });
  form.config.category_overrides = map;
}

async function fetchDefaults() {
  if (!secondaryVerified.value) return;
  const resp = await fetch('/api/admin/policy', { credentials: 'same-origin' });
  const data = await resp.json();
  if (!resp.ok || !data.success) {
    throw new Error(data.error || t('adminPolicy.loadDefaultsFailed'));
  }
  defaults.categories = data.defaults?.categories || {};
  defaults.models = data.defaults?.models || [];
  defaults.ui_block_keys = data.defaults?.ui_block_keys || [];
  policyCache.value = data.data;
  lastUpdated.value = data.data?.updated_at || null;
  applyScopeConfig();
}

function scopeConfig(): any {
  return policyCache.value?.global || {};
}

function applyScopeConfig() {
  const cfg = scopeConfig() || {};
  const base = JSON.parse(JSON.stringify(defaults.categories || {}));
  const overrides = cfg.category_overrides || {};
  const removed = new Set(cfg.remove_categories || []);

  Object.keys(overrides).forEach((key) => {
    base[key] = overrides[key];
  });
  removed.forEach((id: string) => {
    delete base[id];
  });

  form.config.category_overrides = base;
  form.config.remove_categories = [...removed];
  form.config.forced_category_states = { ...(cfg.forced_category_states || {}) };
  form.config.disabled_models = [...(cfg.disabled_models || [])];
  form.config.ui_blocks = { ...(cfg.ui_blocks || {}) };
  openForceMenu.value = null;
  openToolMenu.value = null;
}

async function loadScope() {
  if (!secondaryVerified.value) return;
  if (!policyCache.value) {
    await fetchDefaults();
    return;
  }
  applyScopeConfig();
}

async function savePolicy() {
  if (!secondaryVerified.value) return;
  rebuildCategoryOverrides();
  saving.value = true;
  try {
    const resp = await fetch('/api/admin/policy', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        target_type: 'global',
        target_value: 'global',
        config: form.config
      })
    });
    const result = await resp.json();
    if (!resp.ok || !result.success) {
      throw new Error(result.error || t('adminPolicy.saveFailed'));
    }
    policyCache.value = result.data;
    lastUpdated.value = result.data?.updated_at || null;
    banner.message = t('adminPolicy.savedSuccess');
    banner.type = 'success';
  } catch (error: any) {
    banner.message = error?.message || t('adminPolicy.saveFailed');
    banner.type = 'error';
  } finally {
    saving.value = false;
  }
}

const handleVerifySecondary = async (password: string) => {
  await verifySecondary(password);
  if (secondaryVerified.value) {
    fetchDefaults().catch((err) => {
      console.error(err);
      banner.message = err?.message || t('adminPolicy.loadPolicyFailed');
      banner.type = 'error';
    });
  }
};

onMounted(async () => {
  document.addEventListener('click', handleDocClick);
  await checkSecondary();
  if (secondaryVerified.value) {
    fetchDefaults().catch((err) => {
      console.error(err);
      banner.message = err?.message || t('adminPolicy.loadPolicyFailed');
      banner.type = 'error';
    });
  }
});

watch(secondaryVerified, (val) => {
  if (val) {
    fetchDefaults().catch((err) => {
      console.error(err);
      banner.message = err?.message || t('adminPolicy.loadPolicyFailed');
      banner.type = 'error';
    });
  }
});

onBeforeUnmount(() => {
  document.removeEventListener('click', handleDocClick);
});
</script>

<style scoped>
:global(body) {
  margin: 0;
  background: #f7f3ea;
  font-family: 'Iowan Old Style', ui-serif, Georgia, Cambria, 'Times New Roman', serif;
  color: #2a2013;
}

:global(#admin-policy-app) {
  min-height: 100vh;
  padding: 24px 32px 48px;
  box-sizing: border-box;
}

.policy-page {
  max-width: 1200px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.policy-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.header-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.dropdown {
  position: relative;
}

.ghost-btn {
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid rgba(0, 0, 0, 0.12);
  background: rgba(255, 255, 255, 0.9);
  color: #2a2013;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}

.ghost-btn .caret {
  font-size: 12px;
  opacity: 0.7;
}

.dropdown-menu {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  min-width: 160px;
  background: #fff;
  border: 1px solid rgba(0, 0, 0, 0.12);
  border-radius: 12px;
  box-shadow: 0 12px 30px rgba(0, 0, 0, 0.12);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  z-index: 10;
}

.dropdown-menu button {
  border: none;
  background: transparent;
  padding: 10px 12px;
  text-align: left;
  width: 100%;
  color: #2a2013;
}

.dropdown-menu button:hover {
  background: rgba(0, 0, 0, 0.04);
}

.text-field input {
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid rgba(0, 0, 0, 0.12);
  background: #fff;
}

.banner {
  margin-top: 12px;
  padding: 12px 14px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  border: 1px solid rgba(0, 0, 0, 0.08);
}

.banner.success {
  background: rgba(73, 160, 120, 0.12);
  border-color: rgba(73, 160, 120, 0.35);
}

.banner.error {
  background: rgba(189, 93, 58, 0.12);
  border-color: rgba(189, 93, 58, 0.35);
}

.panel {
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(118, 103, 84, 0.2);
  border-radius: 16px;
  padding: 16px;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.4);
}

.error-text {
  color: #b05b3c;
}

.panel-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 12px;
}

.category-table {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.category-row {
  display: grid;
  grid-template-columns: 1.2fr 1.2fr 2fr 0.8fr 1fr 0.8fr;
  gap: 8px;
  align-items: center;
}

.category-head {
  font-weight: 600;
  color: #5b4d3b;
}

.category-row input,
.category-row select {
  padding: 8px 10px;
  border-radius: 10px;
  border: 1px solid rgba(0, 0, 0, 0.12);
  background: #fff;
}

.id-input {
  font-family:
    'SFMono-Regular', ui-monospace, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New',
    monospace;
}

.checkbox-cell {
  display: flex;
  justify-content: center;
}

.grid-2 {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 12px;
}

.checkbox-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 8px;
}

.toggle-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 10px;
}

.check-pill {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 14px;
  border: 1px solid rgba(0, 0, 0, 0.12);
  background: rgba(255, 255, 255, 0.92);
  cursor: pointer;
  transition: all 0.2s ease;
}

.check-pill.compact {
  padding: 8px 10px;
}

.check-pill .pill-knob {
  width: 34px;
  height: 18px;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.12);
  position: relative;
  transition: all 0.2s ease;
}

.check-pill .pill-knob::after {
  content: '';
  position: absolute;
  top: 2px;
  left: 2px;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
  transition: all 0.2s ease;
}

.check-pill.on {
  border-color: rgba(73, 160, 120, 0.45);
  box-shadow: 0 6px 18px rgba(73, 160, 120, 0.18);
}

.check-pill.on .pill-knob {
  background: linear-gradient(90deg, #49a078, #4fb28a);
}

.check-pill.on .pill-knob::after {
  transform: translateX(16px);
}

.check-pill .pill-label {
  font-weight: 600;
  color: #2a2013;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.chip {
  background: rgba(118, 103, 84, 0.12);
  border: 1px solid rgba(118, 103, 84, 0.3);
  border-radius: 999px;
  padding: 6px 10px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.muted {
  color: #8a7a62;
}

.muted-info {
  color: #6a5d4c;
  background: rgba(247, 243, 234, 0.9);
}

.link {
  background: transparent;
  border: 1px solid rgba(176, 91, 60, 0.28);
  color: #5b4d3b;
  cursor: pointer;
  padding: 6px 10px;
  border-radius: 10px;
  transition:
    background 0.15s ease,
    border-color 0.15s ease;
}

.link.danger {
  color: #b05b3c;
  border-color: rgba(176, 91, 60, 0.4);
  background: rgba(176, 91, 60, 0.06);
}

.link.danger:hover {
  background: rgba(176, 91, 60, 0.12);
  border-color: rgba(176, 91, 60, 0.55);
}

button {
  padding: 8px 12px;
  border-radius: 10px;
  border: 1px solid rgba(0, 0, 0, 0.12);
  background: #fff;
  cursor: pointer;
}

button.primary {
  background: linear-gradient(90deg, #4b2e14, #8b5d3b);
  color: #fff;
  border: none;
}

button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

/* 复用个人空间的勾选样式 */
.toggle-row {
  position: relative;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 14px;
  border: 1px solid var(--theme-control-border);
  background: var(--theme-surface-muted);
  cursor: pointer;
  transition:
    border-color 0.2s ease,
    box-shadow 0.2s ease,
    background 0.2s ease;
}

.toggle-row.compact {
  padding: 10px 12px;
}

.toggle-row:hover {
  border-color: var(--theme-control-border-strong);
  background: var(--theme-surface-soft);
}

.toggle-row input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

.toggle-row span {
  color: var(--text-primary);
  font-weight: 600;
}

.tool-select {
  position: relative;
}

.tool-select-trigger {
  width: 100%;
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 9px 10px;
  border-radius: 10px;
  border: 1px solid rgba(0, 0, 0, 0.12);
  background: #fff;
  cursor: pointer;
  min-height: 40px;
}

.tool-select.open .tool-select-trigger {
  border-color: rgba(118, 103, 84, 0.4);
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.08);
}

.tool-badges {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.tool-badge {
  display: inline-flex;
  align-items: center;
  padding: 4px 8px;
  border-radius: 999px;
  background: rgba(118, 103, 84, 0.12);
  color: #4b3d2f;
  border: 1px solid rgba(118, 103, 84, 0.2);
  font-size: 13px;
}

.tool-select-menu {
  position: absolute;
  z-index: 20;
  top: calc(100% + 6px);
  left: 0;
  min-width: 260px;
  max-height: 240px;
  background: #fffaf4;
  border: 1px solid rgba(118, 103, 84, 0.2);
  border-radius: 12px;
  box-shadow: 0 16px 36px rgba(0, 0, 0, 0.12);
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow: hidden;
}

.tool-select-search input {
  width: 100%;
  padding: 8px 10px;
  border-radius: 10px;
  border: 1px solid rgba(0, 0, 0, 0.12);
  background: #fff;
}

.tool-select-options {
  display: grid;
  grid-template-columns: 1fr;
  gap: 6px;
  max-height: 140px;
  overflow: auto;
  scrollbar-width: none;
  -ms-overflow-style: none;
}

.tool-select-options::-webkit-scrollbar {
  display: none;
}

.tool-select-options label {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.85);
  border: 1px solid rgba(0, 0, 0, 0.06);
  cursor: pointer;
}

.tool-select-options input {
  position: static;
  opacity: 1;
}

.tiny {
  font-size: 12px;
}
</style>
