<template>
  <svg
    ref="svgRef"
    class="status-avatar"
    :class="`status-avatar--${props.mode || 'idle'}`"
    :style="{ width: size + 'px', height: size + 'px' }"
    viewBox="0 0 200 200"
    aria-hidden="true"
    @click="handleAvatarClick"
  >
    <!-- flat-top 圆角正六边形背景（与对话区背景同色） -->
    <path
      v-if="props.hexBackground"
      class="sa-bg"
      d="M 175.959,107.000 Q 180.000,100.000 175.959,93.000 L 144.041,37.718 Q 140.000,30.718 131.917,30.718 L 68.083,30.718 Q 60.000,30.718 55.959,37.718 L 24.041,93.000 Q 20.000,100.000 24.041,107.000 L 55.959,162.282 Q 60.000,169.282 68.083,169.282 L 131.917,169.282 Q 140.000,169.282 144.041,162.282 Z"
    />

    <!-- flat-top 圆角正六边形外框 -->
    <path
      class="sa-frame"
      d="M 175.959,107.000 Q 180.000,100.000 175.959,93.000 L 144.041,37.718 Q 140.000,30.718 131.917,30.718 L 68.083,30.718 Q 60.000,30.718 55.959,37.718 L 24.041,93.000 Q 20.000,100.000 24.041,107.000 L 55.959,162.282 Q 60.000,169.282 68.083,169.282 L 131.917,169.282 Q 140.000,169.282 144.041,162.282 Z"
    />

    <defs>
      <clipPath :id="clipId">
        <path d="M 180,100 L 140,30.718 L 60,30.718 L 20,100 L 60,169.282 L 140,169.282 Z" />
      </clipPath>
    </defs>

    <g class="sa-face" ref="faceRef">
      <!-- 眼睛组：idle 眼睛 与 work 提示符 互相 morph -->
      <g class="sa-fc sa-shared" :class="{ hidden: !sharedVisible }">
        <path
          class="sa-eye"
          ref="eyeLeftRef"
          d="M 0,-11 Q 0,-5.5 0,0 Q 0,5.5 0,11 Q 0,5.5 0,0 Q 0,-5.5 0,-11"
          transform="translate(84, 96)"
        />
        <path
          class="sa-eye"
          ref="eyeRightRef"
          d="M 0,-11 Q 0,-5.5 0,0 Q 0,5.5 0,11 Q 0,5.5 0,0 Q 0,-5.5 0,-11"
          transform="translate(116, 96)"
        />
      </g>

      <!-- 思考：三个大点 -->
      <g class="sa-fc sa-icon" :class="{ active: activeFaceKey === 'think' }">
        <circle class="sa-think-dot" cx="80" cy="100" r="7" />
        <circle class="sa-think-dot" cx="100" cy="100" r="7" />
        <circle class="sa-think-dot" cx="120" cy="100" r="7" />
      </g>

      <!-- 子智能体：六边形分裂-聚合 -->
      <g class="sa-fc sa-icon" :class="{ active: activeFaceKey === 'subagent' }" :clip-path="`url(#${clipId})`">
        <g class="sa-sub-scene">
          <line class="sa-sub-ray" x1="180" y1="100" x2="140" y2="100" />
          <line class="sa-sub-ray" x1="140" y1="30.718" x2="120" y2="65.359" />
          <line class="sa-sub-ray" x1="60" y1="30.718" x2="80" y2="65.359" />
          <line class="sa-sub-ray" x1="20" y1="100" x2="60" y2="100" />
          <line class="sa-sub-ray" x1="60" y1="169.282" x2="80" y2="134.641" />
          <line class="sa-sub-ray" x1="140" y1="169.282" x2="120" y2="134.641" />
          <path class="sa-sub-hex" d="M 137.9795,103.5 Q 140,100 137.9795,96.5 L 122.0205,68.859 Q 120,65.359 115.9585,65.359 L 84.0415,65.359 Q 80,65.359 77.9795,68.859 L 62.0205,96.5 Q 60,100 62.0205,103.5 L 77.9795,131.141 Q 80,134.641 84.0415,134.641 L 115.9585,134.641 Q 120,134.641 122.0205,131.141 Z" />
          <g class="sa-sub-eyes">
            <line class="sa-sub-eye" x1="0" y1="0" x2="11" y2="0" style="transform: translate(92px, 92px) rotate(90deg);" />
            <line class="sa-sub-eye" x1="0" y1="0" x2="11" y2="0" style="transform: translate(108px, 92px) rotate(90deg);" />
          </g>
        </g>
      </g>

      <!-- 注册表工具 face：动态渲染 -->
      <g
        v-for="tool in TOOLS"
        :key="tool.key"
        class="sa-fc sa-icon"
        :class="{ active: activeFaceKey === tool.key }"
        v-html="buildFaceInner(tool)"
      ></g>
    </g>
  </svg>
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch } from 'vue';

