<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { t } from '@/locales';
import { storeToRefs } from 'pinia';
import { useProvidersStore } from '@/stores/providers';
import type { ProviderCatalogEntry } from '@/stores/providers';
import { useUiStore } from '@/stores/ui';
import ProviderIcon from './providers/ProviderIcon.vue';
import ConnectDialog from './providers/ConnectDialog.vue';
import CodexConnectDialog from './providers/CodexConnectDialog.vue';
import CustomProviderDialog from './providers/CustomProviderDialog.vue';

defineOptions({ name: 'ProvidersTab' });

/**
 * 设置页「提供商」分区（仅管理员可见，导航层已挡）。
 * 已连接分区 + 目录分区（含自定义提供商入口行），行式列表对齐设计稿：
 * 图标 + 名称/徽标 + 第二行小字 Base URL + 右侧状态区/按钮。
 */

const providersStore = useProvidersStore();
const { catalog, loading, loadError, busyMap, connectedEntries, disconnectedEntries } =
  storeToRefs(providersStore);
const uiStore = useUiStore();

// ── 弹窗状态 ──
const connectTarget = ref<ProviderCatalogEntry | null>(null);
const codexTarget = ref<ProviderCatalogEntry | null>(null);
const customDialogOpen = ref(false);

// ── protocol_note 提示标记（hover/点击显示说明，自定义 tooltip） ──
const noteOpenFor = ref<string>('');

const toggleNote = (id: string) => {
  noteOpenFor.value = noteOpenFor.value === id ? '' : id;
};

const protocolNoteText = (entry: ProviderCatalogEntry): string => {
  if (entry.protocol_note === 'multi_protocol_partial') {
    const base = t('settings.protocolNoteMultiPartial');
    const count = entry.unsupported_protocol_count || 0;
    return count > 0 ? `${base} ${t('settings.protocolNoteMultiPartialCount', { count })}` : base;
  }
  return String(entry.protocol_note || '');
};

// ── 连接入口：按 auth 分发 ──
const openConnect = (entry: ProviderCatalogEntry) => {
  if (entry.auth === 'codex_oauth') {
    codexTarget.value = entry;
    return;
  }
  if (entry.auth === 'none') {
    // 本地无密钥条目（Ollama / LM Studio）：无需弹窗，直接连接
    void connectLocal(entry);
    return;
  }
  connectTarget.value = entry;
};

const connectLocal = async (entry: ProviderCatalogEntry) => {
  const result = await providersStore.connectCatalog(entry);
  if (result.ok) {
    uiStore.pushToast({
      message:
        result.modelsError || t('settings.connectSuccess', { count: result.modelsCount ?? 0 }),
      type: result.modelsError ? 'warning' : 'success'
    });
  } else {
    // 后端错误原样展示
    uiStore.pushToast({ message: result.error || t('common.unknownError'), type: 'error' });
  }
};

// ── 刷新 / 断开 ──
const refresh = async (entry: ProviderCatalogEntry) => {
  const result = await providersStore.refreshProvider(entry);
  if (!result.ok) {
    uiStore.pushToast({ message: result.error || t('common.unknownError'), type: 'error' });
  } else if (result.modelsError) {
    uiStore.pushToast({ message: result.modelsError, type: 'warning' });
  } else {
    uiStore.pushToast({
      message: t('settings.refreshSuccess', { count: result.modelsCount ?? 0 }),
      type: 'success'
    });
  }
};

const disconnect = async (entry: ProviderCatalogEntry) => {
  const ok = await uiStore.requestConfirm({
    title: t('settings.disconnectConfirmTitle'),
    message: t('settings.disconnectConfirmMessage', { name: entry.name }),
    confirmText: t('settings.providerDisconnect'),
    confirmVariant: 'danger'
  });
  if (!ok) return;
  const result = await providersStore.disconnectProvider(entry);
  if (!result.ok) {
    uiStore.pushToast({ message: result.error || t('common.unknownError'), type: 'error' });
  }
};

const busyOf = computed(() => busyMap.value);

