<script setup lang="ts">
import { computed, inject, onMounted, unref, ref, watch } from 'vue';
import FancyCheck from '@/components/common/FancyCheck.vue';
import { useSettingsStore } from '@/stores/settings';

defineOptions({ name: 'SearchTab' });

/**
 * 网络搜索设置分区：Tavily API 密钥 + 网页直提白名单（自工具页迁入）。
 * 共享上下文由 usePersonalizationContext（PersonalizationDrawer / SettingsShell 各自 provide 同一份）注入。
 */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const { form, personalization } = ctx;

const settingsStore = useSettingsStore();
/** Tavily 密钥可见性：host 单机模式不受限；docker/web 多用户模式仅管理员可见
 *（对齐模型栏目中提供商分区的权限线——服务器密钥由管理员统一管控）。 */
const canEditTavilyKey = computed(() => settingsStore.isHostMode || settingsStore.isAdmin);
onMounted(() => {
  // 补拉会话状态（store 内去重），确保 isAdmin/isHostMode 判定就绪
  settingsStore.fetchSessionStatus();
});

/* ---------- Tavily API 密钥 ---------- */
/** 本地草稿（密码框双向绑定）；保存后才写入 form/store */
const tavilyKeyDraft = ref<string>(String((unref(form) as any)?.tavily_api_key || ''));
const tavilyKeyVisible = ref(false);

/* form 侧密钥被外部更新（如加载完成）时同步草稿；用户正在编辑时不覆盖 */
const tavilyKeyDirty = ref(false);
watch(
  () => (unref(form) as any)?.tavily_api_key,
  (value) => {
    if (!tavilyKeyDirty.value) {
      tavilyKeyDraft.value = String(value || '');
    }
  }
);

const saveTavilyKey = () => {
  personalization.updateField({ key: 'tavily_api_key', value: tavilyKeyDraft.value.trim() });
  tavilyKeyDirty.value = false;
};

const clearTavilyKey = () => {
  tavilyKeyDraft.value = '';
  tavilyKeyDirty.value = false;
  personalization.updateField({ key: 'tavily_api_key', value: '' });
};

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
    <!-- Tavily API 密钥（docker/web 模式仅管理员可见） -->
    <div v-if="canEditTavilyKey" class="settings-group-block">
      <div class="settings-group-title">
        <span class="settings-row-title">{{ $t('personalization.tavilyApiKeyTitle') }}</span
        ><span class="settings-row-desc">{{ $t('personalization.tavilyApiKeyDesc') }}</span>
      </div>
      <div class="settings-add-row">
        <div class="settings-key-input-wrap">
          <input
            v-model="tavilyKeyDraft"
            :type="tavilyKeyVisible ? 'text' : 'password'"
            :placeholder="$t('personalization.tavilyApiKeyPlaceholder')"
            autocomplete="off"
            spellcheck="false"
            @input="tavilyKeyDirty = true"
            @keydown.enter="saveTavilyKey"
          />
          <button
            type="button"
            class="settings-key-toggle"
            :class="{ active: tavilyKeyVisible }"
            :title="tavilyKeyVisible ? $t('personalization.tavilyApiKeyHide') : $t('personalization.tavilyApiKeyShow')"
            @click="tavilyKeyVisible = !tavilyKeyVisible"
          >
            <span class="icon icon-eye" aria-hidden="true"></span>
          </button>
        </div>
        <button type="button" class="settings-secondary-button" @click="saveTavilyKey">
          {{ $t('common.save') }}
        </button>
        <button
          v-if="form.tavily_api_key"
          type="button"
          class="settings-secondary-button"
          @click="clearTavilyKey"
        >
          {{ $t('personalization.tavilyApiKeyClear') }}
        </button>
      </div>
      <div class="settings-row-desc settings-key-status">
        {{
          form.tavily_api_key
            ? $t('personalization.tavilyApiKeyStatusCustom')
            : $t('personalization.tavilyApiKeyStatusEnv')
        }}
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
</style>