// mode: 'idle' | 'work' | 'think' | 'tool'
const props = withDefaults(
  defineProps<{
    mode?: string;
    toolKeys?: string[];
    tracking?: boolean;
    size?: number;
    hexBackground?: boolean;
  }>(),
  {
    mode: 'idle',
    toolKeys: () => [],
    tracking: false,
    size: 30,
    hexBackground: false
  }
);
const emit = defineEmits<{
  (event: 'poke'): void;
}>();

// 每个实例独立的 clipPath id，避免多实例冲突
const clipId = `sa-hex-clip-${Math.random().toString(36).slice(2, 9)}`;

const svgRef = ref<SVGSVGElement | null>(null);
const faceRef = ref<SVGGElement | null>(null);
const eyeLeftRef = ref<SVGPathElement | null>(null);
const eyeRightRef = ref<SVGPathElement | null>(null);

// 当前激活的独立 face（think / 工具 key）；为 null 表示显示 shared（idle/work）
const activeFaceKey = ref<string | null>(null);
const sharedVisible = ref(true);

// ---- 工具 face 注册表（与 demo 一致）----
interface ToolDef {
  key: string;
  motion: string;
  scale?: number;
  svg?: string;
  raw?: string;
}
const TOOLS: ToolDef[] = [
  { key: 'search', motion: 'wiggle', svg: '<path class="sa-inner" d="m21 21-4.34-4.34"/><circle class="sa-inner" cx="11" cy="11" r="8"/>' },
  { key: 'webpage', motion: 'bob', svg: '<circle class="sa-inner" cx="12" cy="12" r="10"/><path class="sa-inner" d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><line class="sa-inner" x1="2" x2="22" y1="12" y2="12"/>' },
  { key: 'write', motion: 'wiggle', svg: '<path class="sa-inner" d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path class="sa-inner" d="m15 5 4 4"/>' },
  { key: 'read', motion: 'bob', svg: '<path class="sa-inner" d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H19a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H6.5a1 1 0 0 1 0-5H20"/>' },
  { key: 'save', motion: 'bob', svg: '<path class="sa-inner" d="M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path class="sa-inner" d="M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7"/><path class="sa-inner" d="M7 3v4a1 1 0 0 0 1 1h7"/>' },
  { key: 'camera', motion: 'bob', svg: '<path class="sa-inner" d="M13.997 4a2 2 0 0 1 1.76 1.05l.486.9A2 2 0 0 0 18.003 7H20a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2h1.997a2 2 0 0 0 1.759-1.048l.489-.904A2 2 0 0 1 10.004 4z"/><circle class="sa-inner" cx="12" cy="13" r="3"/>' },
  { key: 'monitor', motion: 'bob', svg: '<rect class="sa-inner" width="20" height="14" x="2" y="3" rx="2"/><line class="sa-inner" x1="8" x2="16" y1="21" y2="21"/><line class="sa-inner" x1="12" x2="12" y1="17" y2="21"/>' },
  { key: 'keyboard', motion: 'bob', svg: '<path class="sa-inner" d="M10 8h.01M12 12h.01M14 8h.01M16 12h.01M18 8h.01M6 8h.01M7 16h10m-9-4h.01"/><rect class="sa-inner" width="20" height="16" x="2" y="4" rx="2"/>' },
  { key: 'clipboard', motion: 'bob', svg: '<rect class="sa-inner" width="8" height="4" x="8" y="2" rx="1" ry="1"/><path class="sa-inner" d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>' },
  { key: 'command', motion: 'bob', svg: '<path class="sa-inner" d="M12 19h8"/><path class="sa-inner" d="m4 17 6-6-6-6"/>' },
  { key: 'brain', motion: 'bob', svg: '<path class="sa-inner" d="M12 18V5"/><path class="sa-inner" d="M15 13a4.17 4.17 0 0 1-3-4 4.17 4.17 0 0 1-3 4"/><path class="sa-inner" d="M17.598 6.5A3 3 0 1 0 12 5a3 3 0 1 0-5.598 1.5"/><path class="sa-inner" d="M17.997 5.125a4 4 0 0 1 2.526 5.77"/><path class="sa-inner" d="M18 18a4 4 0 0 0 2-7.464"/><path class="sa-inner" d="M19.967 17.483A4 4 0 1 1 12 18a4 4 0 1 1-7.967-.517"/><path class="sa-inner" d="M6 18a4 4 0 0 1-2-7.464"/><path class="sa-inner" d="M6.003 5.125a4 4 0 0 0-2.526 5.77"/>' },
  { key: 'notebook', motion: 'bob', svg: '<path class="sa-inner" d="M2 6h4m-4 4h4m-4 4h4m-4 4h4"/><rect class="sa-inner" width="16" height="20" x="4" y="2" rx="2"/><path class="sa-inner" d="M16 2v20"/>' },
  { key: 'note', motion: 'bob', svg: '<path class="sa-inner" d="M21 9a2.4 2.4 0 0 0-.706-1.706l-3.588-3.588A2.4 2.4 0 0 0 15 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2z"/><path class="sa-inner" d="M15 3v5a1 1 0 0 0 1 1h5"/>' },
  { key: 'check', motion: 'bob', svg: '<path class="sa-inner" d="M20 6 9 17l-5-5"/>' },
  { key: 'skill', motion: 'bob', svg: '<path class="sa-inner" d="M11.017 2.814a1 1 0 0 1 1.966 0l1.051 5.558a2 2 0 0 0 1.594 1.594l5.558 1.051a1 1 0 0 1 0 1.966l-5.558 1.051a2 2 0 0 0-1.594 1.594l-1.051 5.558a1 1 0 0 1-1.966 0l-1.051-5.558a2 2 0 0 0-1.594-1.594l-5.558-1.051a1 1 0 0 1 0-1.966l5.558-1.051a2 2 0 0 0 1.594-1.594z"/><path class="sa-inner" d="M20 2v4"/><path class="sa-inner" d="M22 4h-4"/><circle class="sa-inner" cx="4" cy="20" r="2"/>' },
  { key: 'persona', motion: 'bob', svg: '<path class="sa-inner" d="M11.5 15H7a4 4 0 0 0-4 4v2m18.378-4.374a1 1 0 0 0-3.004-3.004l-4.01 4.012a2 2 0 0 0-.506.854l-.837 2.87a.5.5 0 0 0 .62.62l2.87-.837a2 2 0 0 0 .854-.506z"/><circle class="sa-inner" cx="10" cy="7" r="4"/>' },
  { key: 'mcp', motion: 'bob', raw: '<g transform="translate(100,100) scale(0.30) translate(-90,-90)"><path class="sa-inner" style="stroke-width:16.8" d="M18 84.8528L85.8822 16.9706C95.2548 7.59798 110.451 7.59798 119.823 16.9706C129.196 26.3431 129.196 41.5391 119.823 50.9117L68.5581 102.177"/><path class="sa-inner" style="stroke-width:16.8" d="M69.2652 101.47L119.823 50.9117C129.196 41.5391 144.392 41.5391 153.765 50.9117L154.118 51.2652C163.491 60.6378 163.491 75.8338 154.118 85.2063L92.7248 146.6C89.6006 149.724 89.6006 154.789 92.7248 157.913L105.331 170.52"/><path class="sa-inner" style="stroke-width:16.8" d="M102.853 33.9411L52.6482 84.1457C43.2756 93.5183 43.2756 108.714 52.6482 118.087C62.0208 127.459 77.2167 127.459 86.5893 118.087L136.794 67.8822"/></g>' },
  { key: 'ask', motion: 'bob', scale: 3.7, svg: '<path class="sa-inner" style="stroke-width:1.6" d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><path class="sa-inner" style="stroke-width:1.6" d="M12 17h.01"/>' },
  { key: 'sleep', motion: 'special', raw: '<g class="sa-z z3"><text x="130" y="94" text-anchor="middle" font-size="34" font-weight="600">Z</text></g><g class="sa-z z2"><text x="100" y="109" text-anchor="middle" font-size="26" font-weight="600">Z</text></g><g class="sa-z z1"><text x="70" y="123" text-anchor="middle" font-size="18" font-weight="600">Z</text></g>' }
];

