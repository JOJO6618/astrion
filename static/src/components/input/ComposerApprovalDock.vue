<template>
  <Transition
    name="composer-approval-motion"
    appear
    @before-enter="startPanelTransition"
    @before-leave="startPanelTransition"
    @after-enter="finishPanelTransition"
    @after-leave="finishPanelTransition"
    @enter-cancelled="finishPanelTransition"
    @leave-cancelled="finishPanelTransition"
  >
    <div v-if="panelVisible" ref="panelRoot" class="composer-approval-dock">
      <ToolApprovalPanel
        :approvals="displayApprovals"
        :deciding-approval-ids="decidingApprovalIds"
        :review-records="displayRecords"
        @close="$emit('collapse')"
        @approve="$emit('approve', $event)"
        @reject="$emit('reject', $event)"
      />
    </div>
  </Transition>
  <Transition name="composer-approval-restore-motion" appear @after-leave="notifyLayout">
    <div
      v-if="restoreVisible"
      ref="restoreRoot"
      class="composer-approval-restore-dock"
      :style="{ bottom: `calc(100% + ${restoreOffset}px)` }"
    >
      <button
        type="button"
        class="composer-approval-dock__restore"
        :aria-label="$t('shell.restoreApprovalPanel')"
        @mousedown.prevent
        @click="$emit('restore')"
      >
        <span>{{ $t('shell.pendingApproval') }}</span>
        <ApprovalChevron up />
      </button>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { computed, ref, shallowRef, watch, onBeforeUnmount } from 'vue';
import ToolApprovalPanel from '@/components/panels/ToolApprovalPanel.vue';
import ApprovalChevron from './ApprovalChevron.vue';
import type { ToolApproval, ApprovalReviewRecord } from './approvalModel';

const props = defineProps<{
  visible: boolean;
  collapsed: boolean;
  statusVisible: boolean;
  queueHeight?: number;
  approvals: ToolApproval[];
  decidingApprovalIds?: string[];
  reviewRecords?: ApprovalReviewRecord[];
}>();
const emit = defineEmits<{
  (event: 'layout-change'): void;
  (event: 'transitioning', active: boolean): void;
  (event: 'restore'): void;
  (event: 'collapse'): void;
  (event: 'approve', approvalId: string): void;
  (event: 'reject', approvalId: string): void;
}>();
const panelVisible = computed(() => props.visible && !props.collapsed);
const panelTransitioning = ref(false);
const restoreVisible = computed(() => props.visible && !!props.approvals.length && props.collapsed);
// Keep the last live content intact until the leaving DOM is removed.
const displayApprovals = shallowRef<ToolApproval[]>([]);
const displayRecords = shallowRef<ApprovalReviewRecord[]>([]);
watch(
  () => [panelVisible.value, props.approvals, props.reviewRecords] as const,
  () => {
    if (!panelVisible.value) return;
    displayApprovals.value = props.approvals.map((item) => ({ ...item }));
    displayRecords.value = (props.reviewRecords || []).map((record) => ({ ...record }));
  },
  { immediate: true, deep: true }
);
const transitioningPanels = new Set<Element>();
function startPanelTransition(element: Element) {
  transitioningPanels.add(element);
  panelTransitioning.value = true;
  if (element instanceof HTMLElement) element.inert = !panelVisible.value;
  emit('transitioning', true);
  notifyLayout();
}
function finishPanelTransition(element: Element) {
  transitioningPanels.delete(element);
  panelTransitioning.value = transitioningPanels.size > 0;
  emit('transitioning', panelTransitioning.value);
  notifyLayout();
}
function notifyLayout() {
  emit('layout-change');
}
// Git/status row is 34px high, 6px above the shell. Leave another 6px gap.
const restoreOffset = computed(() =>
  props.queueHeight ? props.queueHeight + 6 : props.statusVisible ? 46 : 6
);
const panelRoot = ref<HTMLElement | null>(null);
const restoreRoot = ref<HTMLElement | null>(null);
let observer: ResizeObserver | null = null;
watch(
  [panelRoot, restoreRoot],
  (nodes) => {
    observer?.disconnect();
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(notifyLayout);
      nodes.forEach((node) => node && observer?.observe(node));
    }
    notifyLayout();
  },
  { flush: 'post' }
);
onBeforeUnmount(() => observer?.disconnect());
</script>

<style scoped>
.composer-approval-dock,
.composer-approval-restore-dock {
  position: absolute;
  left: 5%;
  width: 90%;
  max-width: 90%;
  z-index: 1;
  pointer-events: auto;
}
.composer-approval-dock {
  bottom: calc(100% - 1px);
}
.composer-approval-restore-dock {
  display: flex;
  justify-content: flex-end;
  height: 30px;
}
.composer-approval-motion-enter-active,
.composer-approval-motion-leave-active {
  transition:
    transform 300ms cubic-bezier(0.25, 0.8, 0.25, 1),
    clip-path 300ms cubic-bezier(0.25, 0.8, 0.25, 1);
  transform-origin: bottom center;
  will-change: transform, clip-path;
}
.composer-approval-motion-enter-from,
.composer-approval-motion-leave-to {
  transform: translateY(100%);
  clip-path: inset(0 0 100% 0 round 9px 9px 0 0);
}
.composer-approval-motion-enter-to,
.composer-approval-motion-leave-from {
  transform: translateY(0);
  clip-path: inset(0 0 0 0 round 9px 9px 0 0);
}
.composer-approval-restore-motion-enter-active,
.composer-approval-restore-motion-leave-active {
  transition:
    transform 300ms cubic-bezier(0.25, 0.8, 0.25, 1),
    opacity 300ms cubic-bezier(0.25, 0.8, 0.25, 1);
  will-change: transform, opacity;
}
.composer-approval-restore-motion-enter-from,
.composer-approval-restore-motion-leave-to {
  transform: translateY(36px);
  opacity: 0;
}
.composer-approval-motion-leave-active,
.composer-approval-restore-motion-leave-active {
  pointer-events: none;
}
.composer-approval-dock__restore {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 30px;
  max-width: 100%;
  padding: 0 9px 0 11px;
  border: 1px solid var(--border-default);
  border-radius: 7px;
  background: var(--surface-panel);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
}
.composer-approval-dock__restore:hover {
  background: var(--surface-soft);
  color: var(--text-primary);
}
.composer-approval-dock__restore span {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
@media (prefers-reduced-motion: reduce) {
  .composer-approval-motion-enter-active,
  .composer-approval-motion-leave-active,
  .composer-approval-restore-motion-enter-active,
  .composer-approval-restore-motion-leave-active {
    transition-duration: 1ms;
  }
}
</style>
