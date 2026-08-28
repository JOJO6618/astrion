<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import type { ReasoningEffort } from '../../stores/personalization';
import FancyCheck from '../common/FancyCheck.vue';

const LEVELS: ReasoningEffort[] = ['low', 'medium', 'high', 'xhigh', 'max'];
// 轨道两端档位圆钮中心 inset；比圆钮半径(23)小 3px，
// 让圆钮在两端时外凸 3px，完整盖住填充条端头（避免相切处露出杂边）
const EDGE = 20;

const props = defineProps<{
  modelValue: ReasoningEffort | null;
}>();
const emit = defineEmits<{
  (event: 'update:modelValue', value: ReasoningEffort | null): void;
}>();

const trackRef = ref<HTMLElement | null>(null);
const knobRef = ref<HTMLElement | null>(null);
const fillRef = ref<HTMLElement | null>(null);
// v-for 圆点元素收集（索引与 LEVELS 对齐）
const dotEls: (HTMLElement | null)[] = [];
const setDotRef = (el: Element | null, i: number) => {
  dotEls[i] = (el as HTMLElement | null) ?? null;
};
const trackWidth = ref(0);
// 当前档位（记忆值，勾选默认时也保留）
const levelIndex = ref(2);
const activeLevel = ref(2);
const dragging = ref(false);
const liveDrag = ref(false);
let downX = 0;
let animFrame = 0;
// 圆钮/填充的视觉像素位置（普通变量，非响应式）。所有视觉更新经
// applyVisualX 直接写 DOM：knob 用 transform、fill 用 clip-path（均为合成器
// 属性，不触发 layout/paint）、圆点只翻转 class，全程不经过 Vue 渲染。
// 此前用响应式 ref + left/width 驱动：rAF 每帧触发 Vue 重渲染并 reflow，
// 是滑动卡顿、经过档位点时「顿一下」的根因。
let visualX = 0;

const isDefault = computed(() => props.modelValue === null);

const posRatio = (i: number) => i / (LEVELS.length - 1);
const posPct = (i: number) => `calc(${EDGE}px + (100% - ${EDGE * 2}px) * ${posRatio(i)})`;
const posPx = (i: number) => EDGE + (trackWidth.value - EDGE * 2) * posRatio(i);
const pxToLevel = (x: number) => {
  const w = trackWidth.value;
  if (w <= EDGE * 2) return 0;
  const ratio = (x - EDGE) / (w - EDGE * 2);
  return Math.max(0, Math.min(LEVELS.length - 1, Math.round(ratio * (LEVELS.length - 1))));
};

const clampEventX = (clientX: number) => {
  const el = trackRef.value;
  if (!el) return EDGE;
  const rect = el.getBoundingClientRect();
  const x = clientX - rect.left;
  return Math.max(EDGE, Math.min(rect.width - EDGE, x));
};

const KNOB_ANIM_MS = 200;
// 与 CSS ease 接近的 easeInOutQuad
const easeInOut = (t: number) => (t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2);

// 把视觉位置直接写入 DOM（knob 水平位移走 translate、fill 可见宽度走
// clip-path inset round 17px 保圆角、圆点 covered 变色时机与圆钮视觉位置
// 严格同步 —— 往低调档时被变色的点会被圆钮遮住渐变过程，不暴露闪烁）。
// 未测量（trackWidth=0 的首帧）时退回档位百分比表达式，与测量后像素值等价。
const applyVisualX = (x: number) => {
  const measured = trackWidth.value > EDGE * 2;
  const expr = measured ? `${x}px` : posPct(levelIndex.value);
  const knob = knobRef.value;
  if (knob) {
    knob.style.transform = `translate(calc(${expr} - 50%), -50%)`;
  }
  const fill = fillRef.value;
  if (fill) {
    fill.style.clipPath = `inset(0 calc(100% - (${expr})) 0 0 round 17px)`;
  }
  for (let i = 0; i < LEVELS.length; i++) {
    const el = dotEls[i];
    if (!el) continue;
    const covered = measured ? posPx(i) <= x : i <= levelIndex.value;
    el.classList.toggle('covered', covered);
  }
};

const cancelKnobAnim = () => {
  if (animFrame) {
    cancelAnimationFrame(animFrame);
    animFrame = 0;
  }
};

const setKnobInstant = (x: number) => {
  cancelKnobAnim();
  visualX = x;
  applyVisualX(x);
};