function buildFaceInner(tool: ToolDef): string {
  if (tool.raw) {
    return tool.motion === 'bob' ? `<g class="sa-bob">${tool.raw}</g>` : tool.raw;
  }
  const s = tool.scale || 2.75;
  const scaled = `<g transform="translate(100, 100) scale(${s}) translate(-12, -12)">${tool.svg}</g>`;
  const motionClass = tool.motion === 'wiggle' ? 'sa-wiggle' : 'sa-bob';
  return `<g class="${motionClass}">${scaled}</g>`;
}

// ---- 眼睛 morph（idle 竖线 ↔ work 提示符），照搬 demo ----
const EYE_SHAPES: Record<string, string> = {
  idle: 'M 0,-11 Q 0,-5.5 0,0 Q 0,5.5 0,11 Q 0,5.5 0,0 Q 0,-5.5 0,-11',
  blink: 'M 0,0 Q 0,0 0,0 Q 0,0 0,0 Q 0,0 0,0 Q 0,0 0,0',
  nervousLeft: 'M -10,-12 Q -2,-6 6,0 Q -2,6 -10,12 Q -2,6 6,0 Q -2,-6 -10,-12',
  nervousRight: 'M 10,-12 Q 2,-6 -6,0 Q 2,6 10,12 Q 2,6 -6,0 Q 2,-6 10,-12',
  work: 'M 0,0 Q 8.5,0 17,0 Q 25.5,0 34,0 Q 25.5,0 17,0 Q 8.5,0 0,0'
};
const EYE_LEFT_ORIGIN = { x: 84, y: 96 };
const EYE_RIGHT_ORIGIN = { x: 116, y: 96 };
const NERVOUS_LEFT_ORIGIN = { x: 82, y: 96 };
const NERVOUS_RIGHT_ORIGIN = { x: 118, y: 96 };
const WORK_ORIGIN = { x: 117, y: 100 };
const WORK_ROTATION_LEFT = 150;
const WORK_ROTATION_RIGHT = 210;
const DEFAULT_EYE_TRANSITION = 'transform 0.55s cubic-bezier(0.22, 1, 0.36, 1)';
const NERVOUS_EYE_TRANSITION = 'transform 0.22s cubic-bezier(0.65, 0, 0.35, 1)';

