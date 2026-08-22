<template>
  <!--
    数字滚筒(直达式):每一位是一个固定高度的窗口,内部只放「旧值 / 新值」两格。
    一次变化只滚一步,不路过中间数字。
    - 值变大:旧在上、新在下,带子上移 → 下面的数字顶上来。
    - 值变小:新在上、旧在下,带子先停在下格再回滚 → 上面的数字压下来。
    各位用 transition-delay 错开,高位先动,间隔很短。
  -->
  <span class="rolling-number">
    <TransitionGroup name="rn-place" tag="span" class="rolling-number__track">
      <span
        v-for="item in renderPlaces"
        :key="item.place"
        class="rolling-number__digit"
      >
        <span
          class="rolling-number__strip"
          :class="{ 'is-animating': item.state.animate }"
          :style="{ '--rn-offset': item.state.offset, transitionDelay: item.state.delay + 'ms' }"
        >
          <span class="rolling-number__cell">{{ item.state.top }}</span>
          <span class="rolling-number__cell">{{ item.state.bottom }}</span>
        </span>
      </span>
    </TransitionGroup>
  </span>
</template>

<script setup lang="ts">
import { ref, computed, reactive, nextTick, watch, onBeforeUnmount } from 'vue';

const props = defineProps<{ value: number }>();

// 相邻位的启动错开(ms),高位先动,整体间隔很短。
const STEP = 38;
// 单位滚动时长,需与下方 css transition 时长保持一致。
const DUR = 360;

type PlaceState = {
  top: number;      // 上格数字
  bottom: number;   // 下格数字
  offset: number;   // 当前停靠格:0=显示上格,1=显示下格
  animate: boolean; // 是否启用过渡(false 时为瞬时落位)
  delay: number;    // transition-delay,实现高→低位错开
};

// place:位权,0=个位,越大位权越高。
const order = ref<number[]>([]);            // 渲染顺序(高位在前)
const states = new Map<number, PlaceState>(); // place => 该位的滚动状态(reactive)
let booted = false;
let gen = 0;                                 // 代次,防止过期定时器回写
const normalizeTimers = new Set<number>();
const removeTimers = new Map<number, number>();

function digitsOf(v: number): Map<number, number> {
  const s = Math.max(0, Math.trunc(Number(v) || 0)).toString();
  const len = s.length;
  const m = new Map<number, number>();
  for (let i = 0; i < len; i++) m.set(len - 1 - i, Number(s[i]));
  return m;
}

const renderPlaces = computed(() =>
  order.value
    .map((p) => ({ place: p, state: states.get(p) as PlaceState }))
    .filter((x) => !!x.state)
);

function setOrderDesc(places: Iterable<number>) {
  order.value = [...new Set(places)].sort((a, b) => b - a);
}

