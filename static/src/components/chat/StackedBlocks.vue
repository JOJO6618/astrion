<template>
  <div
    class="stacked-shell"
    :class="{
      'stacked-shell--single': isSingle,
      'stacked-shell--not-ready': !ready,
      'stacked-shell--no-borders': hideBorders
    }"
    ref="shell"
    :style="{
      height: `${shellHeight}px`,
      paddingTop: moreVisible ? `${moreHeight}px` : '0px'
    }"
  >
    <div
      class="stacked-more-block"
      ref="moreBlock"
      :class="{ visible: moreVisible }"
      :style="{ height: `${moreVisible ? moreHeight : 0}px` }"
      @click="toggleMore"
    >
      <img class="more-icon" src="/static/icons/align-left.svg" :alt="$t('chat.expand')" />
      <div class="more-copy">
        <span class="more-title">{{ moreTitle }}</span>
        <span class="more-desc">{{ moreDesc }}</span>
      </div>
    </div>

    <div class="stacked-viewport" :style="{ height: `${viewportHeight}px` }">
      <TransitionGroup
        name="stacked"
        tag="div"
        class="stacked-inner"
        ref="inner"
        :style="{ transform: `translateY(${innerOffset}px)` }"
        :appear="animated"
        :css="animated"
      >
        <div
          v-for="(action, idx) in stackableActions"
          :key="blockKey(action, idx)"
          class="stacked-item"
        >
          <div
            v-if="action.type === 'thinking'"
            class="collapsible-block thinking-block stacked-block"
            :class="{ expanded: isExpanded(action, idx), processing: action.streaming }"
            :data-block-id="blockKey(action, idx)"
          >
            <div class="collapsible-header" @click="toggleBlock(blockKey(action, idx))">
              <div class="arrow"></div>
              <div class="status-icon">
                <span class="thinking-icon" :class="{ 'thinking-animation': action.streaming }">
                  <span class="icon icon-sm" :style="iconStyle('brain')" aria-hidden="true"></span>
                </span>
              </div>
              <span class="status-text">{{ action.streaming ? $t('chat.thinkingRunning') : $t('chat.thinking') }}</span>
            </div>
            <div class="collapsible-content" :style="contentStyle(blockKey(action, idx))">
              <div
                class="content-inner thinking-content"
                :ref="(el) => registerThinking(blockKey(action, idx), el)"
                @scroll="handleThinkingScrollInternal(blockKey(action, idx), $event)"
                style="max-height: 240px; overflow-y: auto"
              >
                {{ action.content }}
              </div>
            </div>
          </div>

          <div
            v-else-if="action.type === 'tool'"
            class="collapsible-block tool-block stacked-block"
            :class="{
              expanded: isExpanded(action, idx),
              processing: isToolProcessing(action),
              completed: isToolCompleted(action)
            }"
            :data-block-id="blockKey(action, idx)"
          >
            <div class="collapsible-header" @click="toggleBlock(blockKey(action, idx))">
              <div class="arrow"></div>
              <div class="status-icon">
                <span
                  class="tool-icon icon icon-md"
                  :class="getToolAnimationClass(action.tool)"
                  :style="iconStyle(getToolIcon(action.tool))"
                  aria-hidden="true"
                ></span>
              </div>
              <span class="status-text">{{ getToolStatusText(action.tool) }}</span>
              <span class="tool-desc">{{ getToolDescription(action.tool) }}</span>
            </div>
            <div class="collapsible-content" :style="contentStyle(blockKey(action, idx))">
              <div class="content-inner">
                <div v-html="renderToolResult(action)"></div>
              </div>
            </div>
            <div v-if="isToolProcessing(action)" class="progress-indicator"></div>
          </div>
        </div>
      </TransitionGroup>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { t, currentLocale } from '@/locales';
import { usePersonalizationStore } from '@/stores/personalization';
import { renderEnhancedToolResult } from './actions/toolRenderers';

defineOptions({ name: 'StackedBlocks' });

const props = defineProps<{
  actions: any[];
  expandedBlocks: Set<string>;
  conversationRunning?: boolean;
  isLatestMessage?: boolean;
  iconStyle: (key: string, size?: string) => Record<string, string>;
  toggleBlock: (blockId: string) => void;
  registerThinkingRef?: (key: string, el: Element | null) => void;
  handleThinkingScroll?: (blockId: string, event: Event) => void;
  getToolAnimationClass: (tool: any) => Record<string, unknown>;
  getToolIcon: (tool: any) => string;
  getToolStatusText: (tool: any) => string;
  getToolDescription: (tool: any) => string;
  formatSearchTopic: (filters: Record<string, any>) => string;
  formatSearchTime: (filters: Record<string, any>) => string;
  formatSearchDomains: (filters: Record<string, any>) => string;
}>();

