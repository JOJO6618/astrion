<script setup lang="ts">
/**
 * 推理强度滑块（顶栏模型弹窗，2026-11-18 缩小版）：档位由使用方给出，
 * 最左可以是「无」（不思考），其余为思考强度；「默认推理」开关在弹窗底部，
 * 打开时本组件只置灰（dimmed），点击任意档位由使用方解除默认。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { t } from '@/locales';
import type { ReasoningStop } from '@/utils/reasoningStops';

const props = withDefaults(
  defineProps<{
    stops: ReasoningStop[];
    modelValue: ReasoningStop;
    dimmed?: boolean;
    disabled?: boolean;
  }>(),
  { dimmed: false, disabled: false }
);
const emit = defineEmits<{ (event: 'update:modelValue', value: ReasoningStop): void }>();

// 两端档位中心 inset；比圆钮半径(11)小 1px，圆钮在两端时盖住填充条端头
const EDGE = 10;
// 圆钮滑动：先带惯性减速冲过目标一点（越界量随距离增长，最多 3px），再缓缓回弹到位
const KNOB_MOVE_MS = 240;
const KNOB_SETTLE_MS = 100;
const OVERSHOOT_RATIO = 0.05;
const OVERSHOOT_MAX = 3;

const trackRef = ref<HTMLElement | null>(null);
const knobRef = ref<HTMLElement | null>(null);
const fillRef = ref<HTMLElement | null>(null);
const dotEls: (HTMLElement | null)[] = [];
const setDotRef = (el: Element | null, i: number) => {
  dotEls[i] = (el as HTMLElement | null) ?? null;
};
const trackWidth = ref(0);
const levelIndex = ref(Math.max(0, props.stops.indexOf(props.modelValue)));
const activeLevel = ref(levelIndex.value);
const dragging = ref(false);
const liveDrag = ref(false);
let downX = 0;
let animFrame = 0;
// 圆钮/填充的视觉位置经 applyVisualX 直接写 DOM（transform + clip-path，
// 合成器属性），拖动全程不经过 Vue 渲染，避免逐帧 reflow 卡顿
let visualX = 0;

const stopLabel = (stop: ReasoningStop) =>
  stop === 'none'
    ? t('quickdock.effortNone')
    : stop === 'on'
      ? t('quickdock.effortThinking')
      : stop;
const valueLabel = computed(() =>
  props.dimmed ? t('quickdock.effortDefault') : stopLabel(props.stops[activeLevel.value])
);

const last = () => Math.max(1, props.stops.length - 1);
const posRatio = (i: number) => i / last();
const posPct = (i: number) => `calc(${EDGE}px + (100% - ${EDGE * 2}px) * ${posRatio(i)})`;
// 档位文字：两端贴轨道外缘（不探出面板内边距），中间居中于档位点
const labelStyle = (i: number) =>
  i === 0
    ? { left: '0' }
    : i === props.stops.length - 1
      ? { right: '0' }
      : { left: posPct(i), transform: 'translateX(-50%)' };
const posPx = (i: number) => EDGE + (trackWidth.value - EDGE * 2) * posRatio(i);
const pxToLevel = (x: number) => {
  const w = trackWidth.value;
  if (w <= EDGE * 2) return 0;
  const ratio = (x - EDGE) / (w - EDGE * 2);
  return Math.max(0, Math.min(props.stops.length - 1, Math.round(ratio * last())));
};
const clampEventX = (clientX: number) => {
  const el = trackRef.value;
  if (!el) return EDGE;
  const rect = el.getBoundingClientRect();
  return Math.max(EDGE, Math.min(rect.width - EDGE, clientX - rect.left));
};
const easeOutCubic = (p: number) => 1 - (1 - p) ** 3;
const easeInOutSine = (p: number) => -(Math.cos(Math.PI * p) - 1) / 2;
const reducedMotion = () =>
  typeof window !== 'undefined' &&
  window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true;

const applyVisualX = (x: number) => {
  const measured = trackWidth.value > EDGE * 2;
  const expr = measured ? `${x}px` : posPct(levelIndex.value);
  if (knobRef.value) knobRef.value.style.transform = `translate(calc(${expr} - 50%), -50%)`;
  if (fillRef.value) fillRef.value.style.clipPath = `inset(0 calc(100% - (${expr})) 0 0 round 9px)`;
  for (let i = 0; i < props.stops.length; i++) {
    const el = dotEls[i];
    if (!el) continue;
    el.classList.toggle('covered', measured ? posPx(i) <= x + 0.5 : i <= levelIndex.value);
  }
};
const cancelKnobAnim = () => {
  if (animFrame) cancelAnimationFrame(animFrame);
  animFrame = 0;
};
const setKnobInstant = (x: number) => {
  cancelKnobAnim();
  visualX = x;
  applyVisualX(x);
};
// 从当前视觉位置滑到目标（进行中的动画被接续，不跳变）：
// 阶段一 easeOutCubic 冲到「目标 + 越界量」，阶段二 easeInOutSine 回弹到目标；
// 距离很短时不越界，只做减速
const animateKnobTo = (target: number) => {
  if (reducedMotion()) return setKnobInstant(target);
  const start = visualX;
  const distance = target - start;
  if (Math.abs(distance) < 0.5) return setKnobInstant(target);
  cancelKnobAnim();
  const overshoot =
    Math.sign(distance) * Math.min(OVERSHOOT_MAX, Math.abs(distance) * OVERSHOOT_RATIO);
  const peak = target + (Math.abs(overshoot) >= 0.5 ? overshoot : 0);
  const settleMs = peak === target ? 0 : KNOB_SETTLE_MS;
  const t0 = performance.now();
  const step = (now: number) => {
    const elapsed = now - t0;
    if (elapsed < KNOB_MOVE_MS) {
      visualX = start + (peak - start) * easeOutCubic(elapsed / KNOB_MOVE_MS);
    } else {
      const p = settleMs ? Math.min(1, (elapsed - KNOB_MOVE_MS) / settleMs) : 1;
      visualX = peak + (target - peak) * easeInOutSine(p);
    }
    applyVisualX(visualX);
    animFrame = elapsed < KNOB_MOVE_MS + settleMs ? requestAnimationFrame(step) : 0;
    if (!animFrame) setKnobInstant(target);
  };
  animFrame = requestAnimationFrame(step);
};
const syncToLevel = (animate = false) => {
  activeLevel.value = levelIndex.value;
  const target = posPx(levelIndex.value);
  if (animate && trackWidth.value > EDGE * 2) animateKnobTo(target);
  else setKnobInstant(target);
};
const commitLevel = () => emit('update:modelValue', props.stops[levelIndex.value]);

const onPointerDown = (e: PointerEvent) => {
  if (props.disabled) return;
  dragging.value = true;
  liveDrag.value = false;
  downX = e.clientX;
  trackRef.value?.setPointerCapture(e.pointerId);
  const next = pxToLevel(clampEventX(e.clientX));
  // 点在当前档上不重复保存；置灰（默认推理）时任何档位都解除默认
  if (next !== levelIndex.value || props.dimmed) {
    levelIndex.value = next;
    commitLevel();
  }
  syncToLevel(true);
};
const onPointerMove = (e: PointerEvent) => {
  if (!dragging.value) return;
  if (!liveDrag.value && Math.abs(e.clientX - downX) > 3) liveDrag.value = true;
  if (!liveDrag.value) return;
  const x = clampEventX(e.clientX);
  cancelKnobAnim();
  visualX = x;
  applyVisualX(x);
  activeLevel.value = pxToLevel(x);
};
const onPointerUp = (e: PointerEvent) => {
  if (!dragging.value) return;
  dragging.value = false;
  if (liveDrag.value) {
    liveDrag.value = false;
    const next = pxToLevel(clampEventX(e.clientX));
    if (next !== levelIndex.value) {
      levelIndex.value = next;
      commitLevel();
    }
  }
  syncToLevel(true);
};
// 键盘：左右键逐档
const onKeydown = (e: KeyboardEvent) => {
  if (props.disabled) return;
  const delta = e.key === 'ArrowLeft' ? -1 : e.key === 'ArrowRight' ? 1 : 0;
  if (!delta) return;
  e.preventDefault();
  const next = Math.max(0, Math.min(props.stops.length - 1, levelIndex.value + delta));
  if (next === levelIndex.value && !props.dimmed) return;
  levelIndex.value = next;
  commitLevel();
  syncToLevel(true);
};

/** 外部状态与本地档位对齐（保存失败回滚、模型切换后调用） */
const sync = (animate = true) => {
  if (dragging.value) return;
  const i = props.stops.indexOf(props.modelValue);
  if (i < 0) return;
  levelIndex.value = i;
  syncToLevel(animate);
};
defineExpose({ sync });

