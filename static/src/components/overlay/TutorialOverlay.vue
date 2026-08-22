<template>
  <div
    v-if="running"
    class="tutorial-overlay"
    aria-live="polite"
    @wheel="handleWheel"
    @touchstart="handleTouchStart"
    @touchmove.prevent="handleTouchMove"
    @touchend="handleTouchEnd"
    @touchcancel="handleTouchEnd"
  >
    <div class="tutorial-click-blocker" @click.stop></div>
    <div
      v-if="stepReady && maskVisible"
      class="tutorial-mask"
      :style="[maskTopStyle, maskInteractivityStyle]"
      @click.stop
    ></div>
    <div
      v-if="stepReady && maskVisible"
      class="tutorial-mask"
      :style="[maskLeftStyle, maskInteractivityStyle]"
      @click.stop
    ></div>
    <div
      v-if="stepReady && maskVisible"
      class="tutorial-mask"
      :style="[maskRightStyle, maskInteractivityStyle]"
      @click.stop
    ></div>
    <div
      v-if="stepReady && maskVisible"
      class="tutorial-mask"
      :style="[maskBottomStyle, maskInteractivityStyle]"
      @click.stop
    ></div>

    <div
      v-if="stepReady && maskVisible"
      class="tutorial-highlight"
      :style="highlightStyle"
      aria-hidden="true"
    ></div>
    <span
      v-if="clickEffect"
      class="tutorial-click-effect"
      :style="{ left: `${clickEffect.x - 9}px`, top: `${clickEffect.y - 9}px` }"
      aria-hidden="true"
    ></span>

    <section
      ref="panelRef"
      class="tutorial-popover"
      :class="[`placement-${resolvedPlacement}`, { 'is-hidden': !stepReady }]"
      :style="popoverStyle"
      @click.stop
    >
      <header class="tutorial-popover__header">
        <span class="tutorial-popover__step">{{ visibleStepIndex }} / {{ totalVisibleSteps }}</span>
        <button type="button" class="tutorial-popover__close" @click="handleExit">退出</button>
      </header>
      <h3 class="tutorial-popover__title">{{ step?.title || '新手教程' }}</h3>
      <p class="tutorial-popover__desc">{{ step?.description || '' }}</p>
      <p v-if="isWaitingTarget" class="tutorial-popover__warn">
        未找到目标元素，可稍后重试或跳过当前步骤。
      </p>
      <p v-else-if="isMustClick" class="tutorial-popover__hint">请点击高亮目标继续。</p>
      <p v-if="showPersonalScrollHint" class="tutorial-popover__hint">
        提示：个人空间内容可上下滚动查看。
      </p>

      <footer class="tutorial-popover__actions">
        <button type="button" class="tutorial-btn" :disabled="nextDisabled" @click="goNext">
          {{ nextLabel }}
        </button>
      </footer>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { useTutorialStore, type TutorialPlacement } from '@/stores/tutorial';

interface RectLike {
  top: number;
  left: number;
  width: number;
  height: number;
  right: number;
  bottom: number;
}

defineOptions({ name: 'TutorialOverlay' });

const GAP = 14;
const HIGHLIGHT_PADDING = 2;

const tutorialStore = useTutorialStore();
const {
  running,
  activeStep: step,
  visibleStepIndex,
  totalVisibleSteps
} = storeToRefs(tutorialStore);

const panelRef = ref<HTMLElement | null>(null);
const targetRect = ref<RectLike | null>(null);
const popoverTop = ref(120);
const popoverLeft = ref(120);
const resolvedPlacement = ref<TutorialPlacement>('center');
const clickEffect = ref<{ x: number; y: number } | null>(null);
let refreshTimer: number | null = null;
let stepSettleTimer: number | null = null;
let lastTouchY: number | null = null;
const stepReady = ref(true);

const isMustClick = computed(() => step.value?.mode === 'must_click');
const isLastStep = computed(() => step.value?.id === 'done');
const isWaitingTarget = computed(() => Boolean(step.value?.target) && !targetRect.value);
const maskVisible = computed(() => Boolean(targetRect.value));
const nextDisabled = computed(() => isMustClick.value && !!targetRect.value);
const isPersonalTutorialPhase = computed(() => {
  const id = step.value?.id || '';
  return (
    id === 'personal-overview' ||
    id === 'open-personal-space' ||
    id === 'close-personal-space' ||
    id.startsWith('tab-') ||
    id.startsWith('page-')
  );
});
const showPersonalScrollHint = computed(() => isPersonalTutorialPhase.value);
const nextLabel = computed(() => {
  if (isLastStep.value) return '完成';
  if (isMustClick.value) return targetRect.value ? '请先点击高亮目标' : '下一步（跳过）';
  return '下一步';
});
const maskInteractivityStyle = computed(() => ({ pointerEvents: 'auto' }));

