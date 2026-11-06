<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { t } from '@/locales';
import UsageTooltip from './UsageTooltip.vue';
import {
  addDays,
  formatDate,
  formatInt,
  formatMoney,
  formatMonth,
  formatPercent,
  formatTokens,
  isoDay,
  metricValue,
  mondayIndex,
  niceMax,
  parseDay,
  quantileLevels,
  splitModelKey,
  weekdayName
} from './format';
import { useUsageTooltip } from './useUsageTooltip';
import type { UsageDay, UsageGranularity, UsageMetric, UsageReport } from './types';

defineOptions({ name: 'UsageActivity' });

const props = defineProps<{
  report: UsageReport;
  metric: UsageMetric;
  granularity: UsageGranularity;
  currency: string;
  rate: number | null;
}>();

const CELL = 12;
const GAP = 3;
const STEP = CELL + GAP;

const { tip, hide, show: showAt } = useUsageTooltip();
const box = ref<HTMLElement | null>(null);
const scroller = ref<HTMLElement | null>(null);
const width = ref(720);
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

const dayMap = computed(() => new Map(props.report.days.map((day) => [day.date, day])));
const today = computed(() => parseDay(props.report.today));
const firstDate = computed(() => (props.report.first_date ? parseDay(props.report.first_date) : null));

function valueText(value: number): string {
  if (props.metric === 'cost') return formatMoney(value, props.currency, props.rate);
  if (props.metric === 'requests') return t('usageStats.valueRequests', { value: formatInt(value) });
  return t('usageStats.valueTokens', { value: formatTokens(value) });
}

function axisText(value: number): string {
  return props.metric === 'cost' ? formatMoney(value, props.currency, props.rate) : formatTokens(value);
}

const METRIC_KEYS: Record<UsageMetric, string> = {
  tokens: 'usageStats.metricTokens',
  work: 'usageStats.metricWork',
  cost: 'usageStats.metricCost',
  requests: 'usageStats.metricRequests'
};
const gridLabel = computed(() => t('usageStats.heatmapAria', { metric: t(METRIC_KEYS[props.metric]) }));

// ── 每天：53 周热力图 ──
interface Cell {
  date: Date;
  iso: string;
  day?: UsageDay;
  level: number;
  void: boolean;
  future: boolean;
}

const cells = computed<Cell[]>(() => {
  const start = parseDay(props.report.window_start);
  const first = addDays(start, -mondayIndex(start));
  const last = addDays(today.value, 6 - mondayIndex(today.value));
  const list: Cell[] = [];
  for (let d = first; d <= last; d = addDays(d, 1)) {
    const iso = isoDay(d);
    const day = dayMap.value.get(iso);
    list.push({
      date: d,
      iso,
      day,
      level: 0,
      void: !firstDate.value || d < firstDate.value,
      future: d > today.value
    });
  }
  const levelOf = quantileLevels(list.map((cell) => metricValue(cell.day, props.metric)));
  for (const cell of list) cell.level = levelOf(metricValue(cell.day, props.metric));
  return list;
});

const weeks = computed(() => cells.value.length / 7);

/** 每月 1 日所在列标月份；首列只在该月剩余天数足够时标注，避免与下一个月重叠 */
const monthLabels = computed(() => {
  const labels: { left: number; text: string }[] = [];
  for (let w = 0; w < weeks.value; w += 1) {
    const column = cells.value.slice(w * 7, w * 7 + 7);
    const firstOfMonth = column.find((cell) => cell.date.getDate() === 1);
    if (firstOfMonth && w < weeks.value - 1) {
      labels.push({ left: w * STEP, text: formatMonth(firstOfMonth.date) });
    } else if (w === 0 && column[0].date.getDate() <= 14) {
      labels.push({ left: 0, text: formatMonth(column[0].date) });
    }
  }
  return labels;
});

const hotIndex = ref(-1);

function dayRows(day: UsageDay): [string, string][] {
  const rows: [string, string][] = [
    [t('usageStats.tipInput'), formatTokens(day.input_tokens)],
    [
      t('usageStats.tipCached'),
      `${formatTokens(day.cached_tokens)} · ${formatPercent(day.cached_tokens, day.input_tokens)}%`
    ],
    [t('usageStats.tipOutput'), formatTokens(day.output_tokens)]
  ];
  const cost = formatMoney(day.cost_usd, props.currency, props.rate);
  rows.push([
    t('usageStats.tipCost'),
    day.unpriced_requests ? t('usageStats.tipCostPartial', { value: cost }) : cost
  ]);
  rows.push([t('usageStats.tipRequests'), t('usageStats.requestsShort', { value: formatInt(day.requests) })]);
  if (day.top_model) rows.push([t('usageStats.tipTopModel'), splitModelKey(day.top_model).model]);
  return rows;
}

