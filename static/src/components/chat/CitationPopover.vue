<template>
  <teleport to="body">
    <!-- 默认模式下进出同时进行：切换引用时旧卡片沿原路收回、新卡片在新引用处展开（DESIGN.md §6.10） -->
    <Transition enter-active-class="is-entering" leave-active-class="is-leaving">
      <CitationCard
        v-if="citationPopover.current"
        :key="citationPopover.current.id"
        :card="citationPopover.current"
      />
    </Transition>
  </teleport>
</template>

<script setup lang="ts">
/**
 * 行内引用悬停卡的挂载点与全局收起规则：外部按下、Esc、页面滚动、窗口 resize 时收起。
 * 悬停／点击状态机在 citationChips.ts，单张卡片的内容与定位在 CitationCard.vue。
 */
import { onBeforeUnmount, onMounted } from 'vue';
import { citationPopover, closeCitationPopover } from './citationChips';
import CitationCard from './CitationCard.vue';

defineOptions({ name: 'CitationPopover' });

/** 活动卡片或引用胶囊内的目标不算外部（退出中的旧卡片已 pointer-events: none） */
function insideCardOrChip(target: EventTarget | null) {
  return (
    target instanceof Element &&
    !!target.closest('.citation-popover:not(.is-leaving), .md-citation-chip')
  );
}

function onPointerDown(event: PointerEvent) {
  if (citationPopover.current && !insideCardOrChip(event.target)) closeCitationPopover();
}

// Esc 先由卡片消费，不连带关闭所在页面或弹窗（§6.3）
function onKeydown(event: KeyboardEvent) {
  if (event.key !== 'Escape' || !citationPopover.current) return;
  event.stopPropagation();
  event.preventDefault();
  closeCitationPopover();
}

// 页面一旦滚动就收起（含固定态）；卡片内部滚动除外
function onScroll(event: Event) {
  if (!citationPopover.current) return;
  const target = event.target;
  if (target instanceof Element && target.closest('.citation-popover')) return;
  closeCitationPopover();
}

const onResize = () => closeCitationPopover();

onMounted(() => {
  document.addEventListener('pointerdown', onPointerDown, true);
  document.addEventListener('keydown', onKeydown, true);
  document.addEventListener('scroll', onScroll, true);
  window.addEventListener('resize', onResize);
});

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onPointerDown, true);
  document.removeEventListener('keydown', onKeydown, true);
  document.removeEventListener('scroll', onScroll, true);
  window.removeEventListener('resize', onResize);
});
</script>
