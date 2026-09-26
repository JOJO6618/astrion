<script setup lang="ts">
import { computed, inject, onMounted, reactive, unref, ref, watch } from 'vue';
import FancyCheck from '@/components/common/FancyCheck.vue';
import { useSettingsStore } from '@/stores/settings';

defineOptions({ name: 'SearchTab' });

/**
 * 网络搜索设置分区：搜索引擎选择 + 各家密钥 + 网页提取方式选择 + 网页直提白名单。
 * 共享上下文由 usePersonalizationContext（PersonalizationDrawer / SettingsShell 各自 provide 同一份）注入。
 */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const { form, personalization, activeDropdown, activeTheme, floatingMenuStyle, toggleDropdown } = ctx;

const settingsStore = useSettingsStore();
/** 服务端密钥可见性：host 单机模式不受限；docker/web 多用户模式仅管理员可见
 *（对齐模型栏目中提供商分区的权限线——服务器密钥由管理员统一管控）。 */
const canEditSearchKeys = computed(() => settingsStore.isHostMode || settingsStore.isAdmin);
onMounted(() => {
  // 补拉会话状态（store 内去重），确保 isAdmin/isHostMode 判定就绪
  settingsStore.fetchSessionStatus();
});

/* ---------- 搜索引擎选择 ---------- */
const providerOptions = [
  { id: 'tavily', labelKey: 'personalization.searchProviderTavily' },
  { id: 'bocha', labelKey: 'personalization.searchProviderBocha' },
  { id: 'exa', labelKey: 'personalization.searchProviderExa' },
  { id: 'parallel', labelKey: 'personalization.searchProviderParallel' },
  { id: 'searxng', labelKey: 'personalization.searchProviderSearxng' }
];

const currentProvider = computed(() => {
  const value = String((unref(form) as any)?.search_provider || 'tavily');
  return providerOptions.some((o) => o.id === value) ? value : 'tavily';
});
const currentProviderOption = computed(
  () => providerOptions.find((o) => o.id === currentProvider.value) || providerOptions[0]
);

const selectProvider = (id: string) => {
  personalization.updateField({ key: 'search_provider', value: id });
  toggleDropdown('searchProvider');
};

/* ---------- 网页提取方式选择（白名单直提之后的云端提取商） ---------- */
const extractProviderOptions = [
  { id: 'jina', labelKey: 'personalization.extractProviderJina' },
  { id: 'tavily', labelKey: 'personalization.extractProviderTavily' },
  { id: 'exa', labelKey: 'personalization.extractProviderExa' },
  { id: 'parallel', labelKey: 'personalization.extractProviderParallel' }
];

const currentExtractProvider = computed(() => {
  const value = String((unref(form) as any)?.webpage_extract_provider || 'jina');
  return extractProviderOptions.some((o) => o.id === value) ? value : 'jina';
});
const currentExtractProviderOption = computed(
  () => extractProviderOptions.find((o) => o.id === currentExtractProvider.value) || extractProviderOptions[0]
);

const selectExtractProvider = (id: string) => {
  personalization.updateField({ key: 'webpage_extract_provider', value: id });
  toggleDropdown('extractProvider');
};

/* ---------- 凭证输入（当前服务商密钥 / SearXNG 地址 / Jina 密钥通用草稿机制） ---------- */
interface CredentialFieldConfig {
  field: string;
  titleKey: string;
  descKey: string;
  placeholderKey: string;
  statusCustomKey: string;
  statusEnvKey: string;
}