function focusCell(index: number, target?: Element | null) {
  hotIndex.value = index;
  const cell = cells.value[index];
  const element = target || scroller.value?.querySelector(`[data-idx="${index}"]`);
  if (index < 0 || !cell || !element) {
    hide();
    return;
  }
  const title = `${formatDate(cell.date)} ${weekdayName(mondayIndex(cell.date))}`;
  if (cell.void) showAt(element, title, t('usageStats.tipBeforeLedger'));
  else if (!cell.day) showAt(element, title, t('usageStats.tipIdle'));
  else showAt(element, title, valueText(metricValue(cell.day, props.metric)), dayRows(cell.day));
}

function onGridPointer(event: Event) {
  const target = (event.target as HTMLElement)?.closest('[data-idx]');
  if (!target || target.classList.contains('is-future')) return;
  focusCell(Number(target.getAttribute('data-idx')), target);
}

function onGridFocus() {
  if (hotIndex.value >= 0) return focusCell(hotIndex.value);
  focusCell(cells.value.findIndex((cell) => cell.iso === props.report.today));
}

let keyTarget = -1;

function onGridKey(event: KeyboardEvent) {
  const delta = ({ ArrowUp: -1, ArrowDown: 1, ArrowLeft: -7, ArrowRight: 7 } as Record<string, number>)[
    event.key
  ];
  if (event.key === 'Escape') return focusCell(-1);
  if (!delta) return;
  event.preventDefault();
  const grid = event.currentTarget;
  // 滚动收起提示会清空 hotIndex，此时以上一次键盘目标为基准继续移动
  const next = (hotIndex.value >= 0 ? hotIndex.value : keyTarget) + delta;
  const cell = cells.value[next];
  if (!cell || cell.future || next < 0) return;
  const element = scroller.value?.querySelector(`[data-idx="${next}"]`);
  // 键盘移动可能触发横向滚动：滚动事件会先收起提示，下一帧再按新位置重新显示
  element?.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'instant' });
  focusCell(next);
  keyTarget = next;
  requestAnimationFrame(() => {
    // 期间又按了别的键、或焦点已离开网格，则不再补显示
    if (keyTarget === next && document.activeElement === grid) focusCell(next);
  });
}

/** 打开时滚动到最近的周（窄屏只能看到一部分时） */
async function scrollToLatest() {
  await nextTick();
  if (scroller.value) scroller.value.scrollLeft = scroller.value.scrollWidth;
}
onMounted(scrollToLatest);
watch(() => [props.granularity, props.report], scrollToLatest);

// ── SVG 图通用尺寸 ──
const H = 190;
const PAD_LEFT = 52;
const PAD_RIGHT = 8;
const PAD_TOP = 8;
const PAD_BOTTOM = 22;
const svgWidth = computed(() => Math.max(320, width.value));
const plotHeight = H - PAD_TOP - PAD_BOTTOM;

// ── 每周 ──
const weekBars = computed(() => {
  const list: { start: Date; value: number; before: boolean }[] = [];
  for (let w = 0; w < weeks.value; w += 1) {
    const column = cells.value.slice(w * 7, w * 7 + 7);
    list.push({
      start: column[0].date,
      value: column.reduce((sum, cell) => sum + metricValue(cell.day, props.metric), 0),
      before: column.every((cell) => cell.void)
    });
  }
  return list;
});
const weekMax = computed(() => niceMax(Math.max(0, ...weekBars.value.map((bar) => bar.value))));
const barWidth = computed(() => (svgWidth.value - PAD_LEFT - PAD_RIGHT) / Math.max(1, weekBars.value.length));
const weekMonthTicks = computed(() =>
  weekBars.value
    .map((bar, index) => ({ bar, index }))
    .filter(({ bar, index }) => index > 0 && bar.start.getDate() <= 7)
    .map(({ bar, index }) => ({ x: PAD_LEFT + index * barWidth.value, text: formatMonth(bar.start) }))
);
const hotWeek = ref(-1);