// 从当前视觉位置插值滑动到目标（进行中的动画被接续，不会跳变）
const animateKnobTo = (target: number) => {
  const start = visualX;
  if (start === target) return;
  cancelKnobAnim();
  const t0 = performance.now();
  const step = (now: number) => {
    const p = Math.min(1, (now - t0) / KNOB_ANIM_MS);
    visualX = start + (target - start) * easeInOut(p);
    applyVisualX(visualX);
    if (p < 1) {
      animFrame = requestAnimationFrame(step);
    } else {
      animFrame = 0;
    }
  };
  animFrame = requestAnimationFrame(step);
};

// animate=true：点击/吸附时平滑滑动；false：挂载/resize/外部变更时瞬间就位
const syncToLevel = (animate = false) => {
  const target = posPx(levelIndex.value);
  activeLevel.value = levelIndex.value;
  if (animate && trackWidth.value > EDGE * 2) {
    animateKnobTo(target);
  } else {
    setKnobInstant(target);
  }
};

const commitLevel = () => {
  emit('update:modelValue', LEVELS[levelIndex.value]);
};

const onPointerDown = (e: PointerEvent) => {
  dragging.value = true;
  liveDrag.value = false;
  downX = e.clientX;
  trackRef.value?.setPointerCapture(e.pointerId);
  // 点击（含置灰状态）：解除默认并平滑滑到目标档（JS 插值驱动）
  levelIndex.value = pxToLevel(clampEventX(e.clientX));
  commitLevel();
  syncToLevel(true);
};

const onPointerMove = (e: PointerEvent) => {
  if (!dragging.value) return;
  if (!liveDrag.value && Math.abs(e.clientX - downX) > 3) liveDrag.value = true;
  if (liveDrag.value) {
    // 真实拖动：无动画连续跟随
    const x = clampEventX(e.clientX);
    cancelKnobAnim();
    visualX = x;
    applyVisualX(x);
    activeLevel.value = pxToLevel(x);
  }
};

const onPointerUp = (e: PointerEvent) => {
  if (!dragging.value) return;
  dragging.value = false;
  if (liveDrag.value) {
    // 拖动结束：吸附最近档。插值从松手点连续位置开始滑向档位，
    // 天然无瞬移（无需旧实现的分帧抑制）
    liveDrag.value = false;
    levelIndex.value = pxToLevel(clampEventX(e.clientX));
    commitLevel();
    syncToLevel(true);
    return;
  }
  syncToLevel(true);
};

const toggleDefault = () => {
  emit('update:modelValue', isDefault.value ? LEVELS[levelIndex.value] : null);
};

const measure = () => {
  trackWidth.value = trackRef.value?.clientWidth ?? 0;
  syncToLevel();
};

watch(
  () => props.modelValue,
  (value) => {
    if (value === null) return; // 勾选默认：保留档位记忆，圆钮不动
    const i = LEVELS.indexOf(value);
    if (i < 0 || i === levelIndex.value) return; // 自己 emit 的回声：不打扰进行中的动画
    // 外部变更（如父组件异步修正）：瞬间同步
    levelIndex.value = i;
    syncToLevel(false);
  },
  // immediate：挂载时立即从 modelValue 同步档位，
  // 否则弹窗每次打开都停在初始值 high
  { immediate: true }
);

onMounted(() => {
  measure();
  window.addEventListener('resize', measure);
});
onBeforeUnmount(() => {
  cancelKnobAnim();
  window.removeEventListener('resize', measure);
});
</script>

<template>
  <div
    class="effort-slider"
    :class="{ 'is-default': isDefault, 'live-drag': liveDrag }"
  >
    <!-- 顶部：非拖动 = 推理强度 + 默认；拖动中 = 更高效 / 更智能 -->
    <div class="effort-header">
      <div class="header-state header-idle">
        <span class="header-title">{{ $t('quickdock.effortTitle') }}</span>
        <span class="default-toggle" :class="{ checked: isDefault }" @click.stop="toggleDefault">
          <span class="default-label">{{ $t('quickdock.effortDefault') }}</span>
          <FancyCheck :checked="isDefault" :size="14" />
        </span>
      </div>
      <div class="header-state header-drag">
        <span>{{ $t('quickdock.effortMoreEfficient') }}</span>
        <span>{{ $t('quickdock.effortMoreIntelligent') }}</span>
      </div>
    </div>

    <!-- 滑块 -->
    <div class="slider-zone">
      <div
        ref="trackRef"
        class="track"
        @pointerdown="onPointerDown"
        @pointermove="onPointerMove"
        @pointerup="onPointerUp"
        @pointercancel="onPointerUp"
      >
        <div ref="fillRef" class="fill-stack">
          <div
            v-for="(_, i) in LEVELS"
            :key="`lv-${i}`"
            class="fill-layer"
            :class="[`lv-${i}`, { on: i === activeLevel }]"
          ></div>
        </div>
        <div
          v-for="(_, i) in LEVELS"
          :key="`dot-${i}`"
          class="dot"
          :ref="(el) => setDotRef(el as Element | null, i)"
          :style="{ left: posPct(i) }"
        ></div>
        <div ref="knobRef" class="knob"></div>
      </div>
      <div class="level-labels">
        <span
          v-for="(name, i) in LEVELS"
          :key="`label-${i}`"
          class="level-label"
          :class="{ active: i === activeLevel }"
          :style="{ left: posPct(i) }"
          >{{ name }}</span
        >
      </div>
    </div>
  </div>