watch(
  () => props.value,
  async (val) => {
    const target = digitsOf(val);
    const myGen = ++gen;

    // 首次直接落位,不滚动。
    if (!booted) {
      booted = true;
      states.clear();
      for (const [p, d] of target) {
        states.set(p, reactive({ top: d, bottom: d, offset: 0, animate: false, delay: 0 }));
      }
      setOrderDesc(target.keys());
      return;
    }

    const union = new Set<number>([...states.keys(), ...target.keys()]);
    const maxPlace = Math.max(...union, 0);

    const ups: PlaceState[] = [];
    const downs: PlaceState[] = [];

    // 目标里仍存在的位,撤销其待删除计时。
    for (const p of target.keys()) {
      const t = removeTimers.get(p);
      if (t) {
        window.clearTimeout(t);
        removeTimers.delete(p);
      }
    }

    for (const place of union) {
      const delay = (maxPlace - place) * STEP;
      const nv = target.get(place);
      if (nv === undefined) continue; // 该位被移除,下面统一处理淡出

      const existing = states.get(place);
      if (!existing) {
        // 新增高位:直接出现(配合 TransitionGroup 淡入),不滚动。
        states.set(place, reactive({ top: nv, bottom: nv, offset: 0, animate: false, delay }));
        continue;
      }

      existing.delay = delay;
      const ov = existing.top; // 当前显示值
      if (ov === nv) {
        existing.bottom = nv;
        existing.offset = 0;
        existing.animate = false;
        continue;
      }
      if (ov < nv) {
        // 增加:上格=旧,下格=新,offset 0 → 1(下面顶上来)
        existing.animate = false;
        existing.top = ov;
        existing.bottom = nv;
        existing.offset = 0;
        ups.push(existing);
      } else {
        // 减少:上格=新,下格=旧,offset 1 → 0(上面压下来)
        existing.animate = false;
        existing.top = nv;
        existing.bottom = ov;
        existing.offset = 1;
        downs.push(existing);
      }
    }

    // 更新渲染顺序(纳入新增位,触发移除位的淡出)。
    setOrderDesc(target.keys());

    // 移除位:淡出动画跑完后再清理状态。
    for (const place of [...states.keys()]) {
      if (!target.has(place) && !removeTimers.has(place)) {
        const t = window.setTimeout(() => {
          removeTimers.delete(place);
          states.delete(place);
        }, 260);
        removeTimers.set(place, t);
      }
    }

    // 等起始态渲染并绘制后,再统一开启过渡并滚到目标格。
    await nextTick();
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        if (myGen !== gen) return;
        for (const s of ups) {
          s.animate = true;
          s.offset = 1;
        }
        for (const s of downs) {
          s.animate = true;
          s.offset = 0;
        }
      });
    });

    // 最慢一位滚完后归一化:落到 top=新值、offset=0、关闭过渡(瞬时,无闪烁)。
    const settle = maxPlace * STEP + DUR + 80;
    const norm = window.setTimeout(() => {
      normalizeTimers.delete(norm);
      if (myGen !== gen) return;
      for (const [p, d] of target) {
        const s = states.get(p);
        if (s) {
          s.animate = false;
          s.top = d;
          s.bottom = d;
          s.offset = 0;
        }
      }
    }, settle);
    normalizeTimers.add(norm);
  },
  { immediate: true }
);

onBeforeUnmount(() => {
  normalizeTimers.forEach((t) => window.clearTimeout(t));
  normalizeTimers.clear();
  removeTimers.forEach((t) => window.clearTimeout(t));
  removeTimers.clear();
});
</script>

<style scoped>
.rolling-number {
  display: inline-flex;
  vertical-align: baseline;
}

.rolling-number__track {
  position: relative;
  display: inline-flex;
  align-items: stretch;
}

.rolling-number__digit {
  --rn-h: 1.2em;
  display: inline-block;
  height: var(--rn-h);
  line-height: var(--rn-h);
  overflow: hidden;
}

.rolling-number__strip {
  display: flex;
  flex-direction: column;
  transform: translateY(calc(var(--rn-offset, 0) * var(--rn-h) * -1));
  will-change: transform;
}

.rolling-number__strip.is-animating {
  transition: transform 360ms cubic-bezier(0.22, 0.61, 0.36, 1);
}

.rolling-number__cell {
  height: var(--rn-h);
  line-height: var(--rn-h);
  text-align: center;
  font-variant-numeric: inherit;
}

/* 位数增减时整位的进出:新增位淡入下沉,消失位淡出上移 */
.rn-place-enter-active,
.rn-place-leave-active {
  transition: opacity 220ms ease, transform 220ms ease;
}

.rn-place-enter-from {
  opacity: 0;
  transform: translateY(45%);
}

.rn-place-leave-to {
  opacity: 0;
  transform: translateY(-45%);
}

/* 让消失位脱离布局,其余位平滑左移而非瞬跳 */
.rn-place-leave-active {
  position: absolute;
}

.rn-place-move {
  transition: transform 220ms ease;
}

@media (prefers-reduced-motion: reduce) {
  .rolling-number__strip.is-animating {
    transition: none;
  }
  .rn-place-enter-active,
  .rn-place-leave-active,
  .rn-place-move {
    transition: none;
  }
}
</style>
