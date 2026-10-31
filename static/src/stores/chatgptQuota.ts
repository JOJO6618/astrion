import { defineStore } from 'pinia';
import { ref } from 'vue';
import { t } from '@/locales';

export interface ChatgptQuotaEntry {
  usage: Record<string, any> | null;
  accountHash: string;
  loading: boolean;
  error: string;
  updatedAt: number;
}

export const useChatgptQuotaStore = defineStore('chatgptQuota', () => {
  const entries = ref<Record<string, ChatgptQuotaEntry>>({});
  // Private to this Pinia instance: independent accounts may load concurrently.
  const pending = new Map<string, Promise<void>>();
  const ttl = 30_000;
  const defaultAccountId = ref('');
  const accountsLoading = ref(false);
  const accountsError = ref('');
  let accountsUpdatedAt = 0;
  let accountsPending: Promise<void> | null = null;

  function fetchAccounts(force = false): Promise<void> {
    if (accountsPending) return accountsPending;
    if (!force && accountsUpdatedAt && Date.now() - accountsUpdatedAt < ttl) {
      return Promise.resolve();
    }
    accountsLoading.value = true;
    accountsError.value = '';
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15_000);
    accountsPending = (async () => {
      try {
        const response = await fetch('/api/codex/accounts', {
          cache: 'no-store',
          signal: controller.signal
        });
        const data = await response.json();
        if (!response.ok || !data?.success || !Array.isArray(data.accounts)) {
          throw new Error(data?.error || t('sidebar.chatgptQuotaUnavailable'));
        }
        // Preserve registration order; skip disconnected/missing credential entries.
        const first = data.accounts.find(
          (account: any) =>
            account.connected && !account.credential_missing && typeof account.id === 'string'
        );
        defaultAccountId.value = first?.id || '';
        accountsUpdatedAt = Date.now();
      } catch (error: any) {
        defaultAccountId.value = '';
        accountsUpdatedAt = 0;
        accountsError.value =
          error?.name === 'AbortError'
            ? t('sidebar.chatgptQuotaUnavailable')
            : String(error?.message || t('sidebar.chatgptQuotaUnavailable'));
      } finally {
        clearTimeout(timer);
        accountsLoading.value = false;
        accountsPending = null;
      }
    })();
    return accountsPending;
  }

  function fetchQuota(accountId: string, force = false): Promise<void> {
    if (!accountId) return Promise.resolve();
    const existing = pending.get(accountId);
    if (existing) return existing;
    if (!entries.value[accountId]) {
      entries.value[accountId] = {
        usage: null,
        accountHash: '',
        loading: false,
        error: '',
        updatedAt: 0
      };
    }
    const entry = entries.value[accountId];
    if (!force && entry.updatedAt && Date.now() - entry.updatedAt < ttl) {
      return Promise.resolve();
    }
    entry.loading = true;
    entry.error = '';
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30_000);
    const task = (async () => {
      try {
        const query = new URLSearchParams({ account_id: accountId });
        if (force) query.set('refresh', '1');
        const response = await fetch(`/api/codex/usage?${query}`, {
          cache: 'no-store',
          signal: controller.signal
        });
        const data = await response.json();
        if (!response.ok || !data?.success || data.account_id !== accountId) {
          throw new Error(data?.error || t('sidebar.chatgptQuotaUnavailable'));
        }
        if (!data.usage || typeof data.usage.rate_limit !== 'object') {
          throw new Error(t('sidebar.chatgptQuotaUnavailable'));
        }
        entry.usage = data.usage;
        entry.accountHash = String(data.account_hash || '');
        entry.updatedAt = Date.now();
      } catch (error: any) {
        // Do not present an earlier snapshot as a successful fresh query.
        entry.usage = null;
        entry.updatedAt = 0;
        entry.error =
          error?.name === 'AbortError'
            ? t('sidebar.chatgptQuotaUnavailable')
            : String(error?.message || t('sidebar.chatgptQuotaUnavailable'));
      } finally {
        clearTimeout(timer);
        entry.loading = false;
        pending.delete(accountId);
      }
    })();
    pending.set(accountId, task);
    return task;
  }

  return { entries, defaultAccountId, accountsLoading, accountsError, fetchAccounts, fetchQuota };
});
