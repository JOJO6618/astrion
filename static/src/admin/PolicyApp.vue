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
        <div class="dropdown" :class="{ open: targetMenuOpen }">
          <button type="button" class="ghost-btn" @click="toggleTargetMenu">
            <span>{{ targetTypeLabel }}</span>
            <span class="caret">▾</span>
          </button>
          <div class="dropdown-menu" v-if="targetMenuOpen">
            <button
              v-for="opt in targetOptions()"
              :key="opt.value"
              type="button"
              @click="pickTargetType(opt.value)"
            >
              {{ opt.label }}
            </button>
          </div>
        </div>
        <div v-if="form.target_type !== 'global'" class="text-field">
          <input
            v-model="form.target_value"
            :placeholder="targetPlaceholder"
            class="target-input"
            type="text"
          />
        </div>
        <button type="button" class="ghost-btn" @click="loadScope">{{ $t('adminPolicy.load') }}</button>
        <button type="button" class="primary" :disabled="saving" @click="savePolicy">
          {{ saving ? $t('common.saving') : $t('common.save') }}
        </button>
      </div>
    </header>

    <transition name="fade">
      <div v-if="banner.message" class="banner" :class="banner.type">
        <span>{{ banner.message }}</span>
        <button type="button" class="banner-close" @click="banner.message = ''">×</button>
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
        <div class="category-row" v-for="cat in categoryList" :key="cat.id">
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
                <input v-model="toolSearch" type="text" :placeholder="$t('adminPolicy.toolSearchPlaceholder')" />
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
                <p v-if="!filteredToolOptions.length" class="muted tiny">{{ $t('adminPolicy.noMatchingTool') }}</p>
              </div>
              <button
                v-if="toolSearch.trim() && !toolOptionsSet.has(toolSearch.trim())"
                type="button"
                class="link small"
                @click="addCustomTool(cat)"
              >
                {{ $t('adminPolicy.addCustomToolWith', { id: toolSearch.trim() }) }}
              </button>
            </div>
          </div>
          <label class="toggle-row compact">
            <input
              type="checkbox"
              :checked="getCategoryDefault(cat.id)"
              @change="setCategoryDefault(cat.id, $event.target.checked)"
            />
            <FancyCheck :checked="getCategoryDefault(cat.id)" accent-checked />
            <span>{{ cat.default_enabled ? $t('adminPolicy.enabledOn') : $t('adminPolicy.enabledOff') }}</span>
          </label>
          <div class="dropdown" :class="{ open: openForceMenu === cat.id }">
            <button type="button" class="ghost-btn" @click="toggleForceMenu(cat.id)">
              <span>{{ forcedLabel(cat.forced) }}</span>
              <span class="caret">▾</span>
            </button>
            <div class="dropdown-menu" v-if="openForceMenu === cat.id">
              <button type="button" @click="setForced(cat.id, null)">{{ $t('adminPolicy.forcedNone') }}</button>
              <button type="button" @click="setForced(cat.id, true)">{{ $t('adminPolicy.forcedEnable') }}</button>
              <button type="button" @click="setForced(cat.id, false)">{{ $t('adminPolicy.forcedDisable') }}</button>
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
        <h2>{{ $t('adminPolicy.mcpSection') }}</h2>
        <div class="header-actions">
          <button type="button" class="ghost-btn" :disabled="mcpLoading" @click="fetchMcpServers">
            {{ mcpLoading ? $t('common.refreshing') : $t('adminPolicy.refresh') }}
          </button>
          <button type="button" class="ghost-btn" :disabled="mcpSyncing" @click="syncAllMcpServers">
            {{ mcpSyncing ? $t('adminPolicy.syncing') : $t('adminPolicy.syncAll') }}
          </button>
          <button type="button" @click="addMcpServer">{{ $t('adminPolicy.addMcpServer') }}</button>
        </div>
      </div>
      <div v-if="!mcpServers.length" class="muted">{{ $t('adminPolicy.emptyMcpServers') }}</div>
      <div class="mcp-list" v-else>
        <div class="mcp-item" v-for="(server, idx) in mcpServers" :key="server.id || `new-${idx}`">
          <div class="mcp-grid">
            <label>
              <span>{{ $t('adminPolicy.mcpId') }}</span>
              <input v-model="server.id" :placeholder="$t('adminPolicy.mcpIdPlaceholder')" />
            </label>
            <label>
              <span>{{ $t('adminPolicy.mcpName') }}</span>
              <input v-model="server.name" :placeholder="$t('adminPolicy.mcpNamePlaceholder')" />
            </label>
            <label>
              <span>Transport</span>
              <select v-model="server.transport">
                <option value="stdio">stdio</option>
                <option value="streamable_http">streamable_http</option>
              </select>
            </label>
            <label>
              <span>{{ $t('adminPolicy.mcpEnabled') }}</span>
              <select v-model="server.enabledText">
                <option value="true">true</option>
                <option value="false">false</option>
              </select>
            </label>
            <label v-if="server.transport === 'stdio'" class="wide">
              <span>command</span>
              <input v-model="server.command" :placeholder="$t('adminPolicy.mcpCommandPlaceholder')" />
            </label>
            <label v-if="server.transport === 'stdio'" class="wide">
              <span>{{ $t('adminPolicy.mcpArgs') }}</span>
              <textarea
                v-model="server.argsText"
                rows="3"
                :placeholder="$t('adminPolicy.mcpArgsPlaceholder')"
              ></textarea>
            </label>
            <label v-if="server.transport === 'stdio'" class="wide">
              <span>{{ $t('adminPolicy.mcpCwd') }}</span>
              <input v-model="server.cwd" :placeholder="$t('adminPolicy.mcpCwdPlaceholder')" />
            </label>
            <label v-if="server.transport === 'streamable_http'" class="wide">
              <span>url</span>
              <input v-model="server.url" :placeholder="$t('adminPolicy.mcpUrlPlaceholder')" />
            </label>
            <label class="wide">
              <span>{{ $t('adminPolicy.mcpHeaders') }}</span>
              <textarea
                v-model="server.headersText"
                rows="2"
                :placeholder="$t('adminPolicy.mcpHeadersPlaceholder')"
              ></textarea>
            </label>
            <label class="wide">
              <span>{{ $t('adminPolicy.mcpEnv') }}</span>
              <textarea v-model="server.envText" rows="2" :placeholder="$t('adminPolicy.mcpEnvPlaceholder')"></textarea>
            </label>
            <label>
              <span>timeout(s)</span>
              <input v-model="server.timeoutText" placeholder="25" />
            </label>
            <label class="wide">
              <span>{{ $t('adminPolicy.mcpIncludeTools') }}</span>
              <input v-model="server.includeToolsText" :placeholder="$t('adminPolicy.mcpIncludeToolsPlaceholder')" />
            </label>
            <label class="wide">
              <span>{{ $t('adminPolicy.mcpExcludeTools') }}</span>
              <input v-model="server.excludeToolsText" :placeholder="$t('adminPolicy.mcpExcludeToolsPlaceholder')" />
            </label>
            <label class="wide">
              <span>{{ $t('adminPolicy.mcpDescription') }}</span>
              <input v-model="server.description" :placeholder="$t('adminPolicy.mcpDescriptionPlaceholder')" />
            </label>
          </div>
          <div class="mcp-meta">
            <span>
              {{ $t('adminPolicy.mcpLastSync', { time: server.tools_cache_updated_at || $t('adminPolicy.mcpNotSynced') }) }}
            </span>
            <span v-if="server.last_error" class="error-text"
              >{{ $t('adminPolicy.mcpError', { message: server.last_error }) }}</span
            >
            <span>{{ $t('adminPolicy.mcpCacheCount', { count: server.tools_cache_count }) }}</span>
          </div>
          <div class="mcp-actions">
            <button type="button" class="primary" @click="saveMcpServer(server)">{{ $t('common.save') }}</button>
            <button type="button" class="ghost-btn" @click="syncMcpServer(server.id)">
              {{ $t('adminPolicy.syncTools') }}
            </button>
            <button type="button" class="link danger" @click="deleteMcpServer(server.id, idx)">
              {{ $t('common.delete') }}
            </button>
          </div>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-title">
        <h2>{{ $t('adminPolicy.removedCategories') }}</h2>
      </div>
      <div class="chips">
        <span v-if="!form.config.remove_categories.length" class="muted">{{ $t('adminPolicy.none') }}</span>
        <span v-for="cid in form.config.remove_categories" :key="cid" class="chip">
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
import { t, currentLocale } from '@/locales';

