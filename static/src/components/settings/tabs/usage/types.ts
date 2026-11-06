/** /api/usage-stats 载荷（后端 modules/usage_stats/report.py + server/usage_stats.py） */

export type UsageMetric = 'tokens' | 'work' | 'cost' | 'requests';
export type UsageGranularity = 'day' | 'week' | 'cum';
export type UsageRange = '7' | '30' | 'all';

export interface UsageDay {
  date: string;
  input_tokens: number;
  cached_tokens: number;
  output_tokens: number;
  cost_usd: string;
  requests: number;
  unpriced_requests: number;
  unpriced_tokens: number;
  top_model: string | null;
}

export interface UsageModelRow {
  model_key: string;
  tokens: number;
  input_tokens: number;
  cached_tokens: number;
  output_tokens: number;
  cost_usd: string;
  requests: number;
  priced_requests: number;
  unpriced_requests: number;
  speed_tokens: number;
  speed_ms: number;
  ttft_ms: number;
  ttft_n: number;
}

export interface UsageBreakdown {
  models: UsageModelRow[];
  /** 执行者类型 → token：main / sub_agent / thinking_summary / review_agent … */
  kinds: Record<string, number>;
  /** agent / chat → token */
  modes: Record<string, number>;
  sources: { id: string; tokens: number }[];
  /** [周一..周日][0..23 时] → token（本地时区） */
  punch: number[][];
}

export interface UsageOverview {
  total_tokens: number;
  input_tokens: number;
  cached_tokens: number;
  output_tokens: number;
  cost_usd: string;
  requests: number;
  priced_requests: number;
  unpriced_requests: number;
  unpriced_tokens: number;
  active_days: number;
  current_streak: number;
  longest_streak: number;
  best_day: { date: string; tokens: number } | null;
}

export interface UsageSource {
  /** ws:<workspace_id> / ws:__unassigned__ / chat */
  id: string;
  label: string;
  mode: 'agent' | 'chat';
  has_data: boolean;
}

export interface UsageReport {
  today: string;
  window_start: string;
  first_date: string | null;
  overview: UsageOverview;
  days: UsageDay[];
  /** 热力图窗口（53 周）之前的合计，供「累计总量」起点 */
  prior: { tokens: number; work_tokens: number; cost_usd: string; requests: number };
  ranges: Record<UsageRange, UsageBreakdown>;
  selected: string;
  timezone: string;
  sources: UsageSource[];
  exchange_rate?: { usd_cny?: number; rate_date?: string; stale?: boolean };
}

/** 图表悬浮提示（UsageTooltip）状态；anchor 为触发元素的视口矩形 */
export interface UsageTooltipState {
  visible: boolean;
  title: string;
  main: string;
  rows: [string, string][];
  anchor: { left: number; top: number; width: number; bottom: number } | null;
}

export function emptyTooltip(): UsageTooltipState {
  return { visible: false, title: '', main: '', rows: [], anchor: null };
}
