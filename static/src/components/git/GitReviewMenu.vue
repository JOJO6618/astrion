<script setup lang="ts">
/**
 * 评审记录：ghost 按钮 + 操作菜单（UiMenu，两行选项：对话标题 / 状态 · 时间），右缘对齐按钮。
 */
import { computed, ref } from 'vue';
import { t } from '@/locales';
import UiIcon from '@/components/ui/UiIcon.vue';
import UiMenu from '@/components/ui/UiMenu.vue';
import { openReview, shortDate, type Review } from './api';
const props = defineProps<{ reviews: Review[] }>();
const trigger = ref<HTMLButtonElement | null>(null);
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
const items = computed(() =>
  props.reviews.length
    ? props.reviews.map((review) => ({
        value: review.conversation_id,
        label: review.title || `${t('git.openReview')} · ${review.head_sha.slice(0, 8)}`,
        description: `${status(review.status)} · ${shortDate(review.created_at)}`
      }))
    : [{ value: '', label: t('git.noReviews'), disabled: true }]
);
function close(restoreFocus: boolean) {
  open.value = false;
  if (restoreFocus) trigger.value?.focus({ preventScroll: true });
}
function pick(id: string) {
  const review = props.reviews.find((item) => item.conversation_id === id);
  if (review) openReview(review);
}
</script>

<template>
  <button
    ref="trigger"
    type="button"
    class="git-button git-button--ghost git-review-trigger"
    :aria-label="$t('git.reviewHistory')"
    :aria-expanded="open"
    aria-haspopup="menu"
    @click="open = !open"
  >
    <UiIcon name="chat-bubble" /><span class="git-review-trigger__label">{{
      $t('git.reviewHistory')
    }}</span>
  </button>
  <UiMenu
    actions
    align-end
    :open="open"
    :anchor="trigger"
    :items="items"
    :min-width="300"
    :label="$t('git.reviewHistory')"
    @select="pick"
    @close="close"
  />
</template>
