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
/* 悬浮条：弹层底色与描边；内部 32px 按钮离外框 4px，外框圆角同心 = 8 + 4 = 12 */
.nb-findbar,
.nb-error {
  box-sizing: border-box;
  height: 42px;
  max-width: 100%;
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 0 4px 0 12px;
  border: 1px solid var(--popover-border);
  border-radius: 12px;
  background: var(--popover-surface);
  pointer-events: auto;
}
.nb-findbar {
  width: 360px;
}
.nb-findbar input {
  height: 100%;
  flex: 1;
  margin-left: 6px;
  font-size: 13px;
}
.nb-findbar output {
  margin-right: 6px;
  color: var(--text-tertiary);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.nb-findbar > .nb-icon {
  width: 14px;
  color: var(--text-tertiary);
}
.nb-error {
  width: max-content;
  color: var(--state-danger);
  font-size: 12px;
}
.nb-error span {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