const clampedRect = computed<RectLike | null>(() => {
  if (!targetRect.value) return null;
  const top = Math.max(0, targetRect.value.top - HIGHLIGHT_PADDING);
  const left = Math.max(0, targetRect.value.left - HIGHLIGHT_PADDING);
  const width = Math.max(12, targetRect.value.width + HIGHLIGHT_PADDING * 2);
  const height = Math.max(12, targetRect.value.height + HIGHLIGHT_PADDING * 2);
  return { top, left, width, height, right: left + width, bottom: top + height };
});

const maskTopStyle = computed(() => {
  const rect = clampedRect.value;
  if (!rect) return {};
  return { top: '0px', left: '0px', width: '100vw', height: `${Math.max(0, rect.top)}px` };
});

const maskLeftStyle = computed(() => {
  const rect = clampedRect.value;
  if (!rect) return {};
  return {
    top: `${rect.top}px`,
    left: '0px',
    width: `${Math.max(0, rect.left)}px`,
    height: `${rect.height}px`
  };
});

const maskRightStyle = computed(() => {
  const rect = clampedRect.value;
  if (!rect) return {};
  return {
    top: `${rect.top}px`,
    left: `${rect.right}px`,
    width: `${Math.max(0, window.innerWidth - rect.right)}px`,
    height: `${rect.height}px`
  };
});

const maskBottomStyle = computed(() => {
  const rect = clampedRect.value;
  if (!rect) return {};
  return {
    top: `${rect.bottom}px`,
    left: '0px',
    width: '100vw',
    height: `${Math.max(0, window.innerHeight - rect.bottom)}px`
  };
});

const highlightStyle = computed(() => {
  const rect = clampedRect.value;
  if (!rect) return {};
  return {
    top: `${rect.top}px`,
    left: `${rect.left}px`,
    width: `${rect.width}px`,
    height: `${rect.height}px`
  };
});

const popoverStyle = computed(() => ({
  top: `${popoverTop.value}px`,
  left: `${popoverLeft.value}px`
}));

const getCurrentTarget = () => {
  if (!step.value?.target) {
    tutorialStore.setActiveSelector(null);
    return null;
  }
  const candidates = Array.from(document.querySelectorAll(step.value.target)) as HTMLElement[];
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const scored = candidates
    .map((node) => {
      const rect = node.getBoundingClientRect();
      const style = window.getComputedStyle(node);
      const area = Math.max(0, rect.width) * Math.max(0, rect.height);
      const visibleWidth = Math.max(0, Math.min(rect.right, vw) - Math.max(rect.left, 0));
      const visibleHeight = Math.max(0, Math.min(rect.bottom, vh) - Math.max(rect.top, 0));
      const visibleArea = visibleWidth * visibleHeight;
      return {
        node,
        rect,
        area,
        visibleArea,
        display: style.display,
        visibility: style.visibility,
        opacity: Number(style.opacity || '1')
      };
    })
    .filter((item) => {
      if (item.area <= 0) return false;
      if (item.visibleArea <= 0) return false;
      if (item.display === 'none' || item.visibility === 'hidden') return false;
      return item.opacity > 0.01;
    })
    .sort((a, b) => {
      if (b.visibleArea !== a.visibleArea) return b.visibleArea - a.visibleArea;
      return a.area - b.area;
    });
  const el = (scored[0]?.node || null) as HTMLElement | null;
  tutorialStore.setActiveSelector(step.value.target);
  return el;
};

const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max);

