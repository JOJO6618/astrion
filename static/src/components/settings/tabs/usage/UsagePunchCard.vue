<script setup lang="ts">
import { computed } from 'vue';
import { t } from '@/locales';
import UsageTooltip from './UsageTooltip.vue';
import { formatTokens, quantileLevels, weekdayName } from './format';
import { useUsageTooltip } from './useUsageTooltip';

defineOptions({ name: 'UsagePunchCard' });

const props = defineProps<{ punch: number[][]; timezone: string }>();

const { tip, hide, show } = useUsageTooltip();
const HOURS = Array.from({ length: 24 }, (_, hour) => hour);

const levelOf = computed(() => quantileLevels(props.punch.flat()));

const peak = computed(() => {
  let best = { day: -1, hour: -1, value: 0 };
  props.punch.forEach((row, day) =>
    row.forEach((value, hour) => {
      if (value > best.value) best = { day, hour, value };
    })
  );
  return best.day < 0
    ? ''
    : t('usageStats.punchPeak', {
        day: weekdayName(best.day),
        from: `${best.hour}:00`,
        to: `${best.hour + 1}:00`
      });
});

function onOver(event: Event) {
  const target = (event.target as HTMLElement)?.closest('[data-day]');
  if (!target) return;
  const day = Number(target.getAttribute('data-day'));
  const hour = Number(target.getAttribute('data-hour'));
  const value = props.punch[day]?.[hour] || 0;
  show(
    target,
    `${weekdayName(day)} ${hour}:00–${hour + 1}:00`,
    value > 0 ? t('usageStats.valueTokens', { value: formatTokens(value) }) : t('usageStats.punchIdle')
  );
}


</script>

<template>
  <div class="usage-punch-wrap">
    <div class="usage-punch" role="img" :aria-label="$t('usageStats.punchAria')" @mouseover="onOver" @mouseleave="hide">
      <span></span>
      <span v-for="hour in HOURS" :key="`h${hour}`" class="usage-punch-hour">{{ hour % 3 === 0 ? hour : '' }}</span>
      <template v-for="(row, day) in punch" :key="day">
        <span class="usage-punch-day">{{ weekdayName(day) }}</span>
        <span
          v-for="hour in HOURS"
          :key="hour"
          class="usage-punch-cell"
          :class="row[hour] > 0 ? `l${levelOf(row[hour])}` : ''"
          :data-day="day"
          :data-hour="hour"
        ></span>
      </template>
    </div>
    <p class="usage-note">
      <span v-if="peak">{{ peak }} · </span>{{ $t('usageStats.punchNote', { tz: timezone }) }}
    </p>
    <UsageTooltip :state="tip" />
  </div>
</template>

<style scoped>
.usage-punch {
  display: grid;
  grid-template-columns: 36px repeat(24, minmax(0, 1fr));
  gap: 3px;
  max-width: 720px;
}

.usage-punch-hour {
  height: 14px;
  font-size: 10px;
  line-height: 14px;
  color: var(--text-tertiary);
  white-space: nowrap;
}

.usage-punch-day {
  display: flex;
  align-items: center;
  overflow: hidden;
  font-size: 11px;
  color: var(--text-tertiary);
  white-space: nowrap;
}

.usage-punch-cell {
  aspect-ratio: 1;
  max-width: 100%;
  border-radius: 3px;
  background: var(--heat-0);
}

.usage-punch-cell.l1 {
  background: var(--heat-1);
}

.usage-punch-cell.l2 {
  background: var(--heat-2);
}

.usage-punch-cell.l3 {
  background: var(--heat-3);
}

.usage-punch-cell.l4 {
  background: var(--heat-4);
}

.usage-note {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--text-tertiary);
}
</style>