let currentLeftEyeShape = 'idle';
let currentRightEyeShape = 'idle';

function cubicBezier(p1x: number, p1y: number, p2x: number, p2y: number) {
  const cx = 3 * p1x;
  const bx = 3 * (p2x - p1x) - cx;
  const ax = 1 - cx - bx;
  const cy = 3 * p1y;
  const by = 3 * (p2y - p1y) - cy;
  const ay = 1 - cy - by;
  const sampleCurveX = (t: number) => ((ax * t + bx) * t + cx) * t;
  return (t: number) => {
    let x = t;
    for (let i = 0; i < 8; i++) {
      const x2 = sampleCurveX(x) - t;
      if (Math.abs(x2) < 1e-6) return ((ay * x + by) * x + cy) * x;
      const d2 = (3 * ax * x + 2 * bx) * x + cx;
      if (Math.abs(d2) < 1e-6) break;
      x -= x2 / d2;
    }
    return ((ay * x + by) * x + cy) * x;
  };
}
const easeOut = cubicBezier(0.22, 1, 0.36, 1);
const easeInOut = cubicBezier(0.65, 0, 0.35, 1);

function parsePathNumbers(d: string): number[] {
  return (d.match(/[-\d.]+/g) || []).map(Number);
}
function buildPath(n: number[]): string {
  return `M ${n[0]},${n[1]} Q ${n[2]},${n[3]} ${n[4]},${n[5]} Q ${n[6]},${n[7]} ${n[8]},${n[9]} Q ${n[10]},${n[11]} ${n[12]},${n[13]} Q ${n[14]},${n[15]} ${n[16]},${n[17]}`;
}

