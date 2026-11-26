<template>
  <div ref="content">
    <slot
      v-for="(action, offset) in visibleActions"
      :key="action.id || action.blockId || `step-${start + offset}`"
      :action="action"
      :index="start + offset"
    />
  </div>
</template>

<script setup lang="ts" generic="T extends { id?: string; blockId?: string }">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';

const props = defineProps<{ actions: T[]; limited: boolean; expanded: boolean }>();
const content = ref<HTMLElement | null>(null);
const batchSize = 4;
const firstIndex = ref(Math.max(0, props.actions.length - batchSize));
const start = computed(() =>
  props.limited ? Math.min(firstIndex.value, Math.max(0, props.actions.length - batchSize)) : 0
);
const visibleActions = computed(() =>
  start.value ? props.actions.slice(start.value) : props.actions
);

let wrapper: HTMLElement | null = null;
let observer: ResizeObserver | undefined;
let updating = false;
let previousTop = 0;

// content 是滚动容器的直接子元素。补旧行时记住同一个 DOM 节点，
// 这样浏览器自身的滚动锚定和同时到达的新步骤都不会造成双重补偿。
async function fill(loadOlder = false) {
  const list = content.value;
  const scroller = wrapper;
  if (updating || !list || !scroller || !props.limited || !props.expanded || !list.offsetWidth)
    return;
  const limit = parseFloat(getComputedStyle(scroller).maxHeight);
  if (!Number.isFinite(limit)) return;
  updating = true;
  try {
    // 多留一段可滚动空间，即使第一批恰好填满也能向上触发续读。
    while (start.value > 0 && (loadOlder || list.offsetHeight < limit + 48)) {
      const anchor = list.firstElementChild;
      const before = anchor?.getBoundingClientRect().top;
      firstIndex.value = Math.max(0, start.value - batchSize);
      await nextTick();
      if (wrapper !== scroller || !props.limited || !props.expanded) return;
      if (before !== undefined && anchor?.isConnected) {
        scroller.scrollTop += anchor.getBoundingClientRect().top - before;
      }
      loadOlder = false;
    }
  } finally {
    previousTop = scroller.scrollTop;
    updating = false;
  }
}

function onScroll() {
  if (!wrapper) return;
  const top = wrapper.scrollTop;
  const nearStart = top < previousTop && top <= 64;
  previousTop = top;
  if (nearStart) void fill(true);
}

function showLatest() {
  if (!wrapper || !props.limited || !props.expanded) return;
  wrapper.scrollTop = wrapper.scrollHeight;
  previousTop = wrapper.scrollTop;
  void fill();
}

onMounted(() => {
  wrapper = content.value!.parentElement;
  if (!wrapper) return;
  wrapper.addEventListener('scroll', onScroll, { passive: true });
  observer = new ResizeObserver(() => {
    void fill();
  });
  observer.observe(wrapper);
  observer.observe(content.value!);
  showLatest();
});

watch(
  () => props.limited,
  async (limited) => {
    if (!limited) return;
    firstIndex.value = Math.max(0, props.actions.length - batchSize);
    await nextTick();
    showLatest();
  }
);

onBeforeUnmount(() => {
  wrapper?.removeEventListener('scroll', onScroll);
  observer?.disconnect();
  wrapper = null;
});
</script>