type TargetType = 'global' | 'role' | 'user' | 'invite';

interface RawPolicy {
  updated_at?: string;
  global?: any;
  roles?: Record<string, any>;
  users?: Record<string, any>;
  invites?: Record<string, any>;
}

interface CategoryFormItem {
  id: string;
  label: string;
  tools: string[];
  default_enabled: boolean;
  forced: boolean | null;
}

interface MCPServerFormItem {
  id: string;
  name: string;
  description: string;
  transport: 'stdio' | 'streamable_http';
  enabledText: 'true' | 'false';
  command: string;
  argsText: string;
  cwd: string;
  url: string;
  headersText: string;
  envText: string;
  timeoutText: string;
  includeToolsText: string;
  excludeToolsText: string;
  tools_cache_updated_at: string;
  last_error: string;
  tools_cache_count: number;
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
  target_type: 'global' as TargetType,
  target_value: '',
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
const mcpServers = ref<MCPServerFormItem[]>([]);
const mcpLoading = ref(false);
const mcpSyncing = ref(false);

const targetPlaceholder = computed(() => {
  void currentLocale.value;
  if (form.target_type === 'role') return t('adminPolicy.placeholderRole');
  if (form.target_type === 'invite') return t('adminPolicy.placeholderInvite');
  if (form.target_type === 'user') return t('adminPolicy.placeholderUsername');
  return '';
});

const targetOptions = () => [
  { value: 'global', label: t('adminPolicy.scopeGlobal') },
  { value: 'role', label: t('adminPolicy.scopeRole') },
  { value: 'user', label: t('adminPolicy.scopeUser') },
  { value: 'invite', label: t('adminPolicy.scopeInvite') }
] as const;

const targetTypeLabel = computed(() => {
  void currentLocale.value;
  return targetOptions().find((o) => o.value === form.target_type)?.label || t('adminPolicy.scopeGlobal');
});
const targetMenuOpen = ref(false);
const openForceMenu = ref<string | null>(null);
const openToolMenu = ref<string | null>(null);
const toolSearch = ref('');

const banner = reactive({ message: '', type: 'info' as 'info' | 'success' | 'error' });

const toolOptionsSet = computed<Set<string>>(() => {
  const set = new Set<string>();
  const addList = (list: any) => {
    if (!Array.isArray(list)) return;
    list.forEach((t) => {
      if (typeof t === 'string' && t.trim()) set.add(t.trim());
    });
  };
  Object.values(defaults.categories || {}).forEach((cat: any) => addList(cat?.tools));
  categoryList.value.forEach((cat) => addList(cat.tools));
  return set;
});

const toolOptions = computed(() => Array.from(toolOptionsSet.value).sort());

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

const toggleTargetMenu = () => {
  targetMenuOpen.value = !targetMenuOpen.value;
};

const pickTargetType = (value: TargetType) => {
  form.target_type = value;
  if (value === 'global') form.target_value = '';
  targetMenuOpen.value = false;
  openForceMenu.value = null;
  openToolMenu.value = null;
  applyScopeConfig();
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

const addCustomTool = (cat: CategoryFormItem) => {
  const val = toolSearch.value.trim();
  if (!val) return;
  const owners = toolAssignments.value[val] || [];
  const conflict = owners.find((id) => id !== cat.id);
  if (conflict) {
    banner.message = t('adminPolicy.toolConflict', { tool: val, category: conflict });
    banner.type = 'error';
    return;
  }
  toggleToolInCategory(cat, val);
  toolSearch.value = '';
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
  return Object.keys(map).map((id) => ({
    id,
    label: map[id]?.label || id,
    tools: Array.isArray(map[id]?.tools) ? [...map[id].tools] : [],
    default_enabled: map[id]?.default_enabled !== false,
    forced: form.config.forced_category_states[id] ?? null
  }));
});

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

function splitCsv(value: string): string[] {
  return (value || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

function linesToList(value: string): string[] {
  return (value || '')
    .split('\n')
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseJsonDict(label: string, raw: string): Record<string, string> {
  const text = (raw || '').trim();
  if (!text) return {};
  let parsed: any;
  try {
    parsed = JSON.parse(text);
  } catch (error: any) {
    throw new Error(t('adminPolicy.jsonInvalid', { label, message: error?.message || error }));
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error(t('adminPolicy.jsonMustBeObject', { label }));
  }
  const output: Record<string, string> = {};
  Object.entries(parsed).forEach(([k, v]) => {
    const key = String(k || '').trim();
    if (!key) return;
    output[key] = String(v ?? '');
  });
  return output;
}

function mapMcpServer(raw: any): MCPServerFormItem {
  const transport = raw?.transport === 'streamable_http' ? 'streamable_http' : 'stdio';
  const args = Array.isArray(raw?.args) ? raw.args.filter((x: any) => typeof x === 'string') : [];
  const toolsCache = Array.isArray(raw?.tools_cache) ? raw.tools_cache : [];
  return {
    id: String(raw?.id || ''),
    name: String(raw?.name || raw?.id || ''),
    description: String(raw?.description || ''),
    transport,
    enabledText: raw?.enabled === false ? 'false' : 'true',
    command: String(raw?.command || ''),
    argsText: args.join('\n'),
    cwd: String(raw?.cwd || ''),
    url: String(raw?.url || ''),
    headersText: raw?.headers && typeof raw.headers === 'object' ? JSON.stringify(raw.headers, null, 2) : '',
    envText: raw?.env && typeof raw.env === 'object' ? JSON.stringify(raw.env, null, 2) : '',
    timeoutText: String(raw?.timeout_seconds ?? 25),
    includeToolsText: Array.isArray(raw?.include_tools) ? raw.include_tools.join(',') : '',
    excludeToolsText: Array.isArray(raw?.exclude_tools) ? raw.exclude_tools.join(',') : '',
    tools_cache_updated_at: String(raw?.tools_cache_updated_at || ''),
    last_error: String(raw?.last_error || ''),
    tools_cache_count: toolsCache.length
  };
}

function normalizeMcpPayload(server: MCPServerFormItem) {
  const id = server.id.trim();
  if (!id) throw new Error(t('adminPolicy.mcpIdRequired'));
  return {
    id,
    name: server.name.trim() || id,
    description: server.description.trim(),
    transport: server.transport,
    enabled: server.enabledText === 'true',
    command: server.command.trim(),
    args: linesToList(server.argsText),
    cwd: server.cwd.trim(),
    url: server.url.trim(),
    headers: parseJsonDict('headers', server.headersText),
    env: parseJsonDict('env', server.envText),
    timeout_seconds: Number(server.timeoutText || 25),
    include_tools: splitCsv(server.includeToolsText),
    exclude_tools: splitCsv(server.excludeToolsText)
  };
}

async function fetchMcpServers() {
  if (!secondaryVerified.value) return;
  mcpLoading.value = true;
  try {
    const resp = await fetch('/api/admin/mcp-servers', { credentials: 'same-origin' });
    const data = await resp.json();
    if (!resp.ok || !data.success) {
      throw new Error(data.error || t('adminPolicy.loadMcpFailed'));
    }
    mcpServers.value = (data.data || []).map((item: any) => mapMcpServer(item));
  } catch (error: any) {
    banner.message = error?.message || t('adminPolicy.loadMcpFailed');
    banner.type = 'error';
  } finally {
    mcpLoading.value = false;
  }
}

function addMcpServer() {
  mcpServers.value.unshift(
    mapMcpServer({
      id: '',
      name: '',
      transport: 'stdio',
      enabled: true,
      timeout_seconds: 25
    })
  );
}

async function saveMcpServer(server: MCPServerFormItem) {
  if (!secondaryVerified.value) return;
  try {
    const payload = normalizeMcpPayload(server);
    const resp = await fetch('/api/admin/mcp-servers', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await resp.json();
    if (!resp.ok || !data.success) {
      throw new Error(data.error || t('adminPolicy.saveMcpFailed'));
    }
    banner.message = t('adminPolicy.mcpSaved', { id: payload.id });
    banner.type = 'success';
    await syncMcpServer(payload.id, true);
  } catch (error: any) {
    banner.message = error?.message || t('adminPolicy.saveMcpFailed');
    banner.type = 'error';
  }
}

async function deleteMcpServer(id: string, idx: number) {
  if (!secondaryVerified.value) return;
  const target = String(id || '').trim();
  if (!target) {
    mcpServers.value.splice(idx, 1);
    return;
  }
  try {
    const resp = await fetch(`/api/admin/mcp-servers?id=${encodeURIComponent(target)}`, {
      method: 'DELETE',
      credentials: 'same-origin'
    });
    const data = await resp.json();
    if (!resp.ok || !data.success) {
      throw new Error(data.error || t('adminPolicy.deleteMcpFailed'));
    }
    banner.message = t('adminPolicy.mcpDeleted', { id: target });
    banner.type = 'success';
    await fetchMcpServers();
    await fetchDefaults();
  } catch (error: any) {
    banner.message = error?.message || t('adminPolicy.deleteMcpFailed');
    banner.type = 'error';
  }
}

async function syncMcpServer(id?: string, silent = false) {
  if (!secondaryVerified.value) return;
  const target = String(id || '').trim();
  if (typeof id !== 'undefined' && !target) {
    banner.message = t('adminPolicy.syncRequiresSave');
    banner.type = 'error';
    return;
  }
  if (!silent) mcpSyncing.value = true;
  try {
    const resp = await fetch('/api/admin/mcp-servers/sync', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(target ? { id: target } : {})
    });
    const data = await resp.json();
    if (!resp.ok || !data.success) {
      throw new Error(data.error || t('adminPolicy.syncMcpFailed'));
    }
    if (!silent) {
      banner.message = target ? t('adminPolicy.mcpSyncedWith', { id: target }) : t('adminPolicy.mcpSyncedAll');
      banner.type = 'success';
    }
    await fetchMcpServers();
    await fetchDefaults();
  } catch (error: any) {
    banner.message = error?.message || t('adminPolicy.syncMcpFailed');
    banner.type = 'error';
  } finally {
    if (!silent) mcpSyncing.value = false;
  }
}

async function syncAllMcpServers() {
  await syncMcpServer();
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
  const p = policyCache.value;
  if (!p) return null;
  if (form.target_type === 'global') return p.global || {};
  if (form.target_type === 'role') return (p.roles || {})[form.target_value] || {};
  if (form.target_type === 'user') return (p.users || {})[form.target_value] || {};
  if (form.target_type === 'invite') return (p.invites || {})[form.target_value] || {};
  return null;
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
        target_type: form.target_type,
        target_value: form.target_type === 'global' ? 'global' : form.target_value,
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
    Promise.all([fetchDefaults(), fetchMcpServers()]).catch((err) => {
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
    Promise.all([fetchDefaults(), fetchMcpServers()]).catch((err) => {
      console.error(err);
      banner.message = err?.message || t('adminPolicy.loadPolicyFailed');
      banner.type = 'error';
    });
  }
});

watch(secondaryVerified, (val) => {
  if (val) {
    Promise.all([fetchDefaults(), fetchMcpServers()]).catch((err) => {
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

.banner-close {
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
}

.panel {
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(118, 103, 84, 0.2);
  border-radius: 16px;
  padding: 16px;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.4);
}

.mcp-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.mcp-item {
  border: 1px solid rgba(118, 103, 84, 0.2);
  border-radius: 14px;
  padding: 12px;
  background: rgba(255, 255, 255, 0.92);
}

.mcp-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px;
}

.mcp-grid label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: #5b4d3b;
  font-size: 13px;
}

.mcp-grid label.wide {
  grid-column: 1 / -1;
}

.mcp-grid textarea,
.mcp-grid input,
.mcp-grid select {
  width: 100%;
  box-sizing: border-box;
  padding: 8px 10px;
  border-radius: 10px;
  border: 1px solid rgba(0, 0, 0, 0.12);
  background: #fff;
}

.mcp-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 8px;
  color: #8a7a62;
  font-size: 12px;
}

.mcp-actions {
  margin-top: 8px;
  display: flex;
  gap: 8px;
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

.link.small {
  font-size: 13px;
  align-self: flex-start;
  padding: 6px 8px;
}

.tiny {
  font-size: 12px;
}

</style>
