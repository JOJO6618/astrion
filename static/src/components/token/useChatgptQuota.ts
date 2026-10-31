import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { t, currentLocale } from '@/locales';
import type { ModelOption } from '@/stores/model';
import { useSettingsStore } from '@/stores/settings';
import { useChatgptQuotaStore } from '@/stores/chatgptQuota';

export function useChatgptQuota(
  model: () => ModelOption | null | undefined,
  active: () => boolean
) {
  const store = useChatgptQuotaStore();
  const settings = useSettingsStore();
  const useDefaultAccount = computed(() => model()?.isChatgptModel !== true);
  const accountId = computed(() => {
    if (!settings.isAdmin) return '';
    return useDefaultAccount.value ? store.defaultAccountId : model()?.providerId || '';
  });
  // All models show subscription quota; only the account selection changes.
  const applicable = computed(() => settings.isAdmin);
  const entry = computed(() => store.entries[accountId.value]);
  const accountHash = computed(() => entry.value?.accountHash || '');
  const loading = computed(
    () => entry.value?.loading || (useDefaultAccount.value && store.accountsLoading) || false
  );
  const error = computed(
    () => entry.value?.error || (useDefaultAccount.value ? store.accountsError : '') || ''
  );
  const now = ref(Date.now());
  const windows = computed(() => {
    const limits = entry.value?.usage?.rate_limit;
    const locale = currentLocale.value;
    return (['primary_window', 'secondary_window'] as const).flatMap((key) => {
      const window = limits?.[key];
      if (
        !window ||
        typeof window.used_percent !== 'number' ||
        !Number.isFinite(window.used_percent)
      )
        return [];
      const absolute = typeof window.reset_at === 'number' ? window.reset_at * 1000 : NaN;
      // Relative fields refer to the query time; do not restart the countdown on render.
      const resetAt =
        Number.isFinite(absolute) && absolute > 0
          ? absolute
          : typeof window.reset_after_seconds === 'number' && entry.value?.updatedAt
            ? entry.value.updatedAt + window.reset_after_seconds * 1000
            : NaN;
      // Once a window expires, a previous percentage is no longer a current quota.
      if (Number.isFinite(resetAt) && resetAt <= now.value) return [];
      return [
        {
          key,
          label: t(
            key === 'primary_window'
              ? 'personalization.codexUsagePrimaryWindow'
              : 'personalization.codexUsageSecondaryWindow'
          ),
          percent: Math.min(100, Math.max(0, Math.round(window.used_percent))),
          resetText: Number.isFinite(resetAt)
            ? new Date(resetAt).toLocaleString(locale, {
                month: 'numeric',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit'
              })
            : '—'
        }
      ];
    });
  });
  const refresh = async (force = true) => {
    if (!settings.isAdmin || !active()) return;
    if (useDefaultAccount.value) await store.fetchAccounts(force);
    // Account lookup may finish after navigation or the card has closed.
    if (!settings.isAdmin || !active()) return;
    if (accountId.value) await store.fetchQuota(accountId.value, force);
  };
  let timer: ReturnType<typeof setInterval> | undefined;
  watch(
    [accountId, useDefaultAccount, active, applicable],
    ([, , visible, allowed]) => {
      clearInterval(timer);
      timer = undefined;
      now.value = Date.now();
      if (!allowed || !visible) return;
      void refresh(false);
      timer = setInterval(() => {
        now.value = Date.now();
        void refresh(false);
      }, 60_000);
    },
    { immediate: true }
  );
  onBeforeUnmount(() => clearInterval(timer));
  return { accountId, accountHash, windows, loading, error, applicable, refresh };
}