const formatSyncTime = (iso?: string | null): string => {
  if (!iso) return '-';
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return String(iso);
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

const connectedDesc = (entry: ProviderCatalogEntry): string => {
  const parts = [entry.base_url];
  if (entry.models_fetched_at) {
    parts.push(t('settings.providerLastSync', { time: formatSyncTime(entry.models_fetched_at) }));
  }
  return parts.join(' · ');
};

onMounted(() => {
  void providersStore.fetchCatalog();
});
</script>

<template>
  <section class="settings-page">
    <div v-if="loading && !catalog.length" class="providers-state">{{ $t('common.loading') }}</div>
    <div v-else-if="loadError" class="providers-state providers-state--error">
      <span>{{ loadError }}</span>
      <button type="button" class="providers-btn" @click="providersStore.fetchCatalog(true)">
        {{ $t('common.retry') }}
      </button>
    </div>
    <template v-else>
      <!-- 已连接 -->
      <div v-if="connectedEntries.length" class="providers-section">
        <div class="providers-section-title">{{ $t('settings.providersConnectedSection') }}</div>
        <div class="providers-list">
          <div v-for="entry in connectedEntries" :key="entry.id" class="providers-row">
            <ProviderIcon :name="entry.name" :icon-url="entry.icon_url" :mono="!!entry.icon_mono" />
            <div class="providers-row__main">
              <div class="providers-row__name">
                <span class="providers-row__name-text">{{ entry.name }}</span>
                <span v-if="entry.badge" class="providers-badge">{{ entry.badge }}</span>
                <span v-if="entry.local" class="providers-badge">{{
                  $t('settings.localBadge')
                }}</span>
                <span
                  v-if="entry.protocol_note"
                  class="providers-note-marker"
                  tabindex="0"
                  @click.stop="toggleNote(entry.id)"
                  @mouseenter="noteOpenFor = entry.id"
                  @mouseleave="noteOpenFor === entry.id && (noteOpenFor = '')"
                >
                  <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                    <circle cx="8" cy="8" r="6.5" stroke="currentColor" stroke-width="1.3" />
                    <path
                      d="M8 7v4"
                      stroke="currentColor"
                      stroke-width="1.5"
                      stroke-linecap="round"
                    />
                    <circle cx="8" cy="4.8" r="0.9" fill="currentColor" />
                  </svg>
                  <span v-if="noteOpenFor === entry.id" class="providers-note-tip" role="tooltip">
                    {{ protocolNoteText(entry) }}
                  </span>
                </span>
              </div>
              <div class="providers-row__desc mono-url">{{ connectedDesc(entry) }}</div>
              <div v-if="entry.models_error" class="providers-row__error">
                {{ entry.models_error }}
              </div>
            </div>
            <span class="providers-status" :title="$t('settings.providerConnected')">
              <span class="providers-status__dot" aria-hidden="true"></span>
              <span v-if="typeof entry.models_count === 'number'" class="providers-status__count">
                {{ $t('settings.providerModelsCount', { count: entry.models_count }) }}
              </span>
              <span v-else class="providers-status__count">{{
                $t('settings.providerConnected')
              }}</span>
            </span>
            <button
              type="button"
              class="providers-btn"
              :disabled="!!busyOf[entry.id]"
              @click="refresh(entry)"
            >
              {{
                busyOf[entry.id] === 'refresh'
                  ? $t('common.refreshing')
                  : $t('settings.providerRefresh')
              }}
            </button>
            <button
              type="button"
              class="providers-btn providers-btn--danger"
              :disabled="!!busyOf[entry.id]"
              @click="disconnect(entry)"
            >
              {{
                busyOf[entry.id] === 'disconnect'
                  ? $t('settings.providerDisconnecting')
                  : $t('settings.providerDisconnect')
              }}
            </button>
          </div>
        </div>
      </div>

      <!-- 添加提供商（目录 + 自定义入口行） -->
      <div class="providers-section">
        <div class="providers-section-title">{{ $t('settings.providersCatalogSection') }}</div>
        <div class="providers-section-desc">{{ $t('settings.providersCatalogDesc') }}</div>
        <div class="providers-list">
          <div v-for="entry in disconnectedEntries" :key="entry.id" class="providers-row">
            <ProviderIcon :name="entry.name" :icon-url="entry.icon_url" :mono="!!entry.icon_mono" />
            <div class="providers-row__main">
              <div class="providers-row__name">
                <span class="providers-row__name-text">{{ entry.name }}</span>
                <span v-if="entry.badge" class="providers-badge">{{ entry.badge }}</span>
                <span v-if="entry.local" class="providers-badge">{{
                  $t('settings.localBadge')
                }}</span>
                <span
                  v-if="entry.protocol_note"
                  class="providers-note-marker"
                  tabindex="0"
                  @click.stop="toggleNote(entry.id)"
                  @mouseenter="noteOpenFor = entry.id"
                  @mouseleave="noteOpenFor === entry.id && (noteOpenFor = '')"
                >
                  <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                    <circle cx="8" cy="8" r="6.5" stroke="currentColor" stroke-width="1.3" />
                    <path
                      d="M8 7v4"
                      stroke="currentColor"
                      stroke-width="1.5"
                      stroke-linecap="round"
                    />
                    <circle cx="8" cy="4.8" r="0.9" fill="currentColor" />
                  </svg>
                  <span v-if="noteOpenFor === entry.id" class="providers-note-tip" role="tooltip">
                    {{ protocolNoteText(entry) }}
                  </span>
                </span>
              </div>
              <div class="providers-row__desc mono-url">{{ entry.base_url }}</div>
            </div>
            <button
              type="button"
              class="providers-btn"
              :disabled="!!busyOf[entry.id]"
              @click="openConnect(entry)"
            >
              {{
                busyOf[entry.id] === 'connect'
                  ? $t('settings.providerConnecting')
                  : $t('settings.providerConnect')
              }}
            </button>
          </div>

          <!-- 自定义提供商入口行 -->
          <div class="providers-row">
            <span class="providers-custom-icon" aria-hidden="true">✦</span>
            <div class="providers-row__main">
              <div class="providers-row__name">
                <span class="providers-row__name-text">{{
                  $t('settings.customProviderTitle')
                }}</span>
                <span class="providers-badge">{{ $t('settings.customProviderBadge') }}</span>
              </div>
              <div class="providers-row__desc">{{ $t('settings.customProviderDesc') }}</div>
            </div>
            <button type="button" class="providers-btn" @click="customDialogOpen = true">
              {{ $t('settings.providerConnect') }}
            </button>
          </div>
        </div>
      </div>
    </template>

    <!-- 弹窗 -->
    <ConnectDialog v-if="connectTarget" :entry="connectTarget" @close="connectTarget = null" />
    <CodexConnectDialog v-if="codexTarget" :entry="codexTarget" @close="codexTarget = null" />
    <CustomProviderDialog v-if="customDialogOpen" @close="customDialogOpen = false" />
  </section>
</template>

<style scoped>
.providers-state {
  padding: 14px 2px;
  border-top: 1px solid var(--border-default);
  border-bottom: 1px solid var(--border-default);
  font-size: 13px;
  color: var(--text-tertiary);
  display: flex;
  align-items: center;
  gap: 10px;
}

.providers-state--error {
  color: var(--state-danger);
}

.providers-section {
  margin-bottom: 32px;
}

.providers-section-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  height: 22px;
  line-height: 22px;
  margin-bottom: 4px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.providers-section-desc {
  font-size: 13px;
  color: var(--text-tertiary);
  margin-bottom: 12px;
}

/* ── 行式列表（分隔线，无卡片网格） ── */
.providers-list {
  border-top: 1px solid var(--border-default);
}

.providers-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 2px;
  border-bottom: 1px solid var(--border-default);
  min-height: 64px;
}

