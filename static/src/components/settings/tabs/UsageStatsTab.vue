<script setup lang="ts">
import { computed, inject } from 'vue';
import { t } from '@/locales';
import UsageActivity from './usage/UsageActivity.vue';
import UsageCacheChart from './usage/UsageCacheChart.vue';
import UsageComposition from './usage/UsageComposition.vue';
import UsageModelTable from './usage/UsageModelTable.vue';
import UsagePunchCard from './usage/UsagePunchCard.vue';
import {
  formatDate,
  formatMoney,
  formatPercent,
  formatTokens,
  parseDay,
  sourceLabel
} from './usage/format';
import { useUsageStats } from './usage/useUsageStats';
import type { UsageGranularity, UsageMetric, UsageRange } from './usage/types';

defineOptions({ name: 'UsageStatsTab' });

/** 下拉沿用设置页共享上下文（与其它分区同一套浮层定位与外部点击关闭） */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const { activeDropdown, activeTheme, closeDropdown, floatingMenuStyle, toggleDropdown } = ctx;

const {
  report,
  loading,
  error,
  updatedAt,
  source,
  granularity,
  metric,
  range,
  currency,
  rate,
  load
} = useUsageStats();

const GRANULARITIES: { id: UsageGranularity; key: string }[] = [
  { id: 'day', key: 'usageStats.granDay' },
  { id: 'week', key: 'usageStats.granWeek' },
  { id: 'cum', key: 'usageStats.granCum' }
];
const METRICS: { id: UsageMetric; key: string }[] = [
  { id: 'tokens', key: 'usageStats.metricTokens' },
  { id: 'work', key: 'usageStats.metricWork' },
  { id: 'cost', key: 'usageStats.metricCost' },
  { id: 'requests', key: 'usageStats.metricRequests' }
];
const RANGES: { id: UsageRange; key: string }[] = [
  { id: '7', key: 'usageStats.range7' },
  { id: '30', key: 'usageStats.range30' },
  { id: 'all', key: 'usageStats.rangeAll' }
];

const sourceOptions = computed(() => {
  const list = report.value?.sources || [];
  return [
    { id: 'all', label: t('usageStats.allSources'), meta: t('usageStats.allSourcesMeta') },
    ...list.map((item) => ({
      id: item.id,
      label: sourceLabel(item),
      meta: !item.has_data
        ? t('usageStats.noDataMeta')
        : item.id === 'chat'
          ? t('usageStats.chatMeta')
          : item.id === 'ws:__unassigned__'
            ? t('usageStats.unassignedMeta')
            : t('usageStats.workspaceMeta')
    }))
  ];
});
const sourceText = computed(
  () => sourceOptions.value.find((item) => item.id === source.value)?.label || t('usageStats.allSources')
);

function pickSource(id: string) {
  source.value = id;
  closeDropdown();
}

const activityTitle = computed(() =>
  metric.value === 'cost'
    ? t('usageStats.activityCost')
    : metric.value === 'requests'
      ? t('usageStats.activityRequests')
      : t('usageStats.activityTokens')
);

const overviewItems = computed(() => {
  const data = report.value;
  if (!data) return [];
  const ov = data.overview;
  return [
    {
      key: 'total',
      label: t('usageStats.ovTotal'),
      value: formatTokens(ov.total_tokens),
      sub: t('usageStats.ovCacheHit', { pct: formatPercent(ov.cached_tokens, ov.input_tokens) }),
      note: false
    },
    {
      key: 'cost',
      label: t('usageStats.ovCost'),
      value: formatMoney(ov.cost_usd, currency.value, rate.value),
      // 只在确有未计价请求时提示；全部已计价时留空，不额外占用注意力
      sub: ov.unpriced_requests
        ? t('usageStats.ovUnpriced', { tokens: formatTokens(ov.unpriced_tokens) })
        : '',
      note: ov.unpriced_requests > 0
    },
    {
      key: 'active',
      label: t('usageStats.ovActive'),
      value: t('usageStats.days', { n: ov.active_days }),
      sub: data.first_date ? t('usageStats.ovFirstDate', { date: formatDate(parseDay(data.first_date)) }) : '—',
      note: false
    },
    {
      key: 'streak',
      label: t('usageStats.ovStreak'),
      value: t('usageStats.days', { n: ov.current_streak }),
      sub: t('usageStats.ovLongest', { n: ov.longest_streak }),
      note: false
    },
    {
      key: 'best',
      label: t('usageStats.ovBest'),
      value: ov.best_day ? formatTokens(ov.best_day.tokens) : '—',
      sub: ov.best_day ? formatDate(parseDay(ov.best_day.date)) : '—',
      note: false
    }
  ];
});