const morphRaf = new Map<SVGPathElement, number>();
function morphShape(
  eye: SVGPathElement | null,
  fromShape: string,
  toShape: string,
  duration = 550,
  easing = easeOut
) {
  if (!eye) return;
  eye.setAttribute('d', EYE_SHAPES[fromShape]);
  const fromNums = parsePathNumbers(EYE_SHAPES[fromShape]);
  const toNums = parsePathNumbers(EYE_SHAPES[toShape]);
  const startTime = performance.now();
  const prev = morphRaf.get(eye);
  if (prev) cancelAnimationFrame(prev);
  const step = (now: number) => {
    const progress = Math.min((now - startTime) / duration, 1);
    const eased = easing(progress);
    const cur = fromNums.map((v, i) => v + (toNums[i] - v) * eased);
    eye.setAttribute('d', buildPath(cur));
    if (progress < 1) {
      morphRaf.set(eye, requestAnimationFrame(step));
    } else {
      morphRaf.delete(eye);
    }
  };
  morphRaf.set(eye, requestAnimationFrame(step));
}

function applyWorkTransform() {
  if (eyeLeftRef.value) {
    eyeLeftRef.value.style.transition = DEFAULT_EYE_TRANSITION;
    eyeLeftRef.value.style.transform = `translate(${WORK_ORIGIN.x}px, ${WORK_ORIGIN.y}px) rotate(${WORK_ROTATION_LEFT}deg)`;
  }
  if (eyeRightRef.value) {
    eyeRightRef.value.style.transition = DEFAULT_EYE_TRANSITION;
    eyeRightRef.value.style.transform = `translate(${WORK_ORIGIN.x}px, ${WORK_ORIGIN.y}px) rotate(${WORK_ROTATION_RIGHT}deg)`;
  }
}
function applyIdleTransform(animate = true) {
  if (eyeLeftRef.value) {
    if (!animate) eyeLeftRef.value.style.transition = 'none';
    eyeLeftRef.value.style.transform = `translate(${EYE_LEFT_ORIGIN.x}px, ${EYE_LEFT_ORIGIN.y}px)`;
    if (!animate) {
      void eyeLeftRef.value.offsetWidth;
      eyeLeftRef.value.style.transition = '';
    }
  }
  if (eyeRightRef.value) {
    if (!animate) eyeRightRef.value.style.transition = 'none';
    eyeRightRef.value.style.transform = `translate(${EYE_RIGHT_ORIGIN.x}px, ${EYE_RIGHT_ORIGIN.y}px)`;
    if (!animate) {
      void eyeRightRef.value.offsetWidth;
      eyeRightRef.value.style.transition = '';
    }
  }
}
function applyNervousTransform() {
  if (eyeLeftRef.value) {
    eyeLeftRef.value.style.transition = NERVOUS_EYE_TRANSITION;
    eyeLeftRef.value.style.transform = `translate(${NERVOUS_LEFT_ORIGIN.x}px, ${NERVOUS_LEFT_ORIGIN.y}px)`;
  }
  if (eyeRightRef.value) {
    eyeRightRef.value.style.transition = NERVOUS_EYE_TRANSITION;
    eyeRightRef.value.style.transform = `translate(${NERVOUS_RIGHT_ORIGIN.x}px, ${NERVOUS_RIGHT_ORIGIN.y}px)`;
  }
}

// ---- 眼睛鼠标追踪 ----
let mouseX = 0;
let mouseY = 0;
let eyeOffsetX = 0;
let eyeOffsetY = 0;
let trackRaf: number | null = null;

function onMouseMove(e: MouseEvent) {
  mouseX = e.clientX;
  mouseY = e.clientY;
}
function stopTracking() {
  if (trackRaf) cancelAnimationFrame(trackRaf);
  trackRaf = null;
  if (faceRef.value) faceRef.value.style.transform = 'translate(0px, 0px)';
}
function trackEyes() {
  const mode = internalMode.value || props.mode;
  if (!(props.tracking && (mode === 'idle' || mode === 'nervous')) || !svgRef.value) {
    stopTracking();
    return;
  }
  const rect = svgRef.value.getBoundingClientRect();
  const centerX = rect.left + rect.width / 2;
  const centerY = rect.top + rect.height / 2;
  const dx = mouseX - centerX;
  const dy = mouseY - centerY;
  const distance = Math.sqrt(dx * dx + dy * dy);
  const angle = Math.atan2(dy, dx);
  const factor = Math.min(distance / 180, 1);
  const targetX = Math.cos(angle) * 10 * factor;
  const targetY = Math.sin(angle) * 10 * factor;
  eyeOffsetX += (targetX - eyeOffsetX) * 0.15;
  eyeOffsetY += (targetY - eyeOffsetY) * 0.15;
  if (faceRef.value) faceRef.value.style.transform = `translate(${eyeOffsetX}px, ${eyeOffsetY}px)`;
  trackRaf = requestAnimationFrame(trackEyes);
}

