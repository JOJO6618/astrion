<template>
  <transition name="overlay-fade">
    <div v-if="open" class="exp-overlay">
      <div class="exp-card">
        <div class="exp-header">
          <div>
            <div class="title">{{ $t('personalization.expTitle') }}</div>
            <div class="subtitle">{{ $t('personalization.expSubtitle') }}</div>
          </div>
          <button class="confirm-btn" type="button" @click="confirmSelection">{{ $t('personalization.expConfirmStart') }}</button>
        </div>
        <div class="grid">
          <div
            v-for="item in modes"
            :key="item.id"
            class="mode-card"
            :class="{ active: selected === item.id }"
            @click="selected = item.id"
          >
            <div class="badge">{{ $t(item.badge) }}</div>
            <div class="icon" v-html="item.icon" aria-hidden="true"></div>
            <h3>{{ $t(item.title) }}</h3>
            <p>{{ $t(item.desc) }}</p>
          </div>
        </div>
      </div>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue';

type Mode = 'fast' | 'thinking' | 'research' | 'expert';

const props = defineProps<{
  open: boolean;
  initial?: Mode;
}>();

const emit = defineEmits<{
  (event: 'close'): void;
  (event: 'confirm', mode: Mode): void;
}>();

const selected = ref<Mode>(props.initial || 'fast');

watch(
  () => props.initial,
  (val) => {
    if (val) selected.value = val;
  }
);

const modes = [
  {
    id: 'fast',
    badge: 'personalization.expModeFastBadge',
    title: 'personalization.expModeFastTitle',
    desc: 'personalization.expModeFastDesc',
    icon: `<svg viewBox="0 0 24 24" stroke="currentColor" fill="none" stroke-width="2"><path d="M13 2 5 14h6l-2 8 10-12h-6z"/></svg>`
  },
  {
    id: 'thinking',
    badge: 'personalization.expModeThinkingBadge',
    title: 'personalization.expModeThinkingTitle',
    desc: 'personalization.expModeThinkingDesc',
    icon: `<svg viewBox="0 0 24 24" stroke="currentColor" fill="none" stroke-width="2"><path d="M12 18V5"/><path d="M15 13a4.17 4.17 0 0 1-3-4 4.17 4.17 0 0 1-3 4"/><path d="M17.598 6.5A3 3 0 1 0 12 5a3 3 0 1 0-5.598 1.5"/><path d="M17.997 5.125a4 4 0 0 1 2.526 5.77"/><path d="M18 18a4 4 0 0 0 2-7.464"/><path d="M19.967 17.483A4 4 0 1 1 12 18a4 4 0 1 1-7.967-.517"/><path d="M6 18a4 4 0 0 1-2-7.464"/><path d="M6.003 5.125a4 4 0 0 0-2.526 5.77"/></svg>`
  },
  {
    id: 'research',
    badge: 'personalization.expModeResearchBadge',
    title: 'personalization.expModeResearchTitle',
    desc: 'personalization.expModeResearchDesc',
    icon: `<svg viewBox="0 0 24 24" stroke="currentColor" fill="none" stroke-width="2"><path d="M9 3v5l-4 9a2 2 0 0 0 1.8 3h10.4A2 2 0 0 0 19 17l-4-9V3"/><path d="M9 8h6"/></svg>`
  },
  {
    id: 'expert',
    badge: 'personalization.expModeExpertBadge',
    title: 'personalization.expModeExpertTitle',
    desc: 'personalization.expModeExpertDesc',
    icon: `<svg viewBox="0 0 24 24" stroke="currentColor" fill="none" stroke-width="2"><path d="M3 9 12 5l9 4-9 4-9-4Z"/><path d="M6 10v5c0 .6.4 1.2 1 1.4l5 2.1 5-2.1c.6-.2 1-.8 1-1.4v-5"/><path d="M12 13v6"/></svg>`
  }
] as const;

function confirmSelection() {
  emit('confirm', selected.value);
  emit('close');
}
</script>

<style scoped>
.exp-overlay {
  position: fixed;
  inset: 0;
  background: var(--overlay-scrim);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  z-index: 1200;
}
.exp-card {
  width: min(1100px, 96vw);
  background: var(--surface-soft);
  border-radius: 18px;
  padding: 24px 28px;
  box-shadow: none;
  border: 1px solid var(--border-default);
}
.exp-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 18px;
  gap: 12px;
}
.title {
  font-size: 22px;
  color: var(--text-primary);
  font-weight: 700;
}
.subtitle {
  font-size: 14px;
  color: var(--text-secondary);
}
.confirm-btn {
  border: none;
  background: var(--accent);
  color: var(--on-accent);
  border-radius: 12px;
  padding: 10px 18px;
  font-weight: 600;
  cursor: pointer;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 16px;
}
.mode-card {
  border: 1px solid var(--border-default);
  border-radius: 14px;
  padding: 16px 14px;
  cursor: pointer;
  transition: 0.2s ease;
  box-shadow: none;
  background: var(--surface-raised);
}
.mode-card:hover {
  transform: translateY(-2px);
  box-shadow: none;
}
.mode-card.active {
  border-color: var(--accent);
  box-shadow: none;
}
.badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--accent-strong);
  background: var(--highlight);
  padding: 4px 10px;
  border-radius: 20px;
  margin-bottom: 8px;
}
.icon {
  width: 52px;
  height: 52px;
  border-radius: 14px;
  background: var(--highlight);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 10px;
  color: var(--accent-strong);
}
.icon svg {
  width: 30px;
  height: 30px;
}
.mode-card h3 {
  margin: 0 0 6px;
  font-size: 17px;
  color: var(--text-primary);
}
.mode-card p {
  margin: 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--text-secondary);
}
</style>