const breakdown = computed(() => report.value?.ranges[range.value]);
const updatedText = computed(() =>
  updatedAt.value
    ? t('usageStats.updatedAt', {
        time: updatedAt.value.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      })
    : ''
);
</script>

<template>
  <section class="settings-page usage-page">
    <!-- 范围筛选 + 刷新 -->
    <div class="usage-toolbar">
      <div
        class="settings-select-wrap"
        :class="{ open: activeDropdown === 'usage-source' }"
        @click.stop
      >
        <button
          type="button"
          class="settings-select-button usage-source-button"
          :aria-label="$t('usageStats.sourceAria')"
          @click="toggleDropdown('usage-source')"
        >
          <span class="usage-ellipsis">{{ sourceText }}</span>
          <span class="select-chevron" aria-hidden="true"></span>
        </button>
        <div
          :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
          :style="activeDropdown === 'usage-source' ? floatingMenuStyle : undefined"
        >
          <button
            v-for="option in sourceOptions"
            :key="option.id"
            type="button"
            class="settings-menu-option"
            :class="{ selected: source === option.id }"
            @click="pickSource(option.id)"
          >
            <strong>{{ option.label }}</strong
            ><span>{{ option.meta }}</span
            ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
          </button>
        </div>
      </div>
      <span class="usage-toolbar-meta">
        <template v-if="report">{{ updatedText }} · {{ $t('usageStats.timezone', { tz: report.timezone }) }}</template>
      </span>
      <button type="button" class="settings-secondary-button" :disabled="loading" @click="load">
        {{ loading ? $t('usageStats.refreshing') : $t('usageStats.refresh') }}
      </button>
    </div>

    <p v-if="error" class="usage-error">{{ error }}</p>
    <p v-if="!report && loading" class="usage-placeholder">{{ $t('usageStats.loading') }}</p>

    <template v-if="report">
      <p v-if="!report.first_date" class="usage-placeholder">{{ $t('usageStats.empty') }}</p>

      <!-- 概览 -->
      <div class="usage-overview">
        <div v-for="item in overviewItems" :key="item.key" class="usage-stat">
          <span class="usage-stat-label">{{ item.label }}</span>
          <span class="usage-stat-value">{{ item.value }}</span>
          <span class="usage-stat-sub" :class="{ 'is-note': item.note }">{{ item.sub }}</span>
        </div>
      </div>

      <!-- 活动 -->
      <div class="usage-block">
        <div class="usage-block-head">
          <h3>{{ activityTitle }}</h3>
          <div class="usage-tabs" role="tablist" :aria-label="$t('usageStats.granAria')">
            <button
              v-for="item in GRANULARITIES"
              :key="item.id"
              type="button"
              role="tab"
              class="usage-tab"
              :aria-selected="granularity === item.id"
              @click="granularity = item.id"
            >
              {{ $t(item.key) }}
            </button>
          </div>
        </div>
        <div class="usage-chips" role="group" :aria-label="$t('usageStats.metricAria')">
          <button
            v-for="item in METRICS"
            :key="item.id"
            type="button"
            class="usage-chip"
            :aria-pressed="metric === item.id"
            @click="metric = item.id"
          >
            {{ $t(item.key) }}
          </button>
        </div>
        <UsageActivity
          :report="report"
          :metric="metric"
          :granularity="granularity"
          :currency="currency"
          :rate="rate"
        />
      </div>

      <!-- 明细 -->
      <div class="usage-details-head">
        <div>
          <h3>{{ $t('usageStats.detailsTitle') }}</h3>
          <p>{{ $t('usageStats.detailsDesc') }}</p>
        </div>
        <div class="usage-tabs" role="tablist" :aria-label="$t('usageStats.rangeAria')">
          <button
            v-for="item in RANGES"
            :key="item.id"
            type="button"
            role="tab"
            class="usage-tab"
            :aria-selected="range === item.id"
            @click="range = item.id"
          >
            {{ $t(item.key) }}
          </button>
        </div>
      </div>

      <template v-if="breakdown">
        <div class="usage-block">
          <div class="usage-block-head"><h3>{{ $t('usageStats.modelsTitle') }}</h3></div>
          <UsageModelTable :models="breakdown.models" :currency="currency" :rate="rate" />
          <p class="usage-note">{{ $t('usageStats.modelsNote') }}</p>
        </div>

        <div class="usage-columns">
          <div class="usage-block">
            <div class="usage-block-head"><h3>{{ $t('usageStats.compTitle') }}</h3></div>
            <UsageComposition
              :breakdown="breakdown"
              :sources="report.sources"
              :show-sources="source === 'all'"
            />
          </div>
          <div class="usage-block">
            <div class="usage-block-head"><h3>{{ $t('usageStats.cacheTitle') }}</h3></div>
            <UsageCacheChart :report="report" :range="range" />
          </div>
        </div>

        <div class="usage-block">
          <div class="usage-block-head"><h3>{{ $t('usageStats.punchTitle') }}</h3></div>
          <UsagePunchCard :punch="breakdown.punch" :timezone="report.timezone" />
        </div>
      </template>
    </template>
  </section>