// ---- 工具 face 切换 ----
let faceSwitchTimer: number | null = null;
let blinkTimer: number | null = null;
let nervousTimer: number | null = null;
let isBlinking = false;
const internalMode = ref<string | null>(null);
function stopFaceSwitchTimer() {
  if (faceSwitchTimer) clearTimeout(faceSwitchTimer);
  faceSwitchTimer = null;
}
function stopAutoBlink() {
  if (blinkTimer) clearTimeout(blinkTimer);
  blinkTimer = null;
}
function stopNervousTimer() {
  if (nervousTimer) clearTimeout(nervousTimer);
  nervousTimer = null;
}
function wait(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}
function scheduleAutoBlink() {
  stopAutoBlink();
  if (props.mode !== 'idle' || internalMode.value) return;
  const delay = 2800 + Math.random() * 3600;
  blinkTimer = window.setTimeout(async () => {
    await blinkEyes();
    scheduleAutoBlink();
  }, delay);
}
async function blinkEyes() {
  if (props.mode !== 'idle' || internalMode.value || isBlinking) return;
  isBlinking = true;
  const blinkCount = Math.random() < 0.5 ? 1 : 2;
  for (let i = 0; i < blinkCount; i++) {
    morphShape(eyeLeftRef.value, 'idle', 'blink', 150, easeInOut);
    morphShape(eyeRightRef.value, 'idle', 'blink', 150, easeInOut);
    await wait(120);
    if (props.mode !== 'idle' || internalMode.value) {
      isBlinking = false;
      return;
    }
    await wait(45);
    morphShape(eyeLeftRef.value, 'blink', 'idle', 210, easeInOut);
    morphShape(eyeRightRef.value, 'blink', 'idle', 210, easeInOut);
    await wait(i === blinkCount - 1 ? 200 : 250);
  }
  isBlinking = false;
}
function triggerNervous() {
  if (props.mode !== 'idle') return;
  internalMode.value = 'nervous';
  stopAutoBlink();
  stopNervousTimer();
  stopTracking();
  stopFaceSwitchTimer();
  activeFaceKey.value = null;
  sharedVisible.value = true;
  applyNervousTransform();
  morphShape(eyeLeftRef.value, currentLeftEyeShape, 'nervousLeft', 220, easeInOut);
  morphShape(eyeRightRef.value, currentRightEyeShape, 'nervousRight', 220, easeInOut);
  currentLeftEyeShape = 'nervousLeft';
  currentRightEyeShape = 'nervousRight';
  if (props.tracking) trackEyes();
  nervousTimer = window.setTimeout(() => {
    if (internalMode.value === 'nervous') {
      internalMode.value = null;
      // 由 watcher 统一调用 applyState，避免与显式调用叠加导致动画被重置
    }
  }, 500);
}
function handleAvatarClick() {
  if (props.mode !== 'idle') return;
  emit('poke');
  triggerNervous();
}
function setIconFace(key: string, animated = true) {
  const fromShared = sharedVisible.value && !activeFaceKey.value;
  sharedVisible.value = false;
  if (!animated || activeFaceKey.value === key) {
    activeFaceKey.value = key;
    return;
  }
  if (fromShared) {
    activeFaceKey.value = null;
    stopFaceSwitchTimer();
    faceSwitchTimer = window.setTimeout(() => {
      activeFaceKey.value = key;
      faceSwitchTimer = null;
    }, 160);
    return;
  }
  if (!activeFaceKey.value) {
    activeFaceKey.value = key;
    return;
  }
  activeFaceKey.value = null;
  stopFaceSwitchTimer();
  faceSwitchTimer = window.setTimeout(() => {
    activeFaceKey.value = key;
    faceSwitchTimer = null;
  }, 160);
}
// ---- 状态应用 ----
function applyState() {
  if (props.mode !== 'idle' && internalMode.value) {
    internalMode.value = null;
    stopNervousTimer();
  }
  const mode = internalMode.value || props.mode;
  if (mode !== 'idle') stopAutoBlink();
  if (mode === 'tool') {
    stopTracking();
    const keys = (props.toolKeys || []).filter(Boolean);
    // 并行工具时固定显示第一个正在执行的工具；
    // 完成一个后 toolKeys 变化，自动切到下一个
    setIconFace(keys.length ? keys[0] : 'command');
    return;
  }
  stopFaceSwitchTimer();
  if (mode === 'think') {
    stopTracking();
    setIconFace('think');
    return;
  }
  if (mode === 'nervous') {
    return;
  }
  // idle / work：显示 shared 眼睛并 morph
  const target = mode === 'work' ? 'work' : 'idle';
  const fromIcon = !!activeFaceKey.value;
  activeFaceKey.value = null;
  const revealShared = () => {
    sharedVisible.value = true;
    morphShape(eyeLeftRef.value, currentLeftEyeShape, target);
    morphShape(eyeRightRef.value, currentRightEyeShape, target);
    if (target === 'work') applyWorkTransform();
    else applyIdleTransform();
    currentLeftEyeShape = target;
    currentRightEyeShape = target;
  };
  if (fromIcon) {
    faceSwitchTimer = window.setTimeout(() => {
      revealShared();
      faceSwitchTimer = null;
    }, 160);
  } else {
    revealShared();
  }
  if (target === 'idle' && props.tracking) {
    stopTracking();
    trackEyes();
    scheduleAutoBlink();
  } else {
    stopTracking();
    stopAutoBlink();
  }
}