const CREDENTIAL_FIELDS: Record<string, CredentialFieldConfig> = {
  tavily: {
    field: 'tavily_api_key',
    titleKey: 'personalization.tavilyApiKeyTitle',
    descKey: 'personalization.tavilyApiKeyDesc',
    placeholderKey: 'personalization.tavilyApiKeyPlaceholder',
    statusCustomKey: 'personalization.tavilyApiKeyStatusCustom',
    statusEnvKey: 'personalization.tavilyApiKeyStatusEnv'
  },
  bocha: {
    field: 'bocha_api_key',
    titleKey: 'personalization.bochaApiKeyTitle',
    descKey: 'personalization.bochaApiKeyDesc',
    placeholderKey: 'personalization.bochaApiKeyPlaceholder',
    statusCustomKey: 'personalization.tavilyApiKeyStatusCustom',
    statusEnvKey: 'personalization.tavilyApiKeyStatusEnv'
  },
  exa: {
    field: 'exa_api_key',
    titleKey: 'personalization.exaApiKeyTitle',
    descKey: 'personalization.exaApiKeyDesc',
    placeholderKey: 'personalization.exaApiKeyPlaceholder',
    statusCustomKey: 'personalization.tavilyApiKeyStatusCustom',
    statusEnvKey: 'personalization.tavilyApiKeyStatusEnv'
  },
  parallel: {
    field: 'parallel_api_key',
    titleKey: 'personalization.parallelApiKeyTitle',
    descKey: 'personalization.parallelApiKeyDesc',
    placeholderKey: 'personalization.parallelApiKeyPlaceholder',
    statusCustomKey: 'personalization.tavilyApiKeyStatusCustom',
    statusEnvKey: 'personalization.tavilyApiKeyStatusEnv'
  },
  searxng: {
    field: 'searxng_base_url',
    titleKey: 'personalization.searxngBaseUrlTitle',
    descKey: 'personalization.searxngBaseUrlDesc',
    placeholderKey: 'personalization.searxngBaseUrlPlaceholder',
    statusCustomKey: 'personalization.searxngBaseUrlStatusCustom',
    statusEnvKey: 'personalization.searxngBaseUrlStatusEnv'
  }
};

/**
 * 凭证草稿通用机制：本地草稿双向绑定，保存后才写入 form/store；
 * form 侧被外部更新（如加载完成）时同步草稿，用户正在编辑时不覆盖。
 */
const useCredentialDraft = (fieldGetter: () => string) => {
  const state = reactive({ draft: '', visible: false, dirty: false });
  const sync = () => {
    if (!state.dirty) {
      state.draft = String((unref(form) as any)?.[fieldGetter()] || '');
    }
  };
  // 字段切换（如换了搜索引擎）时重置草稿与可见性
  watch(fieldGetter, () => {
    state.dirty = false;
    state.visible = false;
    sync();
  });
  watch(() => (unref(form) as any)?.[fieldGetter()], sync);
  onMounted(sync);
  const save = () => {
    personalization.updateField({ key: fieldGetter(), value: state.draft.trim() });
    state.dirty = false;
  };
  const clear = () => {
    state.draft = '';
    state.dirty = false;
    personalization.updateField({ key: fieldGetter(), value: '' });
  };
  const hasValue = computed(() => Boolean((unref(form) as any)?.[fieldGetter()]));
  return { state, save, clear, hasValue };
};

const currentCredential = computed(() => CREDENTIAL_FIELDS[currentProvider.value]);
const providerCredential = useCredentialDraft(() => currentCredential.value.field);
const jinaCredential = useCredentialDraft(() => 'jina_api_key');

/* ---------- 网页直提白名单（内置 github.com 不在此列，直接展示内置徽标） ---------- */
const directExtractDomains = (): string[] => {
  const f: any = unref(form);
  return Array.isArray(f?.webpage_direct_extract_domains) ? f.webpage_direct_extract_domains : [];
};

/** 新增域名输入框的本地草稿 */
const newDirectDomain = ref('');

const addDirectDomain = () => {
  let value = newDirectDomain.value.trim().toLowerCase();
  if (!value) return;
  // 容忍粘贴完整 URL：提取 hostname 部分
  if (value.includes('://')) {
    try {
      value = new URL(value).hostname;
    } catch {
      return;
    }
  }
  value = value.split('/')[0].replace(/^\.+|\.+$/g, '');
  if (!value || !value.includes('.')) return;
  const current = [...directExtractDomains()];
  if (!current.includes(value)) {
    current.push(value);
    personalization.updateField({ key: 'webpage_direct_extract_domains', value: current });
  }
  newDirectDomain.value = '';
};

const removeDirectDomain = (domain: string) => {
  personalization.updateField({
    key: 'webpage_direct_extract_domains',
    value: directExtractDomains().filter((d) => d !== domain)
  });
};
</script>