const emit = defineEmits<{
  (
    event: 'more-toggle',
    payload: { element: HTMLElement; expanded: boolean; stackKey: string }
  ): void;
}>();

let stackInstanceCounter = 0;
const stackKey = `stacked-${++stackInstanceCounter}`;

// 初始化 personalization store
const personalizationStore = usePersonalizationStore();

const hideBorders = computed(() => personalizationStore.form.stacked_hide_borders);

const renderToolResult = (action: any) => {
  return renderEnhancedToolResult(
    action,
    props.formatSearchTopic,
    props.formatSearchTime,
    props.formatSearchDomains
  );
};

const VISIBLE_LIMIT = 6;

const shell = ref<HTMLElement | null>(null);
const inner = ref<any>(null);
const moreBlock = ref<any>(null);

const showAll = ref(false);
const shellHeight = ref(0);
const moreHeight = ref(0);
const innerOffset = ref(0);
const viewportHeight = ref(0);
// 首帧就绪标志：false 时禁用高度过渡（初始挂载/批量加载直接到位），首次 measure 后置 true。
const ready = ref(false);
const contentHeights = ref<Record<string, number>>({});
const COLLAPSE_MAX_HEIGHT = 600;
const HEADER_FALLBACK = 36; // 仅在 DOM 未挂载、读不到 header 时兜底
// content-inner 真实 DOM 引用，用于读取稳定的内容高度（不受 max-height 过渡影响）
const contentInnerEls = new Map<string, HTMLElement>();

const stackableActions = computed(() =>
  (props.actions || []).filter((item) => item && (item.type === 'thinking' || item.type === 'tool'))
);

const hiddenCount = computed(() => Math.max(0, stackableActions.value.length - VISIBLE_LIMIT));
const moreVisible = computed(() => hiddenCount.value > 0 || showAll.value);
// 单块场景：外观需与原 single 路径(.collapsible-block 12px 圆角/透明底)一致，
// 由 .stacked-shell--single 还原，避免「单块也走堆叠组件」后单块外观变样。
const isSingle = computed(() => stackableActions.value.length === 1);

// 是否播放进场动画：仅「对话运行中 + 当前是最新消息」时播（新块逐个平滑进场）。
// 历史加载 / 对话重建 / 非最新消息：禁用 appear + enter 动画，块瞬间出现，
// 避免一次性挂载多个块时整个对话区剧烈高度跳动。
const animated = computed(() => !!props.conversationRunning && !!props.isLatestMessage);
const totalSteps = computed(() => stackableActions.value.length);
const moreTitle = computed(() => {
  void currentLocale.value;
  return showAll.value ? t('chat.allExpanded') : t('chat.more');
});
const moreDesc = computed(() => {
  void currentLocale.value;
  return showAll.value
    ? t('chat.stepsTotal', { n: totalSteps.value })
    : t('chat.stepsHidden', { n: hiddenCount.value });
});

const blockKey = (action: any, idx: number) => action?.blockId || action?.id || `stacked-${idx}`;

const isExpanded = (action: any, idx: number) => props.expandedBlocks?.has(blockKey(action, idx));
const isExpandedById = (blockId: string) => props.expandedBlocks?.has(blockId);
const contentStyle = (blockId: string) => {
  const h = Math.max(0, contentHeights.value[blockId] || 0);
  return { maxHeight: isExpandedById(blockId) ? `${h}px` : '0px' };
};

const isToolProcessing = (action: any) => {
  const status = action?.tool?.status;
  return status === 'preparing' || status === 'running';
};

const isToolCompleted = (action: any) => action?.tool?.status === 'completed';

const toggleMore = () => {
  showAll.value = !showAll.value;
  scheduleMeasure();
  const el = moreBlock.value;
  if (el instanceof HTMLElement) {
    emit('more-toggle', { element: el, expanded: showAll.value, stackKey });
  }
};

const toggleBlock = (blockId: string) => {
  if (typeof props.toggleBlock === 'function') {
    props.toggleBlock(blockId);
    scheduleMeasure();
  }
};

const registerThinking = (key: string, el: Element | null) => {
  // 记录 content-inner 真实引用用于高度测量，同时透传给上层（思考块自动滚动）
  if (el instanceof HTMLElement) {
    contentInnerEls.set(key, el);
  } else {
    contentInnerEls.delete(key);
  }
  if (typeof props.registerThinkingRef === 'function') {
    props.registerThinkingRef(key, el);
  }
};