// toolKeys 按内容 join 后再监听：avatarStatus computed 每次重算都产出新数组引用，
// 若直接监听数组，流式期间（intent 打字、状态刷新）applyState 会被高频触发，
// face 切换动画反复重启，表现为图标无规律抖动
watch(
  () => [props.mode, (props.toolKeys || []).join('\u001f'), props.tracking, internalMode.value],
  () => applyState()
);

onMounted(() => {
  // 初始化时直接定位，避免眼睛从 SVG 原点飞入
  applyIdleTransform(false);
  document.addEventListener('mousemove', onMouseMove);
  applyState();
});
onBeforeUnmount(() => {
  document.removeEventListener('mousemove', onMouseMove);
  stopTracking();
  stopFaceSwitchTimer();
  stopAutoBlink();
  stopNervousTimer();
  morphRaf.forEach((id) => cancelAnimationFrame(id));
  morphRaf.clear();
});
</script>

<style scoped lang="scss">
.status-avatar {
  display: block;
  cursor: pointer;
  overflow: visible;
  color: currentColor;
  flex-shrink: 0;
}

.sa-bg {
  fill: var(--chat-surface-color);
  stroke: none;
}

.sa-frame {
  fill: none;
  stroke: currentColor;
  stroke-width: 10;
  stroke-linecap: round;
  stroke-linejoin: round;
  transform-origin: 100px 100px;
}

.status-avatar--idle .sa-frame {
  stroke-width: 12;
}

.sa-face {
  transform-origin: 100px 100px;
  transition: transform 0.08s linear;
}
.sa-fc {
  transform-origin: 100px 100px;
  pointer-events: none;
}
.sa-shared {
  opacity: 1;
  transition: opacity 0.35s cubic-bezier(0.22, 1, 0.36, 1);
}
.sa-shared.hidden {
  opacity: 0;
}

.status-avatar--work .sa-shared {
  animation: sa-bob 2.4s ease-in-out infinite;
}

.sa-icon {
  opacity: 0;
  transform: scale(0.92);
  transition:
    opacity 0.35s cubic-bezier(0.22, 1, 0.36, 1),
    transform 0.35s cubic-bezier(0.22, 1, 0.36, 1);
}
.sa-icon.active {
  opacity: 1;
  transform: scale(1);
}