<template>
  <section class="settings-page">
    <!-- 搜索引擎选择 + 当前服务商凭证（docker/web 模式仅管理员可见） -->
    <div v-if="canEditSearchKeys" class="settings-group-block">
      <div class="settings-group-title">
        <span class="settings-row-title">{{ $t('personalization.searchProviderTitle') }}</span
        ><span class="settings-row-desc">{{ $t('personalization.searchProviderDesc') }}</span>
      </div>
      <div class="settings-select-row">
        <span class="settings-row-copy"
          ><span class="settings-row-title">{{ $t(currentProviderOption.labelKey) }}</span></span
        >
        <div
          class="settings-select-wrap"
          :class="{ open: activeDropdown === 'searchProvider' }"
          @click.stop
        >
          <button
            type="button"
            class="settings-select-button"
            @click="toggleDropdown('searchProvider')"
          >
            {{ $t(currentProviderOption.labelKey) }}
            <span class="select-chevron" aria-hidden="true"></span>
          </button>
          <div
            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
            :style="activeDropdown ? floatingMenuStyle : undefined"
          >
            <button
              v-for="option in providerOptions"
              :key="option.id"
              type="button"
              class="settings-menu-option"
              :class="{ selected: currentProvider === option.id }"
              @click="selectProvider(option.id)"
            >
              <strong>{{ $t(option.labelKey) }}</strong
              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
            </button>
          </div>
        </div>
      </div>

      <!-- 当前搜索引擎的密钥 / 实例地址 -->
      <div class="settings-credential-block">
        <div class="settings-group-title">
          <span class="settings-row-title">{{ $t(currentCredential.titleKey) }}</span
          ><span class="settings-row-desc">{{ $t(currentCredential.descKey) }}</span>
        </div>
        <div class="settings-add-row">
          <div class="settings-key-input-wrap">
            <input
              v-model="providerCredential.state.draft"
              :type="providerCredential.state.visible ? 'text' : 'password'"
              :placeholder="$t(currentCredential.placeholderKey)"
              autocomplete="off"
              spellcheck="false"
              @input="providerCredential.state.dirty = true"
              @keydown.enter="providerCredential.save"
            />
            <button
              type="button"
              class="settings-key-toggle"
              :class="{ active: providerCredential.state.visible }"
              :title="
                providerCredential.state.visible
                  ? $t('personalization.tavilyApiKeyHide')
                  : $t('personalization.tavilyApiKeyShow')
              "
              @click="providerCredential.state.visible = !providerCredential.state.visible"
            >
              <span class="icon icon-eye" aria-hidden="true"></span>
            </button>
          </div>
          <button type="button" class="settings-secondary-button" @click="providerCredential.save">
            {{ $t('common.save') }}
          </button>
          <button
            v-if="providerCredential.hasValue.value"
            type="button"
            class="settings-secondary-button"
            @click="providerCredential.clear"
          >
            {{ $t('personalization.tavilyApiKeyClear') }}
          </button>
        </div>
        <div class="settings-row-desc settings-key-status">
          {{
            providerCredential.hasValue.value
              ? $t(currentCredential.statusCustomKey)
              : $t(currentCredential.statusEnvKey)
          }}
        </div>
      </div>
    </div>

    <!-- 网页提取方式（本地直提之后的云端提取商；docker/web 模式仅管理员可见） -->
    <div v-if="canEditSearchKeys" class="settings-group-block">
      <div class="settings-group-title">
        <span class="settings-row-title">{{ $t('personalization.extractProviderTitle') }}</span
        ><span class="settings-row-desc">{{ $t('personalization.extractProviderDesc') }}</span>
      </div>
      <div class="settings-select-row">
        <span class="settings-row-copy"
          ><span class="settings-row-title">{{ $t(currentExtractProviderOption.labelKey) }}</span></span
        >
        <div
          class="settings-select-wrap"
          :class="{ open: activeDropdown === 'extractProvider' }"
          @click.stop
        >
          <button
            type="button"
            class="settings-select-button"
            @click="toggleDropdown('extractProvider')"
          >
            {{ $t(currentExtractProviderOption.labelKey) }}
            <span class="select-chevron" aria-hidden="true"></span>
          </button>
          <div
            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
            :style="activeDropdown ? floatingMenuStyle : undefined"
          >
            <button
              v-for="option in extractProviderOptions"
              :key="option.id"
              type="button"
              class="settings-menu-option"
              :class="{ selected: currentExtractProvider === option.id }"
              @click="selectExtractProvider(option.id)"
            >
              <strong>{{ $t(option.labelKey) }}</strong
              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
            </button>
          </div>
        </div>
      </div>

      <!-- Jina 密钥（仅当选定 Jina 时显示；匿名可用故为可选项） -->
      <div v-if="currentExtractProvider === 'jina'" class="settings-credential-block">
        <div class="settings-group-title">
          <span class="settings-row-title">{{ $t('personalization.jinaApiKeyTitle') }}</span
          ><span class="settings-row-desc">{{ $t('personalization.jinaApiKeyDesc') }}</span>
        </div>
        <div class="settings-add-row">
          <div class="settings-key-input-wrap">
            <input
              v-model="jinaCredential.state.draft"
              :type="jinaCredential.state.visible ? 'text' : 'password'"
              :placeholder="$t('personalization.jinaApiKeyPlaceholder')"
              autocomplete="off"
              spellcheck="false"
              @input="jinaCredential.state.dirty = true"
              @keydown.enter="jinaCredential.save"
            />
            <button
              type="button"
              class="settings-key-toggle"
              :class="{ active: jinaCredential.state.visible }"
              :title="
                jinaCredential.state.visible
                  ? $t('personalization.tavilyApiKeyHide')
                  : $t('personalization.tavilyApiKeyShow')
              "
              @click="jinaCredential.state.visible = !jinaCredential.state.visible"
            >
              <span class="icon icon-eye" aria-hidden="true"></span>
            </button>
          </div>
          <button type="button" class="settings-secondary-button" @click="jinaCredential.save">
            {{ $t('common.save') }}
          </button>
          <button
            v-if="jinaCredential.hasValue.value"
            type="button"
            class="settings-secondary-button"
            @click="jinaCredential.clear"
          >
            {{ $t('personalization.tavilyApiKeyClear') }}
          </button>
        </div>
        <div class="settings-row-desc settings-key-status">
          {{
            jinaCredential.hasValue.value
              ? $t('personalization.tavilyApiKeyStatusCustom')
              : $t('personalization.jinaApiKeyStatusAnonymous')
          }}
        </div>
      </div>
    </div>

    <!-- 网页直提白名单（自工具页迁入） -->
    <div class="settings-group-block">
      <div class="settings-group-title">
        <span class="settings-row-title">{{ $t('personalization.webDirectExtractTitle') }}</span
        ><span class="settings-row-desc">{{ $t('personalization.webDirectExtractDesc') }}</span>
      </div>
      <label class="settings-toggle-row inner"
        ><span class="settings-row-title">{{ $t('personalization.webDirectExtractEnabledTitle') }}</span
        ><input
          type="checkbox"
          :checked="form.webpage_direct_extract_enabled"
          @change="
            personalization.updateField({
              key: 'webpage_direct_extract_enabled',
              value: $event.target.checked
            })
          " /><FancyCheck :checked="form.webpage_direct_extract_enabled" /></label>
      <template v-if="form.webpage_direct_extract_enabled">
        <div class="settings-domain-list">
          <div class="settings-domain-row">
            <span class="settings-row-title">github.com</span
            ><span class="settings-domain-badge">{{
              $t('personalization.webDirectExtractBuiltinBadge')
            }}</span>
          </div>
          <div v-for="domain in directExtractDomains()" :key="domain" class="settings-domain-row">
            <span class="settings-row-title">{{ domain }}</span
            ><button
              type="button"
              class="settings-domain-remove"
              @click="removeDirectDomain(domain)"
            >
              {{ $t('common.delete') }}
            </button>
          </div>
        </div>
        <div class="settings-add-row">
          <input
            v-model="newDirectDomain"
            type="text"
            :placeholder="$t('personalization.webDirectExtractDomainPlaceholder')"
            @keydown.enter="addDirectDomain"
          />
          <button type="button" class="settings-secondary-button" @click="addDirectDomain">
            {{ $t('personalization.webDirectExtractAdd') }}
          </button>
        </div>
      </template>
    </div>
  </section>