const placePopover = (rect: RectLike | null) => {
  const panel = panelRef.value;
  if (!panel) return;

  const panelWidth = Math.max(280, panel.offsetWidth || 320);
  const panelHeight = Math.max(170, panel.offsetHeight || 200);
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const edge = 16;

  if (!rect || step.value?.placement === 'center') {
    resolvedPlacement.value = 'center';
    popoverLeft.value = clamp((vw - panelWidth) / 2, edge, Math.max(edge, vw - panelWidth - edge));
    popoverTop.value = clamp((vh - panelHeight) / 2, edge, Math.max(edge, vh - panelHeight - edge));
    return;
  }

  const preferred = step.value?.placement || 'auto';
  const spaces = {
    top: rect.top,
    right: vw - rect.right,
    bottom: vh - rect.bottom,
    left: rect.left
  };

  const placements: Array<'top' | 'right' | 'bottom' | 'left'> =
    preferred === 'auto'
      ? (['right', 'bottom', 'left', 'top'] as Array<'top' | 'right' | 'bottom' | 'left'>).sort(
          (a, b) => spaces[b] - spaces[a]
        )
      : [preferred as 'top' | 'right' | 'bottom' | 'left', 'right', 'bottom', 'left', 'top'];

  let best: {
    p: 'top' | 'right' | 'bottom' | 'left';
    left: number;
    top: number;
    overlap: number;
  } | null = null;
  for (const p of Array.from(new Set(placements))) {
    let top = 0;
    let left = 0;
    if (p === 'right') {
      left = rect.right + GAP;
      top = rect.top + rect.height / 2 - panelHeight / 2;
    } else if (p === 'left') {
      left = rect.left - panelWidth - GAP;
      top = rect.top + rect.height / 2 - panelHeight / 2;
    } else if (p === 'top') {
      left = rect.left + rect.width / 2 - panelWidth / 2;
      top = rect.top - panelHeight - GAP;
    } else {
      left = rect.left + rect.width / 2 - panelWidth / 2;
      top = rect.bottom + GAP;
    }

    const clampedLeft = clamp(left, edge, Math.max(edge, vw - panelWidth - edge));
    const clampedTop = clamp(top, edge, Math.max(edge, vh - panelHeight - edge));
    const fits =
      left >= edge &&
      top >= edge &&
      left + panelWidth <= vw - edge &&
      top + panelHeight <= vh - edge;
    const overlapX = Math.max(
      0,
      Math.min(clampedLeft + panelWidth, rect.right) - Math.max(clampedLeft, rect.left)
    );
    const overlapY = Math.max(
      0,
      Math.min(clampedTop + panelHeight, rect.bottom) - Math.max(clampedTop, rect.top)
    );
    const overlap = overlapX * overlapY;
    if (fits) {
      resolvedPlacement.value = p;
      popoverLeft.value = left;
      popoverTop.value = top;
      return;
    }
    if (!best || overlap < best.overlap) {
      best = { p, left: clampedLeft, top: clampedTop, overlap };
    }
  }

  if (best) {
    resolvedPlacement.value = best.p;
    popoverLeft.value = best.left;
    popoverTop.value = best.top;
    return;
  }
  resolvedPlacement.value = 'center';
  popoverLeft.value = clamp((vw - panelWidth) / 2, edge, Math.max(edge, vw - panelWidth - edge));
  popoverTop.value = clamp((vh - panelHeight) / 2, edge, Math.max(edge, vh - panelHeight - edge));
};

const updateRect = () => {
  if (!running.value) {
    targetRect.value = null;
    return;
  }
  const el = getCurrentTarget();
  if (!el) {
    targetRect.value = null;
    resolvedPlacement.value = step.value?.placement || 'center';
    placePopover(null);
    return;
  }
  const rect = el.getBoundingClientRect();
  targetRect.value =
    rect.width > 0 && rect.height > 0
      ? {
          top: rect.top,
          left: rect.left,
          width: rect.width,
          height: rect.height,
          right: rect.right,
          bottom: rect.bottom
        }
      : null;
  placePopover(targetRect.value);
};

const scheduleRefresh = () => {
  window.setTimeout(updateRect, 30);
};

const beginStepTransition = () => {
  stepReady.value = false;
  targetRect.value = null;
  popoverLeft.value = -9999;
  popoverTop.value = -9999;
  if (stepSettleTimer) {
    window.clearTimeout(stepSettleTimer);
    stepSettleTimer = null;
  }
};

const handleCaptureClick = (event: MouseEvent) => {
  if (!running.value || !isMustClick.value || !step.value?.target) return;
  const clicked = event.target as HTMLElement | null;
  if (!clicked) return;
  if (!clicked.closest(step.value.target)) return;
  window.setTimeout(() => tutorialStore.goNext(), 280);
};

const getPersonalScrollTarget = () => {
  const pageCandidates = Array.from(
    document.querySelectorAll('.personalization-content .personal-page')
  ) as HTMLElement[];
  const pageScrollable = pageCandidates.find((el) => el.scrollHeight - el.clientHeight > 2);
  if (pageScrollable) return pageScrollable;
  const content = document.querySelector('.personalization-content') as HTMLElement | null;
  if (content && content.scrollHeight - content.clientHeight > 2) return content;
  return (
    (document.querySelector('[data-tutorial="personal-content-shell"]') as HTMLElement | null) ||
    content
  );
};

const handleWheel = (event: WheelEvent) => {
  if (!isPersonalTutorialPhase.value) {
    event.preventDefault();
    return;
  }
  const scrollTarget = getPersonalScrollTarget();
  if (!scrollTarget) {
    event.preventDefault();
    return;
  }
  scrollTarget.scrollBy({ top: event.deltaY, left: 0, behavior: 'auto' });
  event.preventDefault();
};

const handleTouchStart = (event: TouchEvent) => {
  if (!event.touches.length) return;
  lastTouchY = event.touches[0].clientY;
};

