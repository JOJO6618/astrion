<script setup lang="ts">
import { computed, inject, onMounted, reactive, unref, ref, watch } from 'vue';
import UiSelect from '@/components/ui/UiSelect.vue';
import UiSwitch from '@/components/ui/UiSwitch.vue';
import UiIcon from '@/components/ui/UiIcon.vue';
import { t } from '@/locales';
import { useSettingsStore } from '@/stores/settings';

defineOptions({ name: 'SearchTab' });

/**
 * 网络搜索设置分区：搜索引擎选择 + 各家密钥 + 网页提取方式选择 + 网页直提白名单。
 * 共享上下文由 usePersonalizationContext（PersonalizationDrawer / SettingsShell 各自 provide 同一份）注入。
 */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const { form, personalization } = ctx;

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
const providerItems = computed(() =>
  providerOptions.map((option) => ({ value: option.id, label: t(option.labelKey) }))
);

const selectProvider = (id: string) => {
  personalization.updateField({ key: 'search_provider', value: id });
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
const extractProviderItems = computed(() =>
  extractProviderOptions.map((option) => ({ value: option.id, label: t(option.labelKey) }))
);

const selectExtractProvider = (id: string) => {
  personalization.updateField({ key: 'webpage_extract_provider', value: id });
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
  <section class="stg-page">
    <!-- 搜索引擎 + 当前服务商凭证（docker/web 模式仅管理员可见） -->
    <template v-if="canEditSearchKeys">
      <div class="stg-section-divider">
        <span class="stg-section-divider__label">
          {{ $t('personalization.searchProviderTitle') }}
        </span>
      </div>
      <div class="stg-card">
        <div class="stg-select-row">
          <span class="stg-row-copy"
            ><span class="stg-row-title">{{ $t('personalization.searchProviderTitle') }}</span
            ><span class="stg-row-desc">{{ $t('personalization.searchProviderDesc') }}</span></span
          >
          <UiSelect
            :model-value="currentProvider"
            :options="providerItems"
            align-end
            @update:model-value="selectProvider"
          />
        </div>
        <div class="stg-textarea-row">
          <span class="stg-row-copy"
            ><span class="stg-row-title">{{ $t(currentCredential.titleKey) }}</span
            ><span class="stg-row-desc">{{
              providerCredential.hasValue.value
                ? $t(currentCredential.statusCustomKey)
                : $t(currentCredential.statusEnvKey)
            }}</span></span
          >
          <div class="stg-field">
            <input
              v-model="providerCredential.state.draft"
              :type="providerCredential.state.visible ? 'text' : 'password'"
              :placeholder="$t(currentCredential.placeholderKey)"
              :aria-label="$t(currentCredential.titleKey)"
              autocomplete="off"
              spellcheck="false"
              @input="providerCredential.state.dirty = true"
              @keydown.enter="providerCredential.save"
            />
            <button
              v-if="providerCredential.state.dirty"
              type="button"
              class="stg-field-action is-text"
              @click="providerCredential.save"
            >
              {{ $t('common.save') }}
            </button>
            <button
              v-else-if="providerCredential.hasValue.value"
              type="button"
              class="stg-field-action is-text"
              @click="providerCredential.clear"
            >
              {{ $t('personalization.tavilyApiKeyClear') }}
            </button>
            <button
              type="button"
              class="stg-field-action"
              :class="{ 'is-active': providerCredential.state.visible }"
              :aria-label="
                providerCredential.state.visible
                  ? $t('personalization.tavilyApiKeyHide')
                  : $t('personalization.tavilyApiKeyShow')
              "
              :title="
                providerCredential.state.visible
                  ? $t('personalization.tavilyApiKeyHide')
                  : $t('personalization.tavilyApiKeyShow')
              "
              @click="providerCredential.state.visible = !providerCredential.state.visible"
            >
              <UiIcon name="eye" />
            </button>
          </div>
        </div>
      </div>

      <!-- 网页提取方式（本地直提之后的云端提取商） -->
      <div class="stg-section-divider">
        <span class="stg-section-divider__label">
          {{ $t('personalization.extractProviderTitle') }}
        </span>
      </div>
      <div class="stg-card">
        <div class="stg-select-row">
          <span class="stg-row-copy"
            ><span class="stg-row-title">{{ $t('personalization.extractProviderTitle') }}</span
            ><span class="stg-row-desc">{{ $t('personalization.extractProviderDesc') }}</span></span
          >
          <UiSelect
            :model-value="currentExtractProvider"
            :options="extractProviderItems"
            align-end
            @update:model-value="selectExtractProvider"
          />
        </div>
        <div v-if="currentExtractProvider === 'jina'" class="stg-textarea-row">
          <span class="stg-row-copy"
            ><span class="stg-row-title">{{ $t('personalization.jinaApiKeyTitle') }}</span
            ><span class="stg-row-desc">{{
              jinaCredential.hasValue.value
                ? $t('personalization.tavilyApiKeyStatusCustom')
                : $t('personalization.jinaApiKeyStatusAnonymous')
            }}</span></span
          >
          <div class="stg-field">
            <input
              v-model="jinaCredential.state.draft"
              :type="jinaCredential.state.visible ? 'text' : 'password'"
              :placeholder="$t('personalization.jinaApiKeyPlaceholder')"
              :aria-label="$t('personalization.jinaApiKeyTitle')"
              autocomplete="off"
              spellcheck="false"
              @input="jinaCredential.state.dirty = true"
              @keydown.enter="jinaCredential.save"
            />
            <button
              v-if="jinaCredential.state.dirty"
              type="button"
              class="stg-field-action is-text"
              @click="jinaCredential.save"
            >
              {{ $t('common.save') }}
            </button>
            <button
              v-else-if="jinaCredential.hasValue.value"
              type="button"
              class="stg-field-action is-text"
              @click="jinaCredential.clear"
            >
              {{ $t('personalization.tavilyApiKeyClear') }}
            </button>
            <button
              type="button"
              class="stg-field-action"
              :class="{ 'is-active': jinaCredential.state.visible }"
              :aria-label="
                jinaCredential.state.visible
                  ? $t('personalization.tavilyApiKeyHide')
                  : $t('personalization.tavilyApiKeyShow')
              "
              :title="
                jinaCredential.state.visible
                  ? $t('personalization.tavilyApiKeyHide')
                  : $t('personalization.tavilyApiKeyShow')
              "
              @click="jinaCredential.state.visible = !jinaCredential.state.visible"
            >
              <UiIcon name="eye" />
            </button>
          </div>
        </div>
      </div>
    </template>

    <!-- 网页直提白名单（自工具页迁入） -->
    <div class="stg-section-divider">
      <span class="stg-section-divider__label">
        {{ $t('personalization.webDirectExtractTitle') }}
      </span>
    </div>
    <div class="stg-card">
      <label class="stg-toggle-row"
        ><span class="stg-row-copy"
          ><span class="stg-row-title">{{
            $t('personalization.webDirectExtractEnabledTitle')
          }}</span
          ><span class="stg-row-desc">{{
            $t('personalization.webDirectExtractEnabledDesc')
          }}</span></span
        ><input
          type="checkbox"
          :checked="form.webpage_direct_extract_enabled"
          @change="
            personalization.updateField({
              key: 'webpage_direct_extract_enabled',
              value: $event.target.checked
            })
          " /><UiSwitch decorative :checked="form.webpage_direct_extract_enabled"
      /></label>
      <template v-if="form.webpage_direct_extract_enabled">
        <div class="stg-domain-list">
          <div class="stg-domain-row">
            <span class="stg-row-title">github.com</span
            ><span class="stg-domain-badge">{{
              $t('personalization.webDirectExtractBuiltinBadge')
            }}</span>
          </div>
          <div v-for="domain in directExtractDomains()" :key="domain" class="stg-domain-row">
            <span class="stg-row-title">{{ domain }}</span
            ><button type="button" class="stg-domain-remove" @click="removeDirectDomain(domain)">
              {{ $t('common.delete') }}
            </button>
          </div>
        </div>
        <div class="stg-field stg-domain-add">
          <input
            v-model="newDirectDomain"
            type="text"
            :placeholder="$t('personalization.webDirectExtractDomainPlaceholder')"
            :aria-label="$t('personalization.webDirectExtractDomainPlaceholder')"
            spellcheck="false"
            @keydown.enter="addDirectDomain"
          />
          <button
            v-if="newDirectDomain.trim()"
            type="button"
            class="stg-field-action is-text"
            @click="addDirectDomain"
          >
            {{ $t('personalization.webDirectExtractAdd') }}
          </button>
        </div>
      </template>
    </div>
  </section>
</template>

<style scoped>
/* 卡片最后一个元素：到卡片底边 16（12 + 卡片 4），与左右距离一致 */
.stg-domain-add {
  margin: 4px 0 12px;
}
</style>