</template>

<style scoped>
.effort-slider {
  width: 100%;
  padding: 4px 2px 0;
  user-select: none;
  -webkit-user-select: none;
}

/* ========== 顶部：两种状态文字叠在一起，淡入淡出切换 ========== */
.effort-header {
  position: relative;
  height: 24px;
  margin-bottom: 14px;
}
.header-state {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  transition:
    opacity 0.18s ease,
    transform 0.18s ease;
}
.header-idle {
  opacity: 1;
}
.header-drag {
  opacity: 0;
  transform: translateY(4px);
  pointer-events: none;
  color: var(--text-secondary);
  font-size: 14px;
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

.header-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

/* ========== 「默认」打勾框：个人空间 fancy-check 缩小版 ========== */
.default-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}
.default-label {
  font-size: 12px;
  color: var(--text-secondary);
}

/* ========== 滑块区域 ========== */
.slider-zone {
  position: relative;
  transition:
    filter 0.25s ease,
    opacity 0.25s ease;
}

/* 默认勾选时：整体置灰，且停掉所有动画 */
.effort-slider.is-default .slider-zone {
  filter: grayscale(1) brightness(0.72);
  opacity: 0.55;
}
.effort-slider.is-default .fill-layer,
.effort-slider.is-default .fill-layer::after {
  animation: none;
}

.track {
  position: relative;
  height: 34px;
  border-radius: 17px;
  background: var(--effort-track-bg);
  cursor: pointer;
  touch-action: none;
}

/* 填充：5 层叠放，当前档淡入、其余淡出 —— 任何颜色切换都是平滑 crossfade。
   宽度恒 100%，可见宽度由 JS 写入 clip-path 控制（合成器属性，无 reflow） */
.fill-stack {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 100%;
  border-radius: 17px;
  overflow: hidden;
}
.fill-layer {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 100%;
  opacity: 0;
  transition: opacity 0.35s ease;
}
.fill-layer.on {
  opacity: 1;
}
.fill-layer.lv-0 {
  background: var(--effort-low);
}
.fill-layer.lv-1 {
  background: var(--effort-medium);
}
.fill-layer.lv-2 {
  background: var(--effort-high);
}
.fill-layer.lv-3 {
  background: var(--effort-xhigh);
}

/* xhigh：白光扫过（90deg 整列渐变无硬边；位移动画两端完全出画后回绕 → 无缝） */
.fill-layer.lv-3::after {
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

/* max：彩虹流动（200% 宽 + 位移整数个循环单元 → 真无缝，方向从左到右） */
.fill-layer.lv-4 {
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

/* 圆钮与填充位置由 JS 直写 DOM 驱动（见 script applyVisualX）：
   knob 走 transform、fill 走 clip-path，均为合成器属性，滑动零 reflow */

/* 档位圆点 */
.dot {
  position: absolute;
  top: 50%;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  transform: translate(-50%, -50%);
  background: var(--effort-dot-uncovered);
  transition: background 0.2s ease;
  pointer-events: none;
}
.dot.covered {
  background: var(--effort-dot-covered);
}

/* 拖柄：left 恒 0，水平位置由 JS 写入 transform translate */
.knob {
  position: absolute;
  left: 0;
  top: 50%;
  width: 46px;
  height: 46px;
  border-radius: 50%;
  background: var(--effort-knob-bg);
  border: 1px solid var(--border-default);
  transform: translate(-50%, -50%);
  box-shadow: 0 1px 4px var(--effort-knob-shadow);
  cursor: grab;
  z-index: 2;
}
.effort-slider.live-drag .knob {
  cursor: grabbing;
}

/* ========== 档位文字 ========== */
.level-labels {
  position: relative;
  height: 18px;
  margin-top: 10px;
}
.level-label {
  position: absolute;
  transform: translateX(-50%);
  font-size: 11px;
  color: var(--text-secondary);
  transition:
    color 0.2s ease,
    font-weight 0.2s ease;
  white-space: nowrap;
}
.level-label.active {
  color: var(--text-primary);
  font-weight: 600;
}
</style>