const handleThinkingScrollInternal = (blockId: string, event: Event) => {
  if (typeof props.handleThinkingScroll === 'function') {
    props.handleThinkingScroll(blockId, event);
  }
};

const resolveEl = (target: any): HTMLElement | null => {
  if (!target) return null;
  if (target instanceof HTMLElement) return target;
  if (target.$el && target.$el instanceof HTMLElement) return target.$el;
  return null;
};

const moreBaseHeight = () => {
  const moreEl = resolveEl(moreBlock.value);
  if (!moreEl) return 56;
  const label = moreEl.querySelector('.more-title') as HTMLElement | null;
  return Math.max(48, Math.ceil(label?.getBoundingClientRect().height || 56));
};

// 核心：测真实 DOM 的稳定值算每块高度，再算 shell/offset/viewport。
// 关键稳定性保证：
//  - header.offsetHeight：表头固定高(min/max-height:36)，展开/折叠都不变；
//  - content-inner.offsetHeight：内容自然高度，即使父级 collapsible-content 的 max-height
//    正在 0↔目标 过渡（被 overflow:hidden 裁切），content-inner 自身 offsetHeight 仍是
//    稳定的完整值（裁切不影响子级布局高度）。
// 因此任何时刻读到的都是「目标态」高度，不是动画中间帧 → 写入 shellHeight/innerOffset 后由
// CSS transition 平滑（外框增高 / 传送带上移 / 展开收起），绝不抖动。
const measureAndCompute = () => {
  const innerEl = resolveEl(inner.value);
  const shellEl = resolveEl(shell.value);
  if (!shellEl || !innerEl) return;

  const children = Array.from(innerEl.children) as HTMLElement[];
  const acts = stackableActions.value;
  const heights: number[] = [];
  const nextContentHeights: Record<string, number> = {};

  children.forEach((el, idx) => {
    const action = acts[idx];
    const key = blockKey(action, idx);
    const header = el.querySelector('.collapsible-header') as HTMLElement | null;
    const innerC = el.querySelector('.content-inner') as HTMLElement | null;
    const headerH = header ? Math.ceil(header.offsetHeight) : HEADER_FALLBACK;
    // 展开后内容可见高度（封顶 600）；content-inner 自身高度稳定（思考块被自己的
    // max-height:240 限制，工具块为内容自然高），不受 collapsible-content 过渡影响。
    const fullContent = innerC ? Math.min(Math.ceil(innerC.offsetHeight), COLLAPSE_MAX_HEIGHT) : 0;
    nextContentHeights[key] = fullContent;
    const expanded = isExpandedById(key);
    // 分隔线：隐藏边线模式下统一为 0，否则最后一块无 border-bottom
    const borderH = hideBorders.value ? 0 : idx === children.length - 1 ? 0 : 1;
    heights.push(headerH + (expanded ? fullContent : 0) + borderH);
  });

  contentHeights.value = nextContentHeights;

  const sum = (arr: number[]) => (arr.length ? arr.reduce((a, b) => a + b, 0) : 0);
  const totalHeight = sum(heights);
  const hiddenHeight = sum(heights.slice(0, Math.max(0, heights.length - VISIBLE_LIMIT)));
  const windowHeight = sum(heights.slice(-VISIBLE_LIMIT));

  moreHeight.value = moreVisible.value ? moreBaseHeight() : 0;
  const targetShell =
    moreHeight.value + (showAll.value || !moreVisible.value ? totalHeight : windowHeight);
  // shellHeight 需 +2 补偿 border-box（上下 border 各 1px），
  // 确保内容区 ≥ viewport 高度，避免 overflow:hidden 裁切最后一块
  shellHeight.value = targetShell + 2;
  innerOffset.value = showAll.value || !moreVisible.value ? 0 : -hiddenHeight;
  viewportHeight.value = Math.max(0, targetShell - moreHeight.value);
};

// 等 DOM 渲染稳定后测量一次（双帧：Vue patch + 浏览器布局完成后再读 offsetHeight）。
const scheduleMeasure = () => {
  nextTick(() => {
    requestAnimationFrame(() => {
      measureAndCompute();
      // 首次测量、高度直接到位后，再下一帧启用过渡；此后展开/收起/增长等变化才平滑，
      // 而初始挂载/批量加载不会从 0 高度过渡跳动。
      if (!ready.value) {
        requestAnimationFrame(() => {
          ready.value = true;
        });
      }
    });
  });
};

let resizeObserver: ResizeObserver | null = null;
let roRaf: number | null = null;

