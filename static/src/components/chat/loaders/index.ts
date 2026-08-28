import { Component } from 'vue';
import { t } from '@/locales';
import SquareGridLoader from './SquareGridLoader.vue';
import RotatingSquaresLoader from './RotatingSquaresLoader.vue';
import DualChaseLoader from './DualChaseLoader.vue';
import BouncingSquaresLoader from './BouncingSquaresLoader.vue';
import FiveSquaresLoader from './FiveSquaresLoader.vue';
import NineGridLoader from './NineGridLoader.vue';
import RippleLoader from './RippleLoader.vue';
import NewtonCradleSquaresLoader from './NewtonCradleSquaresLoader.vue';
import DotSpinnerSquaresLoader from './DotSpinnerSquaresLoader.vue';
import OrbitBlurSquaresLoader from './OrbitBlurSquaresLoader.vue';
import ShadowRollingSquaresLoader from './ShadowRollingSquaresLoader.vue';
import JumpingSequenceSquaresLoader from './JumpingSequenceSquaresLoader.vue';
import ChaoticOrbitSquaresLoader from './ChaoticOrbitSquaresLoader.vue';
import LeapFrogSquaresLoader from './LeapFrogSquaresLoader.vue';
import WaveMatrixSquaresLoader from './WaveMatrixSquaresLoader.vue';
import FollowTrackSquaresLoader from './FollowTrackSquaresLoader.vue';
import FourCornerCycleSquaresLoader from './FourCornerCycleSquaresLoader.vue';

// 加载动画池
export const loaderPool: Component[] = [
  SquareGridLoader,
  RotatingSquaresLoader,
  DualChaseLoader,
  BouncingSquaresLoader,
  FiveSquaresLoader,
  NineGridLoader,
  RippleLoader,
  NewtonCradleSquaresLoader,
  DotSpinnerSquaresLoader,
  OrbitBlurSquaresLoader,
  ShadowRollingSquaresLoader,
  JumpingSequenceSquaresLoader,
  ChaoticOrbitSquaresLoader,
  LeapFrogSquaresLoader,
  WaveMatrixSquaresLoader,
  FollowTrackSquaresLoader,
  FourCornerCycleSquaresLoader
];

// 至少间隔多少次后才允许重复（例如 4 表示最近 4 次不重复）
const MIN_NON_REPEAT_WINDOW = 4;
const recentPickedIndices: number[] = [];

const pickRandomIndex = (indices: number[]) => {
  const randomIndex = Math.floor(Math.random() * indices.length);
  return indices[randomIndex];
};

// 随机获取一个加载动画（带“近期不重复”约束）
export function getRandomLoader(): Component {
  const poolSize = loaderPool.length;
  if (poolSize === 0) {
    throw new Error(t('chatActions.loaderPoolEmpty'));
  }

  // 不重复窗口不能超过 poolSize - 1，否则无解
  const nonRepeatWindow = Math.max(0, Math.min(MIN_NON_REPEAT_WINDOW, poolSize - 1));

  let candidateIndices = Array.from({ length: poolSize }, (_, i) => i);
  if (nonRepeatWindow > 0 && recentPickedIndices.length > 0) {
    const blocked = new Set(recentPickedIndices.slice(-nonRepeatWindow));
    const filtered = candidateIndices.filter((idx) => !blocked.has(idx));
    if (filtered.length > 0) {
      candidateIndices = filtered;
    }
  }

  const pickedIndex = pickRandomIndex(candidateIndices);
  recentPickedIndices.push(pickedIndex);

  // 控制历史队列长度，避免无限增长
  const maxHistory = Math.max(poolSize * 2, nonRepeatWindow + 1);
  if (recentPickedIndices.length > maxHistory) {
    recentPickedIndices.splice(0, recentPickedIndices.length - maxHistory);
  }

  return loaderPool[pickedIndex];
}
