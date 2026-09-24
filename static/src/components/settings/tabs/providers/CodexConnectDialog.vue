<script setup lang="ts">
import { ref } from 'vue';
import { useProvidersStore } from '@/stores/providers';
import type { ProviderCatalogEntry } from '@/stores/providers';
import { useModelStore } from '@/stores/model';
import SettingsModal from './SettingsModal.vue';
import ProviderIcon from './ProviderIcon.vue';
import { useCodexLogin } from './useCodexLogin';

/**
 * OpenAI · ChatGPT 订阅（auth=codex_oauth）连接对话框。
 * 提供「浏览器授权 / 无头设备码」两种方式，流程复用 CodexTab 的
 * /api/codex/login/* 链路（经 useCodexLogin 抽取，CodexTab 本身不改）。
 */
defineProps<{
  entry: ProviderCatalogEntry;
}>();

const emit = defineEmits<{ (e: 'close'): void }>();

const providersStore = useProvidersStore();
const modelStore = useModelStore();
const completed = ref(false);

const {
  busy,
  pending,
  devicePending,
  deviceStarting,
  exchanging,
  errorText,
  userCode,
  verificationUri,
  startBrowserLogin,
  startDeviceLogin,
  cancelLogin,
  openDevicePage,
  copyUserCode
} = useCodexLogin({
  onCompleted: () => {
    completed.value = true;
    // 登录成功：刷新目录连接状态；模型列表后台已刷新，稍等后同步前端选择器
    void providersStore.fetchCatalog(true);
    setTimeout(() => {
      modelStore.fetchModels().catch(() => {});
    }, 1500);
  }
});
</script>

<template>
  <SettingsModal
    :title="
      $t('settings.connectDialogTitle', { name: `${entry.name} · ${entry.badge || 'OAuth'}` })
    "
    @close="emit('close')"
  >
    <template #icon>
      <ProviderIcon
        :name="entry.name"
        :icon-url="entry.icon_url"
        :mono="!!entry.icon_mono"
        size="sm"
      />
    </template>

    <template v-if="completed">
      <p class="provider-form-note">{{ $t('settings.codexConnectSuccess') }}</p>
      <div class="provider-form-actions">
        <button type="button" class="provider-form-btn primary" @click="emit('close')">
          {{ $t('common.ok') }}
        </button>
      </div>
    </template>

    <template v-else-if="!pending">
      <p class="provider-form-note">{{ $t('settings.codexConnectDesc') }}</p>
      <div class="codex-connect-options">
        <button
          type="button"
          class="codex-connect-option"
          :disabled="busy"
          @click="startBrowserLogin"
        >
          <span class="codex-connect-option__main">
            <span class="codex-connect-option__label">{{ $t('settings.codexBrowserAuth') }}</span>
            <span class="codex-connect-option__desc">{{
              $t('settings.codexBrowserAuthDesc')
            }}</span>
          </span>
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path
              d="M6 3l5 5-5 5"
              stroke="currentColor"
              stroke-width="1.5"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
        </button>
        <button
          type="button"
          class="codex-connect-option"
          :disabled="busy"
          @click="startDeviceLogin"
        >
          <span class="codex-connect-option__main">
            <span class="codex-connect-option__label">{{ $t('settings.codexDeviceAuth') }}</span>
            <span class="codex-connect-option__desc">{{ $t('settings.codexDeviceAuthDesc') }}</span>
          </span>
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path
              d="M6 3l5 5-5 5"
              stroke="currentColor"
              stroke-width="1.5"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
        </button>
      </div>
      <div v-if="errorText" class="provider-form-error">{{ errorText }}</div>
    </template>

    <template v-else>
      <!-- 授权进行中：浏览器流等待提示 / 设备码面板（文案复用 personalization.codex*） -->
      <p v-if="exchanging" class="provider-form-note">
        {{ $t('personalization.codexLoginExchanging') }}
      </p>
      <div v-else-if="devicePending" class="codex-device-panel">
        <span v-if="deviceStarting" class="codex-device-panel__hint">
          {{ $t('personalization.codexLoginStartingDevice') }}
        </span>
        <template v-else-if="userCode">
          <span class="codex-device-panel__hint">
            {{ $t('personalization.codexLoginDeviceHint') }}
          </span>
          <span class="codex-device-code">
            <span class="codex-device-code__text">{{ userCode }}</span>
            <button type="button" class="provider-form-btn" @click="copyUserCode">
              {{ $t('common.copy') }}
            </button>
            <button
              v-if="verificationUri"
              type="button"
              class="provider-form-btn"
              @click="openDevicePage"
            >
              {{ $t('personalization.codexLoginDeviceOpen') }}
            </button>
          </span>
          <span class="codex-device-panel__hint">
            {{ $t('personalization.codexLoginPendingDevice') }}
          </span>
        </template>
      </div>
      <p v-else class="provider-form-note">{{ $t('personalization.codexLoginPending') }}</p>

      <div v-if="errorText" class="provider-form-error">{{ errorText }}</div>
      <div class="provider-form-actions">
        <button type="button" class="provider-form-btn" @click="cancelLogin">
          {{ $t('common.cancel') }}
        </button>
      </div>
    </template>
  </SettingsModal>
</template>

<style scoped>
.codex-connect-options {
  border-top: 1px solid var(--border-default);
}

.codex-connect-option {
  width: 100%;
  min-height: 52px;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 10px 2px;
  border: 0;
  border-bottom: 1px solid var(--border-default);
  background: transparent;
  color: var(--text-primary);
  text-align: left;
  cursor: pointer;
}

.codex-connect-option svg {
  color: var(--text-tertiary);
  flex-shrink: 0;
}

.codex-connect-option:hover:not(:disabled) {
  background: var(--hover-bg);
}

.codex-connect-option:disabled {
  opacity: 0.5;
  cursor: default;
}

.codex-connect-option__main {
  flex: 1;
  min-width: 0;
  display: block;
}

.codex-connect-option__label {
  display: block;
  font-size: 14px;
  font-weight: 500;
  line-height: 1.32;
}

.codex-connect-option__desc {
  display: block;
  margin-top: 1px;
  font-size: 12.5px;
  color: var(--text-tertiary);
  line-height: 1.45;
}

.codex-device-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.codex-device-panel__hint {
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.6;
}

.codex-device-code {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.codex-device-code__text {
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
  font-size: 20px;
  font-weight: 600;
  letter-spacing: 2px;
  color: var(--text-primary);
  user-select: all;
}
</style>
