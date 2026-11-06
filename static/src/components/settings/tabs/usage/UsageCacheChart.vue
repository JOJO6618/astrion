<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { t } from '@/locales';
import UsageTooltip from './UsageTooltip.vue';
import { addDays, formatDate, formatPercent, formatTokens, isoDay, parseDay } from './format';
import { useUsageTooltip } from './useUsageTooltip';
import type { UsageDay, UsageRange, UsageReport } from './types';

defineOptions({ name: 'UsageCacheChart' });

const props = defineProps<{ report: UsageReport; range: UsageRange }>();

const box = ref<HTMLElement | null>(null);
const width = ref(360);
const { tip, hide, showAtRect } = useUsageTooltip();
let observer: ResizeObserver | null = null;

onMounted(() => {
  if (!box.value) return;
  width.value = box.value.clientWidth || width.value;
  observer = new ResizeObserver(() => {
    if (box.value) width.value = box.value.clientWidth || width.value;
  });
  observer.observe(box.value);
});
onBeforeUnmount(() => observer?.disconnect());

const H = 150;
const PAD_LEFT = 38;
const PAD_RIGHT = 8;
const PAD_TOP = 8;
const PAD_BOTTOM = 20;
const LOW = 0; // 纵轴下限 0%，不截断，避免夸大波动
const svgWidth = computed(() => Math.max(240, width.value));

/** 时间范围内逐日序列；「全部」从首条记录开始（最多热力图窗口 53 周） */
const series = computed(() => {
  const today = parseDay(props.report.today);
  const windowStart = parseDay(props.report.window_start);
  const first = props.report.first_date ? parseDay(props.report.first_date) : today;
  let start =
    props.range === 'all' ? (first > windowStart ? first : windowStart) : addDays(today, -(Number(props.range) - 1));
  if (start > today) start = today;
  const byDate = new Map(props.report.days.map((day) => [day.date, day]));
  const points: { date: Date; day?: UsageDay; rate: number | null }[] = [];
  for (let d = start; d <= today; d = addDays(d, 1)) {
    const day = byDate.get(isoDay(d));
    points.push({ date: d, day, rate: day && day.input_tokens > 0 ? day.cached_tokens / day.input_tokens : null });
  }
  return points;
});

const x = (index: number) =>
  PAD_LEFT + (svgWidth.value - PAD_LEFT - PAD_RIGHT) * (index / Math.max(1, series.value.length - 1));
const y = (rate: number) => PAD_TOP + (H - PAD_TOP - PAD_BOTTOM) * (1 - (rate - LOW) / (1 - LOW));

/** 无请求的日子断开折线，不把缺失当作 0% */
const path = computed(() => {
  let d = '';
  let pen = false;
  series.value.forEach((point, index) => {
    if (point.rate === null) {
      pen = false;
      return;
    }
    d += `${pen ? 'L' : 'M'}${x(index).toFixed(1)} ${y(point.rate).toFixed(1)} `;
    pen = true;
  });
  return d.trim();
});

const average = computed(() => {
  const totals = series.value.reduce(
    (acc, point) => {
      if (point.day) {
        acc.input += point.day.input_tokens;
        acc.cached += point.day.cached_tokens;
      }
      return acc;
    },
    { input: 0, cached: 0 }
  );
  return totals.input > 0 ? formatPercent(totals.cached, totals.input) : null;
});

/** 点较少（≤31 天）时画出每日数据点，长区间只画折线 */
const dots = computed(() =>
  series.value.length > 31
    ? []
    : series.value.flatMap((point, index) =>
        point.rate === null ? [] : [{ index, cx: x(index), cy: y(point.rate) }]
      )
);

function onMove(event: MouseEvent) {
  const svg = event.currentTarget as SVGSVGElement;
  const rect = svg.getBoundingClientRect();
  const px = (event.clientX - rect.left) * (svgWidth.value / rect.width);
  const n = series.value.length;
  const index = Math.max(0, Math.min(n - 1, Math.round(((px - PAD_LEFT) / (svgWidth.value - PAD_LEFT - PAD_RIGHT)) * (n - 1))));
  const point = series.value[index];
  if (!point) return;
  const anchorX = rect.left + (x(index) / svgWidth.value) * rect.width;
  const anchorY = rect.top + ((point.rate === null ? y(LOW) : y(point.rate)) / H) * rect.height;
  showAtRect(
    { left: anchorX, top: anchorY, width: 0, bottom: anchorY },
    formatDate(point.date),
    point.rate === null ? t('usageStats.tipIdle') : `${Math.round(point.rate * 100)}%`,
    point.day
      ? [
          [t('usageStats.tipInput'), formatTokens(point.day.input_tokens)],
          [t('usageStats.tipCached'), formatTokens(point.day.cached_tokens)]
        ]
      : []
  );
}

</script>

<template>
  <div ref="box" class="usage-cache">
    <template v-if="average !== null">
      <svg
        class="usage-chart"
        :viewBox="`0 0 ${svgWidth} ${H}`"
        :width="svgWidth"
        :height="H"
        @mousemove="onMove"
        @mouseleave="hide"
      >
        <g v-for="f in [0, 0.5, 1]" :key="f">
          <line class="usage-grid-line" :x1="PAD_LEFT" :x2="svgWidth - PAD_RIGHT" :y1="y(f)" :y2="y(f)" />
          <text class="usage-axis" :x="PAD_LEFT - 6" :y="y(f) + 4" text-anchor="end">{{ f * 100 }}%</text>
        </g>
        <path class="usage-line" :d="path" />
        <circle
          v-for="item in dots"
          :key="item.index"
          class="usage-dot"
          r="2.5"
          :cx="item.cx"
          :cy="item.cy"
        />
        <text class="usage-axis" :x="PAD_LEFT" :y="H - 3">{{ formatDate(series[0].date) }}</text>
        <text class="usage-axis" :x="svgWidth - PAD_RIGHT" :y="H - 3" text-anchor="end">{{
          $t('usageStats.today')
        }}</text>
      </svg>
      <p class="usage-note">{{ $t('usageStats.cacheNote', { pct: average }) }}</p>
    </template>
    <p v-else class="usage-note">{{ $t('usageStats.cacheEmpty') }}</p>
    <UsageTooltip :state="tip" />
  </div>
</template>

<style scoped>
.usage-cache {
  min-width: 0;
}

.usage-chart {
  display: block;
  max-width: 100%;
  height: auto;
  overflow: visible;
}

.usage-axis {
  fill: var(--text-tertiary);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.usage-grid-line {
  stroke: var(--border-default);
  stroke-width: 1;
}

.usage-line {
  fill: none;
  stroke: var(--heat-3);
  stroke-width: 2;
  stroke-linejoin: round;
  stroke-linecap: round;
}

.usage-dot {
  fill: var(--heat-3);
}

.usage-note {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--text-tertiary);
}
</style>
