import { onBeforeUnmount, ref } from 'vue';
import type { StorageData } from './types';

const POLL_INTERVAL_MS = 1200;
const POLL_TIMEOUT_MS = 30000;

/**
 * 存储统计页数据：GET /api/storage-stats（读缓存，毫秒级）。
 * 进入页面先渲染缓存，再触发一次后台重扫并轮询直至 scanned_at 变化。
 */
export function useStorageStats() {
  const data = ref<StorageData | null>(null);
  const loading = ref(false);
  /** 首次加载或后台重扫期间为 true（用于禁用刷新按钮并显示「统计中」） */
  const busy = ref(false);
  const error = ref('');

  let timer: number | null = null;
  let baseline: string | null = null;
  let deadline = 0;

  function stopPolling() {
    if (timer !== null) {
      window.clearTimeout(timer);
      timer = null;
    }
    busy.value = false;
  }

  async function getData(): Promise<StorageData> {
    const resp = await fetch('/api/storage-stats', { credentials: 'same-origin' });
    const payload = await resp.json().catch(() => null);
    if (!resp.ok || !payload?.success) {
      throw new Error(payload?.error || `HTTP ${resp.status}`);
    }
    return payload.data as StorageData;
  }

  async function triggerRefresh() {
    await fetch('/api/storage-stats/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}'
    });
  }

  function schedulePoll() {
    if (timer !== null) window.clearTimeout(timer);
    timer = window.setTimeout(poll, POLL_INTERVAL_MS);
  }

  async function poll() {
    timer = null;
    if (Date.now() > deadline) {
      stopPolling();
      return;
    }
    try {
      const next = await getData();
      data.value = next;
      if (next.scanned_at && next.scanned_at !== baseline) {
        stopPolling();
        return;
      }
    } catch {
      // 轮询期间的瞬时失败直接重试，最终由 deadline 收口
    }
    schedulePoll();
  }

  async function load() {
    if (busy.value) return;
    busy.value = true;
    loading.value = true;
    error.value = '';
    try {
      const first = await getData();
      data.value = first;
      baseline = first.scanned_at ?? null;
      if (!first.available) {
        stopPolling();
        return;
      }
      await triggerRefresh();
      deadline = Date.now() + POLL_TIMEOUT_MS;
      schedulePoll();
    } catch (err: any) {
      error.value = err?.message || String(err);
      stopPolling();
    } finally {
      loading.value = false;
    }
  }

  onBeforeUnmount(stopPolling);

  /** 清理后的强制重载：先收口旧轮询，避免被 busy 拦截。 */
  async function reload() {
    stopPolling();
    busy.value = false;
    await load();
  }

  return { data, loading, busy, error, load, reload };
}
