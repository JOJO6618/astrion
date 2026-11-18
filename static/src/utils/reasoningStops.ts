/**
 * 顶栏模型弹窗的推理滑块档位（2026-11-18）：快速模式并入滑块最左的「无」档，
 * 右侧各档是思考模式的推理强度。Agent 页与 Chat 模式共用同一套换算。
 */
import type { ReasoningEffort } from '@/stores/personalization';

export const REASONING_LEVELS: ReasoningEffort[] = ['low', 'medium', 'high', 'xhigh', 'max'];

/** none = 不思考（fast）；on = 思考但模型没有强度参数；其余为具体强度 */
export type ReasoningStop = 'none' | 'on' | ReasoningEffort;

export interface ReasoningCapability {
  /** 能开启思考 */
  canThink: boolean;
  /** 能关闭思考（thinkingOnly 模型不能） */
  canSkipThinking: boolean;
  /** 支持的强度档位；空数组 = 没有强度参数 */
  levels: ReasoningEffort[];
}

/** 弹窗里的一次推理设置变更 */
export interface ReasoningChange {
  thinking: boolean;
  /** 不传 = 不改强度；null = 默认推理 */
  effort?: ReasoningEffort | null;
}

interface ModelLike {
  fastOnly?: boolean;
  supportsThinking?: boolean;
  thinkingOnly?: boolean;
  supportsReasoningEffort?: boolean;
  supportedReasoningLevels?: Array<{ effort?: string } | null> | null;
}

/** 档位按模型过滤（Codex 各模型支持 4~6 档，未定义的档位天然被过滤）；无声明时用全集 */
export function reasoningLevelsFor(model: ModelLike | null | undefined): ReasoningEffort[] {
  if (!model?.supportsReasoningEffort) return [];
  const supported = model.supportedReasoningLevels;
  if (Array.isArray(supported) && supported.length) {
    const filtered = REASONING_LEVELS.filter((lv) => supported.some((s) => s?.effort === lv));
    if (filtered.length) return filtered;
  }
  return [...REASONING_LEVELS];
}

export function reasoningCapabilityFor(model: ModelLike | null | undefined): ReasoningCapability {
  const canThink = !!model && !model.fastOnly && model.supportsThinking !== false;
  return {
    canThink,
    canSkipThinking: !model?.thinkingOnly,
    levels: canThink ? reasoningLevelsFor(model) : []
  };
}

/** 滑块档位；少于 2 档时不显示滑块 */
export function reasoningStops(cap: ReasoningCapability): ReasoningStop[] {
  const stops: ReasoningStop[] = [];
  if (cap.canSkipThinking) stops.push('none');
  if (cap.canThink) stops.push(...(cap.levels.length ? cap.levels : (['on'] as ReasoningStop[])));
  return stops;
}

/** 「默认推理」打开时滑块停留（置灰）的档位：优先记忆值，其次 high，再次最高档 */
export function fallbackLevel(
  levels: ReasoningEffort[],
  preferred?: string | null
): ReasoningEffort | null {
  if (preferred && levels.includes(preferred as ReasoningEffort))
    return preferred as ReasoningEffort;
  if (levels.includes('high')) return 'high';
  return levels[levels.length - 1] ?? null;
}

/** 当前状态对应的档位 */
export function currentReasoningStop(
  cap: ReasoningCapability,
  thinking: boolean,
  effort: string | null | undefined,
  memory: string | null | undefined
): ReasoningStop {
  const stops = reasoningStops(cap);
  if (!thinking && stops.includes('none')) return 'none';
  if (!cap.levels.length) return stops.includes('on') ? 'on' : stops[0];
  return fallbackLevel(cap.levels, effort || memory) ?? stops[0];
}