watch(
  () => props.modelValue,
  (value) => {
    const i = props.stops.indexOf(value);
    if (i < 0 || i === levelIndex.value || dragging.value) return; // 自己 emit 的回声
    levelIndex.value = i;
    syncToLevel(false);
  }
);
// 档位集合随模型变化：重新定位
watch(
  () => props.stops.join(','),
  () => {
    levelIndex.value = Math.max(0, props.stops.indexOf(props.modelValue));
    requestAnimationFrame(() => syncToLevel(false));
  }
);

let observer: ResizeObserver | null = null;
const measure = () => {
  trackWidth.value = trackRef.value?.clientWidth ?? 0;
  syncToLevel();
};
onMounted(() => {
  measure();
  if (typeof ResizeObserver !== 'undefined' && trackRef.value) {
    observer = new ResizeObserver(measure);
    observer.observe(trackRef.value);
  }
});
onBeforeUnmount(() => {
  cancelKnobAnim();
  observer?.disconnect();
});
</script>

<template>
  <div
    class="effort-slider"
    :class="{ 'is-dimmed': dimmed, 'is-disabled': disabled, 'live-drag': liveDrag }"
  >
    <!-- 顶部：静止 = 推理强度 + 当前档；拖动中 = 更高效 / 更智能 -->
    <div class="effort-header">
      <div class="header-state header-idle">
        <span class="header-title">{{ $t('quickdock.effortTitle') }}</span>
        <span class="header-value">{{ valueLabel }}</span>
      </div>
      <div class="header-state header-drag" aria-hidden="true">
        <span>{{ $t('quickdock.effortMoreEfficient') }}</span>
        <span>{{ $t('quickdock.effortMoreIntelligent') }}</span>
      </div>
    </div>

    <div class="slider-zone">
      <div
        ref="trackRef"
        class="track"
        role="slider"
        :tabindex="disabled ? -1 : 0"
        :aria-label="$t('quickdock.effortTitle')"
        :aria-valuemin="0"
        :aria-valuemax="stops.length - 1"
        :aria-valuenow="activeLevel"
        :aria-valuetext="valueLabel"
        :aria-disabled="disabled || undefined"
        @pointerdown="onPointerDown"
        @pointermove="onPointerMove"
        @pointerup="onPointerUp"
        @pointercancel="onPointerUp"
        @keydown="onKeydown"
      >
        <div ref="fillRef" class="fill-stack">
          <div
            v-for="(stop, i) in stops"
            :key="`lv-${stop}`"
            class="fill-layer"
            :class="[`lv-${stop}`, { on: i === activeLevel }]"
          ></div>
        </div>
        <div
          v-for="(stop, i) in stops"
          :key="`dot-${stop}`"
          class="dot"
          :ref="(el) => setDotRef(el as Element | null, i)"
          :style="{ left: posPct(i) }"
        ></div>
        <div ref="knobRef" class="knob"></div>
      </div>
      <div class="level-labels" aria-hidden="true">
        <span
          v-for="(stop, i) in stops"
          :key="`label-${stop}`"
          class="level-label"
          :class="{ active: i === activeLevel }"
          :style="labelStyle(i)"
          >{{ stopLabel(stop) }}</span
        >
      </div>
    </div>
  </div>