</template>

<style scoped>
/* 扁平布局：区块之间用分隔线与留白区分，不套卡片 */
.usage-page {
  display: flex;
  flex-direction: column;
}

.usage-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-bottom: 16px;
}

.usage-source-button {
  max-width: 240px;
  border: 1px solid var(--border-default);
}

.usage-ellipsis {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.usage-toolbar-meta {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--text-tertiary);
}

.usage-error {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--state-danger);
}

.usage-placeholder {
  margin: 0 0 16px;
  font-size: 13px;
  color: var(--text-tertiary);
}

.usage-overview {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  border-top: 1px solid var(--border-default);
  border-bottom: 1px solid var(--border-default);
}

.usage-stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  padding: 14px 14px 14px 0;
}

.usage-stat + .usage-stat {
  padding-left: 14px;
  border-left: 1px solid var(--border-default);
}

.usage-stat-label,
.usage-stat-sub {
  height: 18px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--text-tertiary);
}

.usage-stat-sub.is-note {
  color: var(--text-secondary);
}

.usage-stat-value {
  height: 30px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 21px;
  font-weight: 600;
  line-height: 30px;
  font-variant-numeric: tabular-nums;
}

.usage-block {
  min-width: 0;
  padding: 22px 0;
  border-bottom: 1px solid var(--border-default);
}

.usage-block-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.usage-block-head h3,
.usage-details-head h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}

.usage-details-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding-top: 26px;
}

.usage-details-head p {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--text-tertiary);
}

.usage-tabs {
  display: flex;
  gap: 2px;
}

.usage-tab {
  height: 28px;
  padding: 0 9px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--text-tertiary);
  font-size: 14px;
  white-space: nowrap;
  cursor: pointer;
}

.usage-tab:hover {
  background: var(--hover-bg);
  color: var(--text-secondary);
}

.usage-tab[aria-selected='true'] {
  color: var(--text-primary);
  font-weight: 600;
}

.usage-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 14px;
}

.usage-chip {
  height: 26px;
  padding: 0 10px;
  border: 1px solid var(--border-default);
  border-radius: 999px;
  background: transparent;
  color: var(--text-secondary);
  font-size: 12px;
  white-space: nowrap;
  cursor: pointer;
}

.usage-chip:hover {
  background: var(--hover-bg);
}

.usage-chip[aria-pressed='true'] {
  border-color: var(--text-primary);
  color: var(--text-primary);
  font-weight: 600;
}

.usage-columns {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  column-gap: 32px;
  border-bottom: 1px solid var(--border-default);
}

.usage-columns > .usage-block {
  border-bottom: 0;
}

.usage-note {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--text-tertiary);
}

@media (max-width: 900px) {
  .usage-overview {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .usage-stat,
  .usage-stat + .usage-stat {
    padding: 12px 12px 12px 0;
    border-left: 0;
    border-bottom: 1px solid var(--border-default);
  }

  .usage-stat:nth-child(even) {
    padding-left: 14px;
    border-left: 1px solid var(--border-default);
  }

  .usage-stat:last-child {
    border-bottom: 0;
  }

  .usage-columns {
    grid-template-columns: minmax(0, 1fr);
  }

  .usage-columns > .usage-block + .usage-block {
    border-top: 1px solid var(--border-default);
  }

  .usage-toolbar-meta {
    display: none;
  }
}
</style>
