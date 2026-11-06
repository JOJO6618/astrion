import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { t } from '@/locales';
import { usePersonalizationStore } from '@/stores/personalization';
import type { UsageGranularity, UsageMetric, UsageRange, UsageReport } from './types';

const VIEW_KEY = 'astrion_usage_stats_view';

interface ViewPrefs {
  granularity: UsageGranularity;
  metric: UsageMetric;
  range: UsageRange;
}

const DEFAULT_VIEW: ViewPrefs = { granularity: 'day', metric: 'tokens', range: '30' };

/** 视图偏好仅是本机便利项：读写失败（隐私模式等）静默回落默认值 */
function readView(): ViewPrefs {
  try {
    const raw = JSON.parse(localStorage.getItem(VIEW_KEY) || '{}');
    return {
      granularity: ['day', 'week', 'cum'].includes(raw.granularity) ? raw.granularity : DEFAULT_VIEW.granularity,
      metric: ['tokens', 'work', 'cost', 'requests'].includes(raw.metric) ? raw.metric : DEFAULT_VIEW.metric,
      range: ['7', '30', 'all'].includes(raw.range) ? raw.range : DEFAULT_VIEW.range
    };
  } catch {
    return { ...DEFAULT_VIEW };
  }
}

/**
 * 用量统计页数据：GET /api/usage-stats。
 * 只有「统计范围」需要重新请求；粒度、指标、时间范围均在前端基于同一份载荷切换。
 * 请求按代次（generation）判定归属：切换范围或卸载后，过期响应不写状态。
 */
export function useUsageStats() {
  const personalization = usePersonalizationStore();
  const report = ref<UsageReport | null>(null);
  const loading = ref(false);
  const error = ref('');
  const updatedAt = ref<Date | null>(null);
  const source = ref('all');
  const view = readView();
  const granularity = ref<UsageGranularity>(view.granularity);
  const metric = ref<UsageMetric>(view.metric);
  const range = ref<UsageRange>(view.range);
  let generation = 0;

  watch([granularity, metric, range], () => {
    try {
      localStorage.setItem(
        VIEW_KEY,
        JSON.stringify({ granularity: granularity.value, metric: metric.value, range: range.value })
      );
    } catch {
      /* 本机偏好写入失败不影响页面 */
    }
  });

  const currency = computed(() => personalization.form.display_currency || 'USD');
  /** USD 恒为 1；CNY 无首次汇率缓存时为 null（金额显示「—」） */
  const rate = computed<number | null>(() => {
    if (currency.value === 'USD') return 1;
    const value = report.value?.exchange_rate?.usd_cny;
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  });

  async function load() {
    const current = ++generation;
    loading.value = true;
    error.value = '';
    try {
      const params = new URLSearchParams({
        source: source.value,
        tz: Intl.DateTimeFormat().resolvedOptions().timeZone || '',
        tz_offset: String(-new Date().getTimezoneOffset())
      });
      const response = await fetch(`/api/usage-stats?${params.toString()}`);
      const payload = await response.json().catch(() => ({}));
      if (current !== generation) return;
      if (!response.ok || !payload?.success) {
        throw new Error(payload?.error || `HTTP ${response.status}`);
      }
      report.value = payload.data as UsageReport;
      updatedAt.value = new Date();
    } catch (err: any) {
      if (current !== generation) return;
      error.value = t('usageStats.loadFailed', { error: err?.message || String(err) });
    } finally {
      if (current === generation) loading.value = false;
    }
  }

  watch(source, () => void load());
  onMounted(() => void load());
  onBeforeUnmount(() => {
    generation += 1;
  });

  return { report, loading, error, updatedAt, source, granularity, metric, range, currency, rate, load };
}