</template>

<style scoped>
.effort-slider {
  width: 100%;
  user-select: none;
  -webkit-user-select: none;
}

.effort-slider.is-disabled {
  opacity: 0.4;
}

.effort-slider.is-disabled .track {
  cursor: default;
}

/* ========== 顶部：两种状态文字叠放，淡入淡出切换 ========== */
.effort-header {
  position: relative;
  height: 28px;
}

.header-state {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  transition:
    opacity 180ms ease,
    transform 180ms ease;
}

.header-title {
  color: var(--text-secondary);
}

.header-value {
  color: var(--text-tertiary);
}

.header-drag {
  opacity: 0;
  transform: translateY(4px);
  pointer-events: none;
  color: var(--text-tertiary);
}

.effort-slider.live-drag .header-idle {
  opacity: 0;
  transform: translateY(-4px);
  pointer-events: none;
}

.effort-slider.live-drag .header-drag {
  opacity: 1;
  transform: translateY(0);
}

/* ========== 滑块：轨道 18、圆钮 22、档位点 5 ========== */
.slider-zone {
  position: relative;
  padding-top: 4px;
  transition:
    filter 250ms ease,
    opacity 250ms ease;
}

/* 默认推理：整体置灰并停掉动画 */
.effort-slider.is-dimmed .slider-zone {
  filter: grayscale(1);
  opacity: 0.5;
}

