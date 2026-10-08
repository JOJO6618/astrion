<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { t } from '@/locales';
import { usePersonalizationStore } from '@/stores/personalization';
import { useResourceStore } from '@/stores/resource';
import type { ConversationCosts, CostActor } from './costTypes';

const props = defineProps<{
  costs?: ConversationCosts | null;
  conversationId: string | null;
  active: boolean;
}>();
const personalization = usePersonalizationStore();
const resource = useResourceStore();
const currency = computed(() => personalization.form.display_currency || 'USD');
const anchor = ref<HTMLElement | null>(null);
const opened = ref(false);
const position = ref({ left: '0px', top: '0px', maxHeight: '280px' });
let hideTimer: ReturnType<typeof setTimeout> | null = null;
let pollTimer: ReturnType<typeof setInterval> | null = null;
let generation = 0;

const rate = computed(() => (currency.value === 'USD' ? 1 : props.costs?.exchange_rate?.usd_cny));
function money(value: string) {
  if (!rate.value || !Number.isFinite(rate.value)) return '—';
  const amount = Number(value) * rate.value;
  if (!Number.isFinite(amount)) return '—';
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: currency.value,
    currencyDisplay: 'narrowSymbol',
    minimumFractionDigits: 4,
    maximumFractionDigits: 4
  }).format(amount);
}
function actorAmount(actor: CostActor) {
  if (actor.priced_requests) return money(actor.cost_usd);
  if (actor.historical_unpriced || actor.unpriced_requests) return '—';
  if (actor.subscription_requests) return t('sidebar.costSubscription');
  if (actor.local_requests) return t('sidebar.costLocal');
  return money('0');
}
const totalText = computed(() => {
  const costs = props.costs;
  if (!costs) return '—';
  if (costs.has_priced) return money(costs.total_usd);
  if (costs.partial) return '—';
  if (costs.actors.some((a) => a.subscription_requests)) return t('sidebar.costSubscription');
  if (costs.actors.some((a) => a.local_requests)) return t('sidebar.costLocal');
  return money('0');
});
const rateText = computed(() => {
  const fx = props.costs?.exchange_rate;
  if (currency.value !== 'CNY') return '';
  if (!fx?.usd_cny) return t('sidebar.costRateUnavailable');
  return t(fx.stale ? 'sidebar.costRateCached' : 'sidebar.costRate', {
    rate: fx.usd_cny.toFixed(4),
    date: fx.rate_date || ''
  });
});

function place() {
  const box = anchor.value?.getBoundingClientRect();
  if (!box) return;
  const width = Math.min(300, window.innerWidth - 24);
  position.value = {
    left: `${Math.max(12, Math.min(box.right - width, window.innerWidth - width - 12))}px`,
    top: `${box.bottom + 8}px`,
    maxHeight: `${Math.max(80, Math.min(320, window.innerHeight - box.bottom - 20))}px`
  };
}
function keepOpen() {
  if (hideTimer) clearTimeout(hideTimer);
  hideTimer = null;
}
function open() {
  keepOpen();
  place();
  opened.value = true;
}
function closeSoon() {
  keepOpen();
  hideTimer = setTimeout(() => {
    opened.value = false;
  }, 160);
}
function close() {
  keepOpen();
  opened.value = false;
}
watch(opened, (value) => {
  if (value) {
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
  } else {
    window.removeEventListener('resize', place);
    window.removeEventListener('scroll', place, true);
  }
});
watch(
  () => [props.conversationId, props.active] as const,
  ([id, active]) => {
    const owner = ++generation;
    close();
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
    if (!id || !active) return;
    let busy = false;
    const refresh = async () => {
      if (busy || owner !== generation) return;
      busy = true;
      try {
        await resource.fetchConversationTokenStatistics(id);
      } finally {
        busy = false;
      }
    };
    void refresh();
    pollTimer = setInterval(refresh, 5000);
  },
  { immediate: true }
);
onBeforeUnmount(() => {
  ++generation;
  if (pollTimer) clearInterval(pollTimer);
  close();
  window.removeEventListener('resize', place);
  window.removeEventListener('scroll', place, true);
});
</script>

<template>
  <div class="stat-block cost-summary">
    <div class="stat-label">{{ $t('sidebar.totalCost') }}</div>
    <button
      ref="anchor"
      type="button"
      class="stat-value cost-trigger"
      :aria-label="$t('sidebar.costDetails')"
      :aria-expanded="opened"
      @mouseenter="open"
      @mouseleave="closeSoon"
      @focus="open"
      @blur="closeSoon"
      @click="opened ? close() : open()"
      @keydown.esc="close"
    >
      {{ totalText }}
      <span v-if="costs?.partial && costs?.has_priced" class="cost-partial">{{
        $t('sidebar.costPartial')
      }}</span>
    </button>
    <Teleport to="body">
      <div
        v-if="opened"
        class="cost-popover"
        :style="position"
        role="tooltip"
        @mouseenter="keepOpen"
        @mouseleave="closeSoon"
        @keydown.esc="close"
      >
        <div class="cost-heading">{{ $t('sidebar.costDetails') }}</div>
        <div v-for="actor in costs?.actors || []" :key="actor.id" class="cost-row">
          <span class="cost-name">{{
            actor.kind === 'main' ? $t('sidebar.costMainAgent') : actor.name
          }}</span>
          <span class="cost-amount">{{ actorAmount(actor) }}</span>
          <span v-if="actor.historical_unpriced || actor.unpriced_requests" class="cost-row-note">{{
            $t('sidebar.costPartial')
          }}</span>
        </div>
        <div v-if="costs?.partial" class="cost-note">{{ $t('sidebar.costIncomplete') }}</div>
        <div v-if="rateText" class="cost-note">{{ rateText }}</div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped lang="scss">
.cost-summary {
  min-width: 0;
}
.cost-trigger {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 26px;
  max-width: 100%;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--text-primary);
  font: inherit;
  font-size: 18px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  cursor: pointer;
  white-space: nowrap;
  &:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 3px;
  }
}
.cost-partial {
  color: var(--text-muted);
  font-size: 10px;
  font-weight: 400;
}
.cost-popover {
  position: fixed;
  z-index: 10020;
  width: min(300px, calc(100vw - 24px));
  overflow: auto;
  padding: 12px 14px;
  border: 1px solid var(--border-default);
  border-radius: 8px;
  background: var(--surface-card);
  color: var(--text-primary);
  box-shadow: var(--shadow-soft);
  font-size: 12px;
  scrollbar-width: thin;
  scrollbar-color: var(--border-default) transparent;
}
.cost-heading {
  height: 28px;
  font-weight: 600;
}
.cost-row {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 32px;
  border-top: 1px solid var(--border-default);
  font-variant-numeric: tabular-nums;
}
.cost-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cost-amount {
  flex-shrink: 0;
}
.cost-row-note {
  color: var(--text-muted);
  font-size: 10px;
}
.cost-note {
  margin-top: 8px;
  color: var(--text-secondary);
  font-size: 11px;
  line-height: 1.6;
}
</style>
