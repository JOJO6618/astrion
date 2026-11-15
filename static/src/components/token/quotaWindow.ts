import { t } from '@/locales';

/**
 * ChatGPT 订阅窗口标题按「窗口时长」决定，不能按 primary/secondary 槽位。
 *
 * OpenAI 的 `rate_limit.primary_window` / `secondary_window` 只是槽位，语义由
 * `limit_window_seconds` 决定。实测存在只有周限额的 Plus 账号：周窗口占用
 * primary_window（604800s），secondary_window 为 null —— 按槽位命名会把周限额
 * 显示成「5 小时窗口」。
 */

/** 短窗口基准 5 小时（18000 秒）。 */
const ROLLING_SECONDS = 5 * 3600;
/** 周窗口基准 7 天（604800 秒）。 */
const WEEKLY_SECONDS = 7 * 86400;

const isNear = (value: number, target: number, tolerance: number) =>
  Math.abs(value - target) <= target * tolerance;

/** 保留最多 1 位小数的数值文案（如 7 / 7.5），避免出现 "7.0 天"。 */
const tidyNumber = (value: number) => String(Math.round(value * 10) / 10);

export function quotaWindowLabel(seconds?: number | null): string {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) return t('sidebar.subscriptionQuotaWindow');
  if (isNear(value, ROLLING_SECONDS, 0.2)) {
    return t('personalization.codexUsagePrimaryWindow');
  }
  if (isNear(value, WEEKLY_SECONDS, 0.05)) {
    return t('personalization.codexUsageSecondaryWindow');
  }
  // 未知时长按真实长度展示，不猜成 5 小时或每周。
  if (value < 2 * 86400) {
    return t('personalization.codexUsageHoursWindow', { n: tidyNumber(value / 3600) });
  }
  return t('personalization.codexUsageDaysWindow', { n: tidyNumber(value / 86400) });
}