// 内部图标线条（比外框稍细），用 :deep 因为 v-html 内容不受 scoped 影响
:deep(.sa-inner) {
  fill: none;
  stroke: currentColor;
  stroke-width: 2.4;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.sa-eye {
  fill: none;
  stroke: currentColor;
  stroke-width: 8;
  stroke-linecap: round;
  stroke-linejoin: round;
  transition: transform 0.55s cubic-bezier(0.22, 1, 0.36, 1);
}

.status-avatar--idle .sa-eye {
  stroke-width: 10;
}

.sa-think-dot {
  fill: currentColor;
  animation: sa-think-wave 1.6s ease-in-out infinite;
}
.sa-think-dot:nth-child(1) {
  animation-delay: -0.48s;
}
.sa-think-dot:nth-child(2) {
  animation-delay: -0.32s;
}
.sa-think-dot:nth-child(3) {
  animation-delay: -0.16s;
}
@keyframes sa-think-wave {
  0%,
  100% {
    transform: translateY(0);
  }
  50% {
    transform: translateY(-12px);
  }
}

:deep(.sa-wiggle) {
  animation: sa-wiggle 1.8s ease-in-out infinite;
  transform-origin: 100px 100px;
}
@keyframes sa-wiggle {
  0%,
  100% {
    transform: translateX(-4px) rotate(-6deg);
  }
  50% {
    transform: translateX(4px) rotate(6deg);
  }
}

:deep(.sa-bob) {
  animation: sa-bob 2.4s ease-in-out infinite;
  transform-origin: 100px 100px;
}
@keyframes sa-bob {
  0%,
  100% {
    transform: translateY(0);
  }
  50% {
    transform: translateY(-9px);
  }
}

// sleep 三个 Z 各自原地晃动，错峰
:deep(.sa-z) {
  animation: sa-z-wiggle 1.8s ease-in-out infinite;
}
:deep(.sa-z.z1) {
  transform-origin: 70px 117px;
  animation-delay: -0.9s;
}
:deep(.sa-z.z2) {
  transform-origin: 100px 100px;
  animation-delay: -0.45s;
}
:deep(.sa-z.z3) {
  transform-origin: 130px 83px;
  animation-delay: 0s;
}
:deep(.sa-z text) {
  fill: currentColor;
}
@keyframes sa-z-wiggle {
  0%,
  100% {
    transform: rotate(-9deg);
  }
  50% {
    transform: rotate(9deg);
  }
}

// 子智能体
.sa-sub-scene {
  transform-origin: 100px 100px;
  animation: sa-sub-zoom 4s ease-in-out infinite;
}
.sa-sub-ray,
.sa-sub-hex,
.sa-sub-eye {
  fill: none;
  stroke: currentColor;
  stroke-width: 5;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.sa-sub-ray {
  stroke-dasharray: 40;
  stroke-dashoffset: 40;
  opacity: 1;
  animation: sa-sub-ray 4s ease-in-out infinite;
}
.sa-sub-hex {
  opacity: 0;
  animation: sa-sub-hex 4s ease-in-out infinite;
}
.sa-sub-eyes {
  opacity: 0;
  transform-origin: 100px 100px;
  animation: sa-sub-eyes 4s ease-in-out infinite;
}
@keyframes sa-sub-ray {
  0% {
    stroke-dashoffset: 40;
    opacity: 1;
  }
  15% {
    stroke-dashoffset: 0;
    opacity: 1;
  }
  30% {
    stroke-dashoffset: 0;
    opacity: 1;
  }
  40%,
  100% {
    opacity: 0;
  }
}
@keyframes sa-sub-hex {
  0%,
  15% {
    opacity: 0;
  }
  30% {
    opacity: 1;
  }
  100% {
    opacity: 1;
  }
}
@keyframes sa-sub-eyes {
  0%,
  40% {
    opacity: 0;
    transform: scale(0.9);
  }
  50% {
    opacity: 1;
    transform: scale(1);
  }
  70% {
    opacity: 1;
    transform: scale(1);
  }
  80%,
  100% {
    opacity: 0;
    transform: scale(1);
  }
}
@keyframes sa-sub-zoom {
  0%,
  50% {
    transform: scale(1);
    opacity: 1;
  }
  70% {
    transform: scale(2);
    opacity: 1;
  }
  99.5% {
    transform: scale(2);
    opacity: 1;
  }
  100% {
    transform: scale(2);
    opacity: 0;
  }
}
</style>