.effort-slider.is-dimmed .fill-layer,
.effort-slider.is-dimmed .fill-layer::after {
  animation: none;
}

.track {
  position: relative;
  height: 18px;
  border-radius: 9px;
  background: var(--effort-track-bg);
  cursor: pointer;
  touch-action: none;
  outline: none;
}

.track:focus-visible {
  outline: 2px solid var(--text-secondary);
  outline-offset: 3px;
}

/* 填充：各档叠放，当前档淡入、其余淡出；可见宽度由 JS 写 clip-path */
.fill-stack {
  position: absolute;
  inset: 0;
  border-radius: 9px;
  overflow: hidden;
}

.fill-layer {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 100%;
  opacity: 0;
  transition: opacity 350ms ease;
}

.fill-layer.on {
  opacity: 1;
}

.fill-layer.lv-none {
  background: transparent;
}

.fill-layer.lv-low {
  background: var(--effort-low);
}

.fill-layer.lv-medium {
  background: var(--effort-medium);
}

.fill-layer.lv-high,
.fill-layer.lv-on {
  background: var(--effort-high);
}

.fill-layer.lv-xhigh {
  background: var(--effort-xhigh);
}

/* xhigh：白光扫过 */
.fill-layer.lv-xhigh::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: -50%;
  width: 45%;
  background: linear-gradient(
    90deg,
    transparent 0%,
    var(--effort-xhigh-shine) 50%,
    transparent 100%
  );
  animation: shine-sweep 1.8s linear infinite;
}

@keyframes shine-sweep {
  from {
    left: -50%;
  }

  to {
    left: 130%;
  }
}

/* max：彩虹流动（200% 宽 + 位移整数个循环单元 → 无缝） */
.fill-layer.lv-max {
  width: 200%;
  background: repeating-linear-gradient(
    90deg,
    var(--effort-max-red) 0%,
    var(--effort-max-orange) 7.15%,
    var(--effort-max-yellow) 14.3%,
    var(--effort-max-green) 21.45%,
    var(--effort-max-cyan) 28.6%,
    var(--effort-max-purple) 35.75%,
    var(--effort-max-pink) 42.9%,
    var(--effort-max-red) 50%
  );
  animation: rainbow-flow 2.4s linear infinite;
}

@keyframes rainbow-flow {
  from {
    transform: translateX(-50%);
  }

  to {
    transform: translateX(0);
  }
}

.dot {
  position: absolute;
  top: 50%;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  transform: translate(-50%, -50%);
  background: var(--effort-dot-uncovered);
  transition: background-color 200ms ease;
  pointer-events: none;
}

.dot.covered {
  background: var(--effort-dot-covered);
}

/* 圆钮：left 恒 0，水平位置由 JS 写 transform */
.knob {
  position: absolute;
  left: 0;
  top: 50%;
  z-index: 2;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--effort-knob-bg);
  border: 1px solid var(--border-default);
  box-shadow: 0 1px 3px var(--effort-knob-shadow);
  transform: translate(-50%, -50%);
  cursor: grab;
}

.effort-slider.live-drag .knob {
  cursor: grabbing;
}

/* ========== 档位文字（位置见 labelStyle） ========== */
.level-labels {
  position: relative;
  height: 18px;
  margin-top: 6px;
}

.level-label {
  position: absolute;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-tertiary);
  white-space: nowrap;
  transition: color 200ms ease;
}

.level-label.active {
  color: var(--text-primary);
  font-weight: 500;
}

@media (prefers-reduced-motion: reduce) {
  .header-state,
  .slider-zone,
  .fill-layer,
  .dot,
  .level-label {
    transition: none;
  }

  .fill-layer.lv-xhigh::after,
  .fill-layer.lv-max {
    animation: none;
  }
}
</style>
