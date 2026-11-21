import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { t, currentLocale } from '@/locales';
import type { ModelOption } from '@/stores/model';
import { usePersonalizationStore } from '@/stores/personalization';
import { useChatgptQuota } from './useChatgptQuota';

/**
 * 订阅额度统一入口（2026-11-16）。
 *
 * 来源由设置决定：「自动」跟随当前模型所属提供商，也可固定只显示某一家的额度。
 * - OpenCode Go：`providerId` 为 `ocgo`（目录条目 id）或其连接后缀；
 * - ChatGPT 订阅：模型的 `isChatgptModel`（Codex OAuth 通道）。
 *
 * 固定选择时即使当前模型不属于该供应商也保留卡片，只是内容显示为不可用；
 * 「自动」判定不出供应商时不显示，由调用方回退旧的上下文提示。
 */
export type SubscriptionSource = 'opencode' | 'chatgpt';

export interface QuotaWindowView {
  key: string;
  label: string;
  percent: number;
  resetText: string;
}

/** 目录条目 `ocgo`（OpenCode Go）；多连接内部 id 形如 `ocgo-<8位随机>`。 */
const OPENCODE_GO_CATALOG_ID = 'ocgo';
const REFRESH_MS = 60_000;

export function isOpencodeGoProvider(providerId?: string | null): boolean {
  const id = String(providerId || '');
  return id === OPENCODE_GO_CATALOG_ID || id.startsWith(`${OPENCODE_GO_CATALOG_ID}-`);
}

interface RawWindow {
  key?: string;
  percent?: number;
  reset_at?: string;
}

const WINDOW_LABEL_KEY: Record<string, string> = {
  rolling: 'sidebar.subscriptionQuotaRolling',
  weekly: 'sidebar.subscriptionQuotaWeekly',
  monthly: 'sidebar.subscriptionQuotaMonthly'
};

export function useSubscriptionQuota(
  model: () => ModelOption | null | undefined,
  active: () => boolean
) {
  const personalization = usePersonalizationStore();

  const enabled = computed(() => personalization.form.subscription_quota_enabled !== false);
  const configured = computed(() => personalization.form.subscription_quota_source || 'auto');

  const source = computed<SubscriptionSource | null>(() => {
    if (!enabled.value) return null;
    if (configured.value === 'opencode') return 'opencode';
    if (configured.value === 'chatgpt') return 'chatgpt';
    const current = model();
    if (isOpencodeGoProvider(current?.providerId)) return 'opencode';
    if (current?.isChatgptModel) return 'chatgpt';
    return null;
  });

  const chatgpt = useChatgptQuota(model, () => active() && source.value === 'chatgpt');

  // ---------------------------------------------------------------- OpenCode Go
  const opencodeWindows = ref<RawWindow[]>([]);
  const opencodeLabel = ref('');
  const opencodeLoading = ref(false);
  const opencodeError = ref('');
  let opencodeTimer: ReturnType<typeof setInterval> | undefined;
  let opencodePending: Promise<void> | null = null;

  // 固定选 OpenCode Go 而当前模型不属该家时不传 provider_id，由后端取首个可用连接。
  const opencodeProviderId = computed(() => {
    const id = model()?.providerId;
    return isOpencodeGoProvider(id) ? String(id) : '';
  });

  const formatReset = (value?: string) => {
    if (!value) return '—';
    const time = new Date(value);
    if (Number.isNaN(time.getTime())) return '—';
    return time.toLocaleString(currentLocale.value, {
      month: 'numeric',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  function loadOpencode(force = false): Promise<void> {
    if (!active() || source.value !== 'opencode') return Promise.resolve();
    if (opencodePending) return opencodePending;
    opencodeLoading.value = true;
    opencodeError.value = '';
    const query = new URLSearchParams();
    if (opencodeProviderId.value) query.set('provider_id', opencodeProviderId.value);
    if (force) query.set('refresh', '1');
    const suffix = query.toString() ? `?${query.toString()}` : '';
    opencodePending = (async () => {
      try {
        const response = await fetch(`/api/subscription/opencode-usage${suffix}`, {
          cache: 'no-store'
        });
        const data = await response.json().catch(() => null);
        if (!response.ok || !data?.success) {
          throw new Error(String(data?.error || 'unavailable'));
        }
        opencodeLabel.value = String(data.label || '');
        opencodeWindows.value = Array.isArray(data.windows) ? data.windows : [];
      } catch {
        // 查询失败不保留上一次数字：额度是时效性数据，旧值会被误读成当前额度。
        opencodeWindows.value = [];
        opencodeError.value = t('sidebar.subscriptionQuotaUnavailable');
      } finally {
        opencodeLoading.value = false;
        opencodePending = null;
      }
    })();
    return opencodePending;
  }

  watch(
    [source, () => active()],
    ([current, visible]) => {
      if (opencodeTimer) {
        clearInterval(opencodeTimer);
        opencodeTimer = undefined;
      }
      if (current !== 'opencode' || !visible) return;
      void loadOpencode(false);
      opencodeTimer = setInterval(() => void loadOpencode(false), REFRESH_MS);
    },
    { immediate: true }
  );

  onBeforeUnmount(() => {
    if (opencodeTimer) clearInterval(opencodeTimer);
    opencodeTimer = undefined;
  });

  // ---------------------------------------------------------------- 统一输出
  const windows = computed<QuotaWindowView[]>(() => {
    if (source.value === 'opencode') {
      return opencodeWindows.value
        .filter((window) => typeof window.percent === 'number')
        .map((window) => {
          const key = String(window.key || '');
          return {
            key,
            label: t(WINDOW_LABEL_KEY[key] || 'sidebar.subscriptionQuotaWindow'),
            percent: Math.max(0, Math.min(100, Math.round(Number(window.percent)))),
            resetText: formatReset(window.reset_at)
          };
        });
    }
    if (source.value === 'chatgpt') return chatgpt.windows.value;
    return [];
  });

  const loading = computed(() => {
    if (source.value === 'opencode') return opencodeLoading.value;
    if (source.value === 'chatgpt') return chatgpt.loading.value;
    return false;
  });

  const error = computed(() => {
    if (source.value === 'opencode') return opencodeError.value;
    if (source.value === 'chatgpt') return chatgpt.error.value;
    return '';
  });

  const title = computed(() =>
    source.value === 'opencode' ? opencodeLabel.value || 'OpenCode Go' : t('sidebar.chatgptQuota')
  );

  const accountLabel = computed(() =>
    source.value === 'chatgpt' ? chatgpt.accountHash.value : ''
  );

  const refresh = (force = true) => {
    if (source.value === 'opencode') return loadOpencode(force);
    if (source.value === 'chatgpt') return chatgpt.refresh(force);
    return Promise.resolve();
  };

  return {
    /** 是否显示额度卡片；false 时调用方回退旧的 Token 上下文提示 */
    visible: computed(() => source.value !== null),
    source,
    title,
    accountLabel,
    windows,
    loading,
    error,
    refresh
  };
}