function onWeekPointer(event: Event) {
  const target = (event.target as Element)?.closest('[data-week]');
  if (!target) return;
  const index = Number(target.getAttribute('data-week'));
  const bar = weekBars.value[index];
  hotWeek.value = index;
  const end = addDays(bar.start, 6);
  const title = `${formatDate(bar.start)} – ${formatDate(end > today.value ? today.value : end)}`;
  showAt(target, title, bar.before ? t('usageStats.tipBeforeLedger') : valueText(bar.value));
}

function leaveWeek() {
  hotWeek.value = -1;
  hide();
}

// ── 累计 ──
const cumulative = computed(() => {
  const start = firstDate.value && firstDate.value > parseDay(props.report.window_start)
    ? firstDate.value
    : parseDay(props.report.window_start);
  const prior =
    props.metric === 'tokens'
      ? props.report.prior.tokens
      : props.metric === 'work'
        ? props.report.prior.work_tokens
        : props.metric === 'cost'
          ? Number(props.report.prior.cost_usd) || 0
          : props.report.prior.requests;
  const points: { date: Date; value: number }[] = [];
  let total = prior;
  for (let d = start; d <= today.value; d = addDays(d, 1)) {
    total += metricValue(dayMap.value.get(isoDay(d)), props.metric);
    points.push({ date: d, value: total });
  }
  return { start, points, total };
});
const cumMax = computed(() => niceMax(cumulative.value.total));
const cumX = (index: number) =>
  PAD_LEFT + (svgWidth.value - PAD_LEFT - PAD_RIGHT) * (index / Math.max(1, cumulative.value.points.length - 1));
const cumY = (value: number) => PAD_TOP + plotHeight * (1 - value / cumMax.value);
const cumLine = computed(() =>
  cumulative.value.points.map((p, i) => `${i ? 'L' : 'M'}${cumX(i).toFixed(1)} ${cumY(p.value).toFixed(1)}`).join(' ')
);
const cumArea = computed(() => {
  const n = cumulative.value.points.length;
  if (!n) return '';
  return `${cumLine.value} L${cumX(n - 1).toFixed(1)} ${cumY(0)} L${cumX(0).toFixed(1)} ${cumY(0)} Z`;
});
const cumTicks = computed(() =>
  cumulative.value.points
    .map((p, index) => ({ p, index }))
    .filter(({ p, index }) => index > 0 && index < cumulative.value.points.length - 4 && p.date.getDate() === 1)
    .map(({ p, index }) => ({ x: cumX(index), text: formatMonth(p.date) }))
);
const cumHot = ref(-1);
const svgRef = ref<SVGSVGElement | null>(null);
const dotRef = ref<SVGCircleElement | null>(null);

function onCumMove(event: MouseEvent) {
  const svg = svgRef.value;
  const n = cumulative.value.points.length;
  if (!svg || !n) return;
  const rect = svg.getBoundingClientRect();
  const x = (event.clientX - rect.left) * (svgWidth.value / rect.width);
  const index = Math.max(0, Math.min(n - 1, Math.round(((x - PAD_LEFT) / (svgWidth.value - PAD_LEFT - PAD_RIGHT)) * (n - 1))));
  cumHot.value = index;
  void nextTick(() => {
    const point = cumulative.value.points[index];
    if (dotRef.value && point) {
      showAt(dotRef.value, t('usageStats.tipUntil', { date: formatDate(point.date) }), valueText(point.value));
    }
  });
}

function leaveCum() {
  cumHot.value = -1;
  hide();
}

const cumDotIndex = computed(() =>
  cumHot.value >= 0 ? cumHot.value : Math.max(0, cumulative.value.points.length - 1)
);

const gridTicks = (max: number) => [0, 0.5, 1].map((f) => ({ y: PAD_TOP + plotHeight * (1 - f), value: max * f }));

watch(() => [props.granularity, props.metric], hide);

/** 提示被收起（含滚动）时一并清掉高亮格、高亮柱与累计十字线，避免残留在错误位置 */
watch(
  () => tip.visible,
  (visible) => {
    if (visible) return;
    hotIndex.value = -1;
    hotWeek.value = -1;
    cumHot.value = -1;
  }
);
</script>