const observeAll = () => {
  if (!resizeObserver) return;
  resizeObserver.disconnect();
  // 只观察 content-inner（内容自然高度，流式增长会变）。不观察 collapsible-content /
  // stacked-inner 等被 transition 的元素，避免「写 → 触发 RO → 再写」的回环抖动。
  contentInnerEls.forEach((el) => resizeObserver!.observe(el));
};

onMounted(() => {
  scheduleMeasure();
  if (typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => {
      if (roRaf) cancelAnimationFrame(roRaf);
      roRaf = requestAnimationFrame(() => measureAndCompute());
    });
    nextTick(() => observeAll());
  }
});

onBeforeUnmount(() => {
  if (resizeObserver) {
    resizeObserver.disconnect();
    resizeObserver = null;
  }
  if (roRaf) {
    cancelAnimationFrame(roRaf);
    roRaf = null;
  }
});

watch(
  () => {
    const acts = stackableActions.value;
    const firstId = acts[0]?.blockId || acts[0]?.id || '';
    const lastId = acts[acts.length - 1]?.blockId || acts[acts.length - 1]?.id || '';
    // 指纹：块数 + 首尾 + moreVisible + 每块内部状态/内容长度桶（追踪流式增长），
    // 比直接 watch 数组引用更精准，避免父组件重渲染传新引用导致的过度触发。
    const snap = acts
      .map((a: any) => {
        if (a?.type === 'tool') return `T:${a?.tool?.status || '?'}${a?.tool?.result ? 'R' : '-'}`;
        if (a?.type === 'thinking')
          return `H:${a?.streaming ? 'S' : '-'}${Math.floor((a?.content?.length || 0) / 48)}`;
        return '?';
      })
      .join(',');
    return `${acts.length}|${firstId}|${lastId}|${moreVisible.value}|${snap}`;
  },
  () => {
    nextTick(() => {
      observeAll();
      scheduleMeasure();
    });
  }
);

watch(
  () => (props.expandedBlocks ? props.expandedBlocks.size : 0),
  () => scheduleMeasure()
);

watch(hideBorders, () => scheduleMeasure());
</script>

<style scoped>
/* 重构核心：shell 高度、viewport 高度、inner 位移三者用同一条 transition，使「外框增高」
   「内部显示区裁切边界」「传送带上移/展开收起」完全同步过渡，时长/缓动与单块
   collapsible-content(max-height 260ms) 对齐，互不打架。
   替换了原先 JS 逐帧 lerp(animateMoreToggle) + 360ms runSync 轮询的方案，从根上去抖。
   注意：viewport(显示区) 必须与 shell 同步过渡，否则收起瞬间 viewport 高度先跳到折叠后
   终值，把还在收缩的下方块瞬间裁掉再重现，造成割裂。 */
.stacked-shell {
  transition:
    height 260ms cubic-bezier(0.4, 0, 0.2, 1),
    padding-top 260ms cubic-bezier(0.4, 0, 0.2, 1);
  will-change: height;
}
.stacked-viewport {
  transition: height 260ms cubic-bezier(0.4, 0, 0.2, 1);
  will-change: height;
}
.stacked-inner {
  transition: transform 260ms cubic-bezier(0.4, 0, 0.2, 1);
  will-change: transform;
}
/* 首帧未就绪（ready=false）：禁用 shell/viewport/inner 的高度/位移过渡，让初始挂载 /
   批量历史加载时高度直接到位，避免从 0 高度过渡造成整个对话区剧烈跳动。
   首次 measure 完成后置 ready=true，此后展开/收起、流式增长、传送带等高度变化均平滑——
   注意：展开/收起是用户主动交互，历史对话里也必须平滑，故用 ready(一次性) 而非 animated 控制。
   块的 enter/appear 进场动画另由模板 :css/:appear="animated" 控制（仅运行中+最新才播）。
   同时禁用内部 collapsible-content 的 max-height/opacity 过渡，否则历史加载时已展开块的
   内容会从 opacity:0 淡入(表现为内部文字仍有动画)。 */
.stacked-shell--not-ready,
.stacked-shell--not-ready .stacked-viewport,
.stacked-shell--not-ready .stacked-inner,
.stacked-shell--not-ready .collapsible-content {
  transition: none !important;
}
/* 单块场景：还原原 single 路径(.collapsible-block)的外观——12px 圆角 + 透明底，
   而非多块外壳的 16px 圆角 + card 底。其余(1px 边框)两者本就一致。
   这样「单块也走堆叠组件」后，单块视觉与改造前完全相同。 */
.stacked-shell--single {
  border-radius: 12px;
  background: transparent;
}
</style>