const handleTouchMove = (event: TouchEvent) => {
  if (!event.touches.length) return;
  const currentY = event.touches[0].clientY;
  const prevY = lastTouchY ?? currentY;
  const deltaY = prevY - currentY;
  lastTouchY = currentY;

  if (!isPersonalTutorialPhase.value) {
    event.preventDefault();
    return;
  }
  const scrollTarget = getPersonalScrollTarget();
  if (!scrollTarget) {
    event.preventDefault();
    return;
  }
  scrollTarget.scrollBy({ top: deltaY, left: 0, behavior: 'auto' });
  event.preventDefault();
};

const handleTouchEnd = () => {
  lastTouchY = null;
};

const playClickEffect = (targetEl: HTMLElement | null) => {
  if (!targetEl) return;
  const rect = targetEl.getBoundingClientRect();
  const x = rect.left + rect.width / 2;
  const y = rect.top + rect.height / 2;
  clickEffect.value = { x, y };
  window.setTimeout(() => {
    clickEffect.value = null;
  }, 460);
};

const shouldScrollBeforeAutoClick = () => {
  if (!step.value) return false;
  const isTabStep = step.value.id.startsWith('tab-');
  if (isTabStep) return true;
  const isMobile = window.innerWidth <= 768;
  return isMobile && step.value.id === 'mobile-menu-personal';
};

const triggerAutoClick = () => {
  if (step.value?.autoOutsideClick) {
    const menu = document.querySelector('.model-mode-dropdown') as HTMLElement | null;
    const targetEl = menu || (document.body as HTMLElement);
    playClickEffect(targetEl);
    window.setTimeout(() => {
      const event = new MouseEvent('click', {
        bubbles: true,
        cancelable: true,
        clientX: 12,
        clientY: 12
      });
      document.body.dispatchEvent(event);
    }, 420);
    return true;
  }
  if (!step.value?.autoClick || !step.value?.target) return false;
  const el = getCurrentTarget();
  if (!el) return false;
  const shouldScroll = shouldScrollBeforeAutoClick();
  if (shouldScroll) {
    const isMobile = window.innerWidth <= 768;
    el.scrollIntoView({
      behavior: 'smooth',
      block: isMobile ? 'nearest' : 'center',
      inline: isMobile ? 'center' : 'nearest'
    });
  }
  window.setTimeout(
    () => {
      playClickEffect(el);
    },
    shouldScroll ? 220 : 0
  );
  window.setTimeout(
    () => {
      (el as HTMLElement).click();
    },
    shouldScroll ? 640 : 420
  );
  return true;
};

const goNext = () => {
  if (nextDisabled.value) return;
  beginStepTransition();
  if (step.value?.id === 'done') {
    tutorialStore.finishTutorial();
    return;
  }
  if (!isMustClick.value) {
    const clicked = triggerAutoClick();
    if (clicked) {
      window.setTimeout(() => tutorialStore.goNext(), 700);
      return;
    }
  }
  tutorialStore.goNext();
};

const handleExit = () => tutorialStore.exitTutorial();

watch(
  () => [running.value, step.value?.id],
  async ([, stepId], [prevRunning, prevStepId]) => {
    if (!running.value) {
      targetRect.value = null;
      stepReady.value = true;
      return;
    }
    const isStepSwitch = Boolean(prevRunning) && Boolean(prevStepId) && prevStepId !== stepId;
    if (isStepSwitch) {
      stepReady.value = false;
      if (stepSettleTimer) {
        window.clearTimeout(stepSettleTimer);
      }
      const nextStep = step.value;
      const settleDelay =
        nextStep?.mode === 'must_click' || nextStep?.autoClick || nextStep?.autoOutsideClick
          ? 500
          : 0;
      stepSettleTimer = window.setTimeout(async () => {
        await nextTick();
        updateRect();
        await nextTick();
        updateRect();
        stepReady.value = true;
      }, settleDelay);
      return;
    }
    await nextTick();
    updateRect();
    await nextTick();
    updateRect();
    stepReady.value = true;
  }
);

onMounted(() => {
  window.addEventListener('click', handleCaptureClick, true);
  window.addEventListener('resize', scheduleRefresh);
  window.addEventListener('scroll', scheduleRefresh, true);
  refreshTimer = window.setInterval(updateRect, 260);
});

onBeforeUnmount(() => {
  window.removeEventListener('click', handleCaptureClick, true);
  window.removeEventListener('resize', scheduleRefresh);
  window.removeEventListener('scroll', scheduleRefresh, true);
  if (refreshTimer) {
    window.clearInterval(refreshTimer);
    refreshTimer = null;
  }
  if (stepSettleTimer) {
    window.clearTimeout(stepSettleTimer);
    stepSettleTimer = null;
  }
});
</script>
