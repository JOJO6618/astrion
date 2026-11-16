<script setup lang="ts">
import { t } from '@/locales';
import ScheduleIcon from './ScheduleIcon.vue';
import { joinWide, runBadge, runDuration, runTitle, shortDate } from './scheduleFormat';
import type { ScheduleRun } from './useSchedules';

defineProps<{ runs: ScheduleRun[] | null; timezone: string; canRun: boolean }>();
const emit = defineEmits<{
  (event: 'back'): void;
  (event: 'open', run: ScheduleRun): void;
  (event: 'run'): void;
}>();
</script>
<template>
  <div class="sch-subview-toolbar">
    <div class="sch-subview-heading">
      <button
        type="button"
        class="sch-icon-button"
        :aria-label="t('schedules.backToSettings')"
        @click="emit('back')"
      >
        <ScheduleIcon name="arrow-left" />
      </button>
      <h3>{{ t('schedules.history') }}</h3>
    </div>
    <span v-if="runs">{{ t('schedules.recentRuns', { count: String(runs.length) }) }}</span>
  </div>
  <div v-if="runs === null" class="sch-empty-state">{{ t('schedules.loading') }}</div>
  <div v-else-if="runs.length" class="sch-history-list">
    <button
      v-for="run in runs"
      :key="run.id"
      type="button"
      class="sch-history-row"
      @click="emit('open', run)"
    >
      <span class="sch-history-status" :class="`is-${runBadge(run.state).tone}`">
        <ScheduleIcon :name="runBadge(run.state).icon" />
      </span>
      <span class="sch-history-text">
        <span class="sch-history-title">{{ runTitle(run) }}</span>
        <span class="sch-history-meta">{{
          joinWide(
            shortDate(run.scheduled_for || run.created_at, timezone),
            t(`schedules.state_${run.state}`)
          )
        }}</span>
      </span>
      <span class="sch-history-duration">{{ runDuration(run) }}</span>
    </button>
  </div>
  <div v-else class="sch-empty-state">
    <ScheduleIcon name="clock" />
    <p>{{ t('schedules.noRuns') }}</p>
    <button
      type="button"
      class="sch-button sch-button-quiet"
      :disabled="!canRun"
      @click="emit('run')"
    >
      {{ t('schedules.run') }}
    </button>
  </div>
</template>
