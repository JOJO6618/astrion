<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { t } from '@/locales';
import { openReview } from './api';
import type { Review } from './api';
import GitIcon from './GitIcon.vue';
defineProps<{ reviews: Review[] }>();
const root = ref<HTMLElement>();
const open = ref(false);
const statuses = new Set([
  'pending',
  'running',
  'completed',
  'succeeded',
  'failed',
  'stopped',
  'cancelled',
  'canceled',
  'cancel_requested',
  'saved'
]);
function status(value: string) {
  return statuses.has(value) ? t(`git.status.${value}`) : value;
}
function close(event: FocusEvent) {
  if (!root.value?.contains(event.relatedTarget as Node)) open.value = false;
}
function outside(event: PointerEvent) {
  if (!root.value?.contains(event.target as Node)) open.value = false;
}
onMounted(() => document.addEventListener('pointerdown', outside));
onBeforeUnmount(() => document.removeEventListener('pointerdown', outside));
</script>

<template>
  <div ref="root" class="git-review-menu" @focusout="close" @keydown.esc.stop="open = false">
    <button
      type="button"
      class="git-button git-button--quiet"
      :aria-label="$t('git.reviewHistory')"
      :aria-expanded="open"
      aria-haspopup="menu"
      @click="open = !open"
    >
      <GitIcon name="history" /><span class="git-review-menu__label">{{
        $t('git.reviewHistory')
      }}</span
      ><GitIcon name="chevron" />
    </button>
    <div
      v-if="open"
      class="git-review-menu__popover"
      role="menu"
      :aria-label="$t('git.reviewHistory')"
    >
      <div v-if="!reviews.length" class="git-empty">{{ $t('git.noReviews') }}</div>
      <button
        v-for="review in reviews"
        :key="review.conversation_id"
        type="button"
        role="menuitem"
        @click="openReview(review)"
      >
        <span class="git-review-menu__title">{{
          review.title || `${$t('git.openReview')} · ${review.head_sha.slice(0, 8)}`
        }}</span>
        <span class="git-review-menu__meta"
          ><span>{{ status(review.status) }}</span
          ><time :datetime="review.created_at">{{
            new Date(review.created_at).toLocaleString()
          }}</time></span
        >
      </button>
    </div>
  </div>
</template>

<style scoped lang="scss">
.git-review-menu {
  position: relative;
  flex-shrink: 0;
}
.git-review-menu > .git-button {
  height: 28px;
  padding: 0 3px;
  font-size: 11px;
  color: var(--text-muted);
}
.git-review-menu > .git-button .git-icon {
  width: 13px;
  height: 13px;
}
.git-review-menu__popover {
  position: absolute;
  top: calc(100% + 7px);
  left: 0;
  z-index: 25;
  width: min(380px, calc(100vw - 32px));
  max-height: min(360px, 60vh);
  padding: 6px;
  box-sizing: border-box;
  overflow: auto;
  background: var(--surface-raised);
  border: 1px solid var(--border-default);
  border-radius: 7px;
  box-shadow: var(--shadow-mid);
  scrollbar-width: thin;
}
.git-review-menu__popover button {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 6px;
  width: 100%;
  height: 60px;
  padding: 0 10px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: var(--text-primary);
  text-align: left;
  cursor: pointer;
}
.git-review-menu__popover button + button {
  margin-top: 4px;
}
.git-review-menu__popover button:hover {
  background: var(--hover-bg);
}
.git-review-menu__title {
  width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
}
.git-review-menu__meta {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  color: var(--text-muted);
  font-size: 10px;
  white-space: nowrap;
}
@media (width <= 760px) {
  .git-review-menu {
    position: static;
  }
  .git-review-menu__popover {
    left: auto;
    right: 16px;
  }
}
@media (width <= 600px) {
  .git-review-menu__label {
    display: none;
  }
}
</style>