</template>

<style scoped>
/* 密钥输入框：密码框 + 内嵌眼睛切换按钮 */
.settings-key-input-wrap {
  position: relative;
  flex: 1;
  display: flex;
  align-items: center;
}

.settings-key-input-wrap input {
  width: 100%;
  padding-right: 34px;
}

.settings-key-toggle {
  position: absolute;
  right: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
}

.settings-key-toggle:hover {
  color: var(--text-primary);
  background: var(--hover-bg);
}

.settings-key-toggle .icon {
  width: 14px;
  height: 14px;
  display: block;
  background: currentColor;
  -webkit-mask-size: contain;
  mask-size: contain;
  -webkit-mask-repeat: no-repeat;
  mask-repeat: no-repeat;
  -webkit-mask-position: center;
  mask-position: center;
}

.settings-key-toggle .icon-eye {
  -webkit-mask-image: url('/static/icons/eye.svg');
  mask-image: url('/static/icons/eye.svg');
}

/* 明文可见状态：眼睛图标用主色高亮区分 */
.settings-key-toggle.active {
  color: var(--text-primary);
  background: var(--hover-bg);
}

.settings-key-status {
  margin-top: 6px;
  font-size: 12px;
}

/* 当前服务商凭证区：与上方选择器保持间距，不再额外包卡片 */
.settings-credential-block {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--border-default);
}
</style>
