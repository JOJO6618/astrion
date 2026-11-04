<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { t } from '@/locales';

defineOptions({ name: 'CodexWeeklyValue' });

/**
 * 本周期 GPT 请求折算价值（后端每小时采样，账本为金额唯一来源）。
 * 历史下拉只列出真正归档过的周期，没有数据就不显示条目。
 */
const props = defineProps<{ accountId: string }>();

interface PeriodEntry {
  period_start: number;
  period_end: number;
  used_percent: number;
  api_value_usd: string | null;
  max_value_usd: string | null;
  sampled_at?: string;
}

const current = ref<PeriodEntry | null>(null);
const history = ref<PeriodEntry[]>([]);
const loading = ref(true);
const error = ref('');
const menuOpen = ref(false);
const rootRef = ref<HTMLElement | null>(null);

let seq = 0;
let retryTimer: ReturnType<typeof setTimeout> | undefined;

const money = (raw: string | null | undefined): string => {
  if (raw === null || raw === undefined || raw === '') return '';
  const value = Number(raw);
  if (!Number.isFinite(value)) return '';
  return `$${value.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  })}`;
};

const day = (ts: number): string => {
  const d = new Date(Number(ts) * 1000);
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const date = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}/${month}/${date}`;
};

const periodLabel = (entry: PeriodEntry): string =>
  `${day(entry.period_start)} - ${day(entry.period_end)}`;

const currentValue = computed(() => money(current.value?.api_value_usd));
const maxValue = computed(() => money(current.value?.max_value_usd));
const hasData = computed(() => !!current.value || history.value.length > 0);

const load = async (refresh = false): Promise<void> => {
  const my = ++seq;
  if (refresh) loading.value = true;
  try {
    const params = new URLSearchParams();
    if (props.accountId) params.set('account_id', props.accountId);
    if (refresh) params.set('refresh', '1');
    const resp = await fetch(`/api/codex/weekly-value?${params.toString()}`);
    const data = await resp.json().catch(() => ({}));
    if (my !== seq) return;
    if (!resp.ok || !data.success) {
      error.value = String(data.error || '');
      return;
    }
    error.value = '';
    current.value = data.current || null;
    history.value = Array.isArray(data.history) ? data.history : [];
    // 首次触发采样后稍等再读一次，避免页面先看到空值。
    if (refresh && !current.value) {
      if (retryTimer) clearTimeout(retryTimer);
      retryTimer = setTimeout(() => void load(false), 6000);
    }
  } catch {
    if (my === seq) error.value = 'network_error';
  } finally {
    if (my === seq) loading.value = false;
  }
};

const toggleMenu = (): void => {
  if (!history.value.length) return;
  menuOpen.value = !menuOpen.value;
};

const onDocumentClick = (event: MouseEvent): void => {
  const root = rootRef.value;
  if (root && !root.contains(event.target as Node)) menuOpen.value = false;
};

onMounted(() => {
  void load(true);
  document.addEventListener('click', onDocumentClick);
});

watch(
  () => props.accountId,
  () => {
    menuOpen.value = false;
    void load(true);
  }
);

onBeforeUnmount(() => {
  if (retryTimer) clearTimeout(retryTimer);
  document.removeEventListener('click', onDocumentClick);
});
</script>

<template>
  <div ref="rootRef" class="codex-weekly">
    <div class="settings-section-divider">
      <span class="settings-section-divider__label">
        {{ t('personalization.codexWeeklySection') }}
      </span>
    </div>

    <div v-if="hasData" class="codex-weekly__rows">
      <div class="settings-input-row codex-weekly__row">
        <span class="settings-row-copy">
          <span class="settings-row-title">{{ t('personalization.codexWeeklyApiValue') }}</span>
        </span>
        <span class="codex-weekly__amount">{{ currentValue }}</span>
      </div>
      <div v-if="maxValue" class="settings-input-row codex-weekly__row">
        <span class="settings-row-copy">
          <span class="settings-row-title">{{ t('personalization.codexWeeklyMaxValue') }}</span>
        </span>
        <span class="codex-weekly__amount">{{ maxValue }}</span>
      </div>

      <div v-if="history.length" class="settings-input-row codex-weekly__row">
        <span class="settings-row-copy">
          <span class="settings-row-title">{{ t('personalization.codexWeeklyHistory') }}</span>
        </span>
        <div class="codex-weekly__select">
          <button
            type="button"
            class="codex-weekly__button"
            :aria-expanded="menuOpen"
            aria-haspopup="listbox"
            @click.stop="toggleMenu"
          >
            <span class="codex-weekly__button-label">{{ periodLabel(history[0]) }}</span>
            <svg
              class="codex-weekly__chevron"
              :class="{ 'codex-weekly__chevron--open': menuOpen }"
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
          </button>
          <div v-if="menuOpen" class="codex-weekly__menu" role="listbox">
            <div
              v-for="entry in history"
              :key="entry.period_end"
              class="codex-weekly__item"
              role="option"
              :aria-selected="false"
            >
              <span class="codex-weekly__item-range">{{ periodLabel(entry) }}</span>
              <span class="codex-weekly__item-value">{{ money(entry.api_value_usd) }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <p v-else-if="loading" class="codex-weekly__hint">
      {{ t('personalization.codexWeeklyLoading') }}
    </p>
    <p v-else-if="error" class="codex-weekly__hint">
      {{ t('personalization.codexWeeklyLoadFailed') }}
    </p>
  </div>
</template>

<style scoped>
.codex-weekly__rows {
  display: flex;
  flex-direction: column;
}

.codex-weekly__row {
  align-items: center;
}

.codex-weekly__amount {
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
  font-size: 13px;
  color: var(--text-primary);
}

.codex-weekly__select {
  position: relative;
  flex: 0 0 auto;
}

.codex-weekly__button {
  height: 32px;
  max-width: 280px;
  padding: 0 10px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  border: 1px solid var(--theme-control-border);
  border-radius: 6px;
  background: var(--surface-raised);
  color: var(--text-primary);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  cursor: pointer;
}

.codex-weekly__button:hover {
  background: var(--hover-bg);
}

.codex-weekly__button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}

.codex-weekly__button-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.codex-weekly__chevron {
  flex-shrink: 0;
  color: var(--text-tertiary);
  transition: transform 0.15s ease;
}

.codex-weekly__chevron--open {
  transform: rotate(180deg);
}

.codex-weekly__menu {
  position: absolute;
  right: 0;
  top: calc(100% + 6px);
  z-index: 20;
  min-width: 260px;
  max-width: 320px;
  max-height: 240px;
  overflow-y: auto;
  padding: 4px;
  border: 1px solid var(--border-default);
  border-radius: 8px;
  background: var(--surface-panel);
  box-shadow: var(--shadow-mid);
  scrollbar-width: thin;
}

.codex-weekly__item {
  height: 36px;
  padding: 0 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  border-radius: 5px;
  font-size: 13px;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}

.codex-weekly__item:hover {
  background: var(--hover-bg);
}

.codex-weekly__item-range {
  white-space: nowrap;
}

.codex-weekly__item-value {
  flex-shrink: 0;
  color: var(--text-secondary);
}

.codex-weekly__hint {
  margin: 0 0 16px;
  font-size: 13px;
  color: var(--text-tertiary);
}
</style>