<template>
  <div ref="box" class="usage-activity">
    <!-- 每天 -->
    <template v-if="granularity === 'day'">
      <div ref="scroller" class="usage-heat-scroll">
        <div class="usage-heat" :style="{ '--cell': `${CELL}px`, '--gap': `${GAP}px` }">
          <div class="usage-heat-days" aria-hidden="true">
            <span v-for="i in 7" :key="i">{{ [1, 3, 5].includes(i) ? weekdayName(i - 1) : '' }}</span>
          </div>
          <div
            class="usage-heat-grid"
            tabindex="0"
            role="grid"
            :aria-label="gridLabel"
            @mouseover="onGridPointer"
            @click="onGridPointer"
            @mouseleave="focusCell(-1)"
            @focus="onGridFocus"
            @blur="focusCell(-1)"
            @keydown="onGridKey"
          >
            <span
              v-for="(cell, index) in cells"
              :key="cell.iso"
              :data-idx="index"
              class="usage-cell"
              :class="[
                cell.future ? 'is-future' : cell.void ? 'is-void' : `l${cell.level}`,
                { 'is-hot': hotIndex === index }
              ]"
            ></span>
          </div>
          <div class="usage-heat-months" aria-hidden="true">
            <span v-for="label in monthLabels" :key="label.left" :style="{ left: `${label.left}px` }">{{
              label.text
            }}</span>
          </div>
        </div>
      </div>
      <div class="usage-activity-foot">
        <span>{{
          report.first_date
            ? $t('usageStats.noteDaily', { date: formatDate(parseDay(report.first_date)) })
            : $t('usageStats.noteDailyEmpty')
        }}</span>
        <span class="usage-legend" aria-hidden="true">
          <span class="usage-cell is-void"></span>{{ $t('usageStats.legendNone') }}
          <span class="usage-legend-gap"></span>{{ $t('usageStats.legendLess') }}
          <span v-for="level in [0, 1, 2, 3, 4]" :key="level" class="usage-cell" :class="`l${level}`"></span>
          {{ $t('usageStats.legendMore') }}
        </span>
      </div>
    </template>

    <!-- 每周 -->
    <template v-else-if="granularity === 'week'">
      <svg
        class="usage-chart"
        :viewBox="`0 0 ${svgWidth} ${H}`"
        :width="svgWidth"
        :height="H"
        @mousemove="onWeekPointer"
        @mouseleave="leaveWeek"
      >
        <g v-for="tick in gridTicks(weekMax)" :key="tick.y">
          <line class="usage-grid-line" :x1="PAD_LEFT" :x2="svgWidth - PAD_RIGHT" :y1="tick.y" :y2="tick.y" />
          <text class="usage-axis" :x="PAD_LEFT - 6" :y="tick.y + 4" text-anchor="end">{{ axisText(tick.value) }}</text>
        </g>
        <g v-for="(bar, index) in weekBars" :key="index">
          <rect
            class="usage-bar"
            :class="{ 'is-hot': hotWeek === index }"
            :x="PAD_LEFT + index * barWidth + 1"
            :y="PAD_TOP + plotHeight * (1 - bar.value / weekMax)"
            :width="Math.max(1, barWidth - 2)"
            :height="plotHeight * (bar.value / weekMax)"
            rx="2"
          />
          <rect
            :data-week="index"
            class="usage-hit"
            :x="PAD_LEFT + index * barWidth"
            :y="PAD_TOP"
            :width="barWidth"
            :height="plotHeight"
          />
        </g>
        <text v-for="tick in weekMonthTicks" :key="tick.x" class="usage-axis" :x="tick.x" :y="H - 4">{{
          tick.text
        }}</text>
      </svg>
      <div class="usage-activity-foot">
        <span>{{ $t('usageStats.noteWeekly') }}</span>
      </div>
    </template>

    <!-- 累计总量 -->
    <template v-else>
      <svg
        ref="svgRef"
        class="usage-chart"
        :viewBox="`0 0 ${svgWidth} ${H}`"
        :width="svgWidth"
        :height="H"
        @mousemove="onCumMove"
        @mouseleave="leaveCum"
      >
        <g v-for="tick in gridTicks(cumMax)" :key="tick.y">
          <line class="usage-grid-line" :x1="PAD_LEFT" :x2="svgWidth - PAD_RIGHT" :y1="tick.y" :y2="tick.y" />
          <text class="usage-axis" :x="PAD_LEFT - 6" :y="tick.y + 4" text-anchor="end">{{ axisText(tick.value) }}</text>
        </g>
        <path class="usage-area" :d="cumArea" />
        <path class="usage-line" :d="cumLine" />
        <text class="usage-axis" :x="PAD_LEFT" :y="H - 4">{{ formatDate(cumulative.start) }}</text>
        <text v-for="tick in cumTicks" :key="tick.x" class="usage-axis" :x="tick.x" :y="H - 4" text-anchor="middle">{{
          tick.text
        }}</text>
        <text class="usage-axis" :x="svgWidth - PAD_RIGHT" :y="H - 4" text-anchor="end">{{
          $t('usageStats.today')
        }}</text>
        <line
          v-if="cumHot >= 0"
          class="usage-cross"
          :x1="cumX(cumHot)"
          :x2="cumX(cumHot)"
          :y1="PAD_TOP"
          :y2="H - PAD_BOTTOM"
        />
        <circle
          v-if="cumulative.points.length"
          ref="dotRef"
          class="usage-dot"
          r="4.5"
          :cx="cumX(cumDotIndex)"
          :cy="cumY(cumulative.points[cumDotIndex].value)"
        />
      </svg>
      <div class="usage-activity-foot">
        <span>{{ $t('usageStats.noteCum', { date: formatDate(cumulative.start) }) }}</span>
        <span class="usage-num">{{ $t('usageStats.cumTotal', { value: valueText(cumulative.total) }) }}</span>
      </div>
    </template>

    <UsageTooltip :state="tip" />
  </div>
