<template>
  <div v-if="store.findOpen || store.error" class="nb-floating-status">
    <div v-if="store.findOpen" class="nb-findbar" role="search" :aria-label="$t('browser.find')">
      <BrowserIcon name="search" />
      <input
        ref="findInput"
        v-model="store.findText"
        :placeholder="$t('browser.findPlaceholder')"
        :aria-label="$t('browser.find')"
        @input="find(false)"
        @keydown.enter.prevent="find(true, !$event.shiftKey)"
      />
      <output>{{ store.active?.find?.active || 0 }}/{{ store.active?.find?.matches || 0 }}</output>
      <BrowserButton
        class="nb-up"
        name="chevronDown"
        :label="$t('browser.previous')"
        @click="find(true, false)"
      />
      <BrowserButton name="chevronDown" :label="$t('browser.next')" @click="find(true)" />
      <BrowserButton name="x" :label="$t('browser.dismiss')" @click="closeFind" />
    </div>
    <div v-if="store.error" class="nb-error" role="status">
      <span>{{ store.error }}</span>
      <BrowserButton name="x" :label="$t('browser.dismiss')" @click="store.error = ''" />
    </div>
  </div>
</template>
<script setup lang="ts">
import { inject } from 'vue';
import { browserControlsKey } from './useBrowserControls';
import BrowserIcon from './BrowserIcon.vue';
import BrowserButton from './BrowserButton.vue';
const { store, findInput, find, closeFind } = inject(browserControlsKey)!;
</script>
<style scoped>
.nb-floating-status {
  position: absolute;
  z-index: 10;
  inset: 10px 10px auto;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
  pointer-events: none;
}
.nb-findbar,
.nb-error {
  box-sizing: border-box;
  height: 40px;
  max-width: 100%;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 6px 0 12px;
  border: 1px solid var(--border-default);
  border-radius: 6px;
  background: var(--surface-base);
  pointer-events: auto;
}
.nb-findbar {
  width: 360px;
}
.nb-findbar input {
  height: 100%;
  flex: 1;
  font-size: 12px;
}
.nb-findbar output {
  color: var(--text-tertiary);
  font-size: 11px;
}
.nb-findbar > .nb-icon {
  width: 14px;
  color: var(--text-tertiary);
}
.nb-error {
  width: max-content;
  color: var(--state-danger);
  font-size: 11px;
}
.nb-error span {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
