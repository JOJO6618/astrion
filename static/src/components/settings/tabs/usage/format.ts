import { currentLocale, t } from '@/locales';
import type { UsageDay, UsageMetric, UsageSource } from './types';

/** ISO 日期（YYYY-MM-DD）→ 本地零点 Date；统计日期已由后端按时区换算，前端不再做时区运算 */
export function parseDay(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, (m || 1) - 1, d || 1);
}

export function isoDay(date: Date): string {
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${m}-${d}`;
}

export function addDays(date: Date, days: number): Date {
  const next = new Date(date.getTime());
  next.setDate(next.getDate() + days);
  return next;
}

/** 周一为 0 … 周日为 6 */
export function mondayIndex(date: Date): number {
  return (date.getDay() + 6) % 7;
}

const locale = () => currentLocale.value;

/** 紧凑计数：zh-CN 由 Intl 输出万／亿，en-US 输出 K／M／B */
export function formatTokens(value: number): string {
  const n = Math.max(0, value || 0);
  return new Intl.NumberFormat(locale(), {
    notation: 'compact',
    maximumFractionDigits: n >= 1e8 ? 2 : 1
  }).format(n);
}

export function formatInt(value: number): string {
  return Math.round(value || 0).toLocaleString(locale());
}

export function formatPercent(part: number, whole: number): number {
  return whole > 0 ? Math.round((part / whole) * 100) : 0;
}

/** USD 金额按显示币种换算；缺汇率返回 null（调用方显示「—」，不虚构汇率） */
export function convertMoney(usd: string | number, rate: number | null): number | null {
  if (rate === null || !Number.isFinite(rate)) return null;
  const amount = Number(usd) * rate;
  return Number.isFinite(amount) ? amount : null;
}

export function formatMoney(usd: string | number, currency: string, rate: number | null): string {
  const amount = convertMoney(usd, rate);
  if (amount === null) return '—';
  const digits = amount !== 0 && Math.abs(amount) < 0.01 ? 4 : 2;
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    currencyDisplay: 'narrowSymbol',
    minimumFractionDigits: digits,
    maximumFractionDigits: digits
  }).format(amount);
}

export function formatDate(date: Date): string {
  return new Intl.DateTimeFormat(locale(), { month: 'short', day: 'numeric' }).format(date);
}

export function formatMonth(date: Date): string {
  return new Intl.DateTimeFormat(locale(), { month: 'short' }).format(date);
}

/** 周一起的星期短名（2024-01-01 为周一，用作基准日） */
export function weekdayName(index: number): string {
  return new Intl.DateTimeFormat(locale(), { weekday: 'short' }).format(new Date(2024, 0, 1 + index));
}

export function metricValue(day: UsageDay | undefined, metric: UsageMetric): number {
  if (!day) return 0;
  if (metric === 'tokens') return day.input_tokens + day.output_tokens;
  if (metric === 'work') return day.input_tokens - day.cached_tokens + day.output_tokens;
  if (metric === 'cost') return Number(day.cost_usd) || 0;
  return day.requests;
}

/**
 * 非零值按 25/50/80 分位分为 1–4 档（0 档为无用量）。
 * 用分位而非线性：一天的极端大用量不会把其余格子全压成最浅色。
 */
export function quantileLevels(values: number[]): (value: number) => number {
  const sorted = values.filter((v) => v > 0).sort((a, b) => a - b);
  if (!sorted.length) return () => 0;
  const at = (p: number) => sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))];
  const [t1, t2, t3] = [at(0.25), at(0.5), at(0.8)];
  return (v) => (v <= 0 ? 0 : v < t1 ? 1 : v < t2 ? 2 : v < t3 ? 3 : 4);
}

/** 坐标轴取整上限：1/2/2.5/5/10 × 10^n */
export function niceMax(value: number): number {
  if (value <= 0) return 1;
  const power = Math.pow(10, Math.floor(Math.log10(value)));
  const n = value / power;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * power;
}

/** model_key = "<provider>/<model>"；无前缀时 provider 为空 */
export function splitModelKey(key: string): { model: string; provider: string } {
  const index = key.indexOf('/');
  return index > 0 ? { provider: key.slice(0, index), model: key.slice(index + 1) } : { provider: '', model: key };
}

export function sourceLabel(source: Pick<UsageSource, 'id' | 'label'>): string {
  if (source.id === 'chat') return t('usageStats.chatSource');
  if (source.id === 'ws:__unassigned__') return t('usageStats.unassignedSource');
  return source.label || source.id.replace(/^ws:/, '');
}