</template>

<style scoped>
.usage-activity {
  min-width: 0;
}

.usage-heat-scroll {
  overflow-x: auto;
  padding-bottom: 4px;
  scrollbar-width: thin;
  scrollbar-color: var(--border-default) transparent;
}

.usage-heat {
  display: grid;
  grid-template-columns: 24px auto;
  column-gap: 6px;
  width: max-content;
}

.usage-heat-days {
  display: grid;
  grid-template-rows: repeat(7, var(--cell));
  row-gap: var(--gap);
  font-size: 11px;
  line-height: var(--cell);
  color: var(--text-tertiary);
  white-space: nowrap;
}

.usage-heat-grid {
  display: grid;
  grid-auto-flow: column;
  grid-template-rows: repeat(7, var(--cell));
  grid-auto-columns: var(--cell);
  gap: var(--gap);
  border-radius: 4px;
  outline: none;
}

.usage-heat-grid:focus-visible {
  box-shadow: 0 0 0 2px var(--border-strong);
}

.usage-heat-months {
  grid-column: 2;
  position: relative;
  height: 18px;
  margin-top: 6px;
  font-size: 12px;
  color: var(--text-tertiary);
}

.usage-heat-months span {
  position: absolute;
  top: 0;
  white-space: nowrap;
}

.usage-cell {
  display: inline-block;
  width: var(--cell, 11px);
  height: var(--cell, 11px);
  border-radius: 3px;
  background: var(--heat-0);
}

.usage-cell.is-void {
  background: var(--heat-void);
}

.usage-cell.is-future {
  visibility: hidden;
}

.usage-cell.l1 {
  background: var(--heat-1);
}

.usage-cell.l2 {
  background: var(--heat-2);
}

.usage-cell.l3 {
  background: var(--heat-3);
}

.usage-cell.l4 {
  background: var(--heat-4);
}

.usage-cell.is-hot {
  outline: 1.5px solid var(--text-primary);
  outline-offset: 1px;
}

.usage-activity-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px 12px;
  margin-top: 10px;
  font-size: 12px;
  color: var(--text-tertiary);
}

.usage-legend {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}

.usage-legend .usage-cell {
  --cell: 11px;
  border-radius: 2px;
}

.usage-legend-gap {
  width: 8px;
}

.usage-num {
  font-variant-numeric: tabular-nums;
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

.usage-bar {
  fill: var(--heat-3);
}

.usage-bar.is-hot {
  fill: var(--heat-4);
}

.usage-hit {
  fill: transparent;
}

.usage-area {
  fill: color-mix(in srgb, var(--heat-3) 18%, transparent);
}

.usage-line {
  fill: none;
  stroke: var(--heat-3);
  stroke-width: 2;
  stroke-linejoin: round;
}

.usage-dot {
  fill: var(--heat-3);
  stroke: var(--surface-base);
  stroke-width: 2;
}

.usage-cross {
  stroke: var(--text-tertiary);
  stroke-dasharray: 3 3;
}

@media (prefers-reduced-motion: no-preference) {
  .usage-cell,
  .usage-bar {
    transition:
      background-color 0.15s,
      fill 0.15s;
  }
}
</style>