.providers-row__main {
  flex: 1;
  min-width: 0;
}

.providers-row__name {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 20px;
  min-width: 0;
}

.providers-row__name-text {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.providers-row__desc {
  font-size: 12.5px;
  color: var(--text-tertiary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-top: 2px;
}

.providers-row__desc.mono-url {
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
  font-size: 12px;
}

.providers-row__error {
  font-size: 12px;
  color: var(--state-warning);
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.providers-badge {
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

/* ── protocol_note 提示标记 + 自定义 tooltip ── */
.providers-note-marker {
  position: relative;
  width: 18px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--state-warning);
  cursor: pointer;
  flex-shrink: 0;
}

.providers-note-tip {
  position: absolute;
  left: 50%;
  bottom: calc(100% + 6px);
  transform: translateX(-50%);
  max-width: 260px;
  width: max-content;
  padding: 6px 10px;
  border-radius: 6px;
  background: var(--surface-panel);
  border: 1px solid var(--border-default);
  box-shadow: var(--shadow-mid);
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.5;
  white-space: normal;
  z-index: 10;
}

/* ── 右侧状态区 ── */
.providers-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  flex-shrink: 0;
}

.providers-status__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--state-success);
  flex-shrink: 0;
}

.providers-status__count {
  font-size: 12px;
  color: var(--state-success);
  white-space: nowrap;
}

/* ── 按钮（对齐设计稿 .btn.sm） ── */
.providers-btn {
  height: 28px;
  padding: 0 10px;
  border-radius: 6px;
  font-size: 12px;
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

.providers-btn:hover:not(:disabled) {
  background: var(--surface-soft);
}

.providers-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.providers-btn--danger {
  color: var(--state-danger);
}

.providers-custom-icon {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  flex-shrink: 0;
  background: var(--surface-card);
  border: 1px solid var(--border-default);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  color: var(--text-secondary);
  line-height: 1;
}
</style>
