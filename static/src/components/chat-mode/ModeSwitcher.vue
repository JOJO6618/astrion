<template>
  <div ref="root" class="mode-switcher" @keydown.esc="open = false">
    <button
      class="mode-switcher__trigger"
      type="button"
      :aria-label="$t('chatMode.mode')"
      aria-haspopup="menu"
      :aria-expanded="open"
      @click="open = !open"
    >
      <span>Astrion {{ mode === 'chat' ? $t('chatMode.chat') : $t('chatMode.agent') }}</span>
      <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
        <path d="m4 6 4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.5" />
      </svg>
    </button>
    <div v-if="open" class="mode-switcher__menu" role="menu">
      <button
        v-for="item in modes"
        :key="item"
        role="menuitemradio"
        :aria-checked="item === mode"
        type="button"
        @click="choose(item)"
      >
        <span class="mode-switcher__copy">
          <span class="mode-switcher__name">
            {{ item === 'chat' ? $t('chatMode.chat') : $t('chatMode.agent') }}
          </span>
          <span class="mode-switcher__description">
            {{ item === 'chat' ? $t('chatMode.chatDescription') : $t('chatMode.agentDescription') }}
          </span>
        </span>
        <span class="mode-switcher__check" aria-hidden="true">{{ item === mode ? '✓' : '' }}</span>
      </button>
    </div>
  </div>
</template>
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
const props = defineProps<{ mode: 'chat' | 'agent' }>();
const open = ref(false);
const root = ref<HTMLElement>();
const modes = ['agent', 'chat'] as const;
function choose(mode: 'chat' | 'agent') {
  open.value = false;
  if (mode !== props.mode) window.location.assign(mode === 'chat' ? '/chat/new' : '/new');
}
function outside(event: Event) {
  if (!root.value?.contains(event.target as Node)) open.value = false;
}
onMounted(() => document.addEventListener('pointerdown', outside));
onBeforeUnmount(() => document.removeEventListener('pointerdown', outside));
</script>
<style scoped lang="scss">
.mode-switcher {
  position: relative;
  height: var(--conversation-header-height);
}
.mode-switcher :where(button) {
  height: var(--conversation-header-height);
  border: 0;
  color: var(--text-primary);
  font: inherit;
  cursor: pointer;
}
.mode-switcher__trigger {
  display: flex;
  align-items: center;
  gap: 8px;
  max-width: 100%;
  padding: 0 8px;
  border-radius: 8px;
  background: transparent;
  font-size: 18px;
  font-weight: 600;
}
.mode-switcher__trigger:hover,
.mode-switcher__trigger:focus-visible {
  background: var(--hover-bg);
}
.mode-switcher__trigger:focus-visible {
  outline: 1px solid var(--border-default);
  outline-offset: -1px;
}
.mode-switcher__trigger svg {
  flex: 0 0 auto;
  color: var(--text-secondary);
}
.mode-switcher__menu {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
  z-index: 30;
  width: 224px;
  max-width: min(calc(var(--conversation-expanded-width) - 12px), calc(100vw - 24px));
  max-height: 200px;
  overflow: auto;
  padding: 5px;
  border: 1px solid var(--border-default);
  border-radius: 10px;
  background: var(--surface-card);
  box-shadow: var(--shadow-mid);
  scrollbar-width: thin;
  scrollbar-color: var(--border-default) transparent;
}
.mode-switcher__menu button {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  width: 100%;
  height: 52px;
  padding: 0 10px;
  border-radius: 7px;
  background: transparent;
  text-align: left;
}
.mode-switcher__menu button:hover,
.mode-switcher__menu button:focus-visible {
  outline: none;
  background: var(--hover-bg);
}
.mode-switcher__copy {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 2px;
  min-width: 0;
  height: 38px;
}
.mode-switcher__name,
.mode-switcher__description {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.mode-switcher__name {
  font-size: 15px;
  font-weight: 600;
  line-height: 20px;
}
.mode-switcher__description {
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 16px;
}
.mode-switcher__check {
  flex: 0 0 14px;
  font-size: 15px;
  text-align: center;
}
</style>
