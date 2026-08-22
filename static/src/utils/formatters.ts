type NumericLike = number | string | null | undefined;

export interface QuotaEntry {
  count?: NumericLike;
  limit?: NumericLike;
  reset_at?: NumericLike;
}

export type UsageQuotaMap = Record<string, QuotaEntry | undefined> | null | undefined;

const BYTE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB'];
const RATE_UNITS = ['B/s', 'KB/s', 'MB/s', 'GB/s'];
const QUOTA_TYPE_LABELS: Record<string, string> = {
  thinking: '思考模型',
  search: '搜索',
  fast: '常规模型'
};

function normalizeNumber(value: NumericLike): number {
  const num = Number(value);
  return Number.isFinite(num) ? num : 0;
}

export function formatTokenCount(tokens: NumericLike): string {
  const num = normalizeNumber(tokens);
  if (num < 1000) {
    return num.toString();
  }
  if (num < 1000000) {
    return `${(num / 1000).toFixed(1)}K`;
  }
  return `${(num / 1000000).toFixed(1)}M`;
}

export function formatBytes(bytes: NumericLike): string {
  if (bytes === null || typeof bytes === 'undefined') {
    return '—';
  }
  let value = Number(bytes);
  if (!Number.isFinite(value)) {
    return '—';
  }
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < BYTE_UNITS.length - 1) {
    value /= 1024;
    unitIndex++;
  }
  const decimals = value >= 10 || unitIndex === 0 ? 0 : 1;
  return `${value.toFixed(decimals)} ${BYTE_UNITS[unitIndex]}`;
}

export function formatPercentage(value: NumericLike): string {
  if (value === null || typeof value === 'undefined') {
    return '—';
  }
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return '—';
  }
  return `${numeric.toFixed(1)}%`;
}

export function formatRate(bytesPerSecond: NumericLike): string {
  if (bytesPerSecond === null || typeof bytesPerSecond === 'undefined') {
    return '—';
  }
  let value = Number(bytesPerSecond);
  if (!Number.isFinite(value)) {
    return '—';
  }
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < RATE_UNITS.length - 1) {
    value /= 1024;
    unitIndex++;
  }
  const decimals = value >= 10 || unitIndex === 0 ? 0 : 1;
  return `${value.toFixed(decimals)} ${RATE_UNITS[unitIndex]}`;
}

export function quotaTypeLabel(type: string): string {
  return QUOTA_TYPE_LABELS[type] || QUOTA_TYPE_LABELS.fast;
}

export function formatResetTime(iso?: NumericLike): string {
  if (!iso) {
    return '--:--';
  }
  const date = new Date(iso as any);
  if (Number.isNaN(date.getTime())) {
    return '--:--';
  }
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export function formatQuotaValue(entry?: QuotaEntry | null): string {
  if (!entry) {
    return '--';
  }
  const count = normalizeNumber(entry.count);
  const limit = normalizeNumber(entry.limit);
  if (!limit) {
    return `${count}`;
  }
  return `${count} / ${limit}`;
}

export function buildQuotaResetSummary(quota: UsageQuotaMap): string {
  if (!quota) {
    return '';
  }
  const parts: string[] = [];
  ['fast', 'thinking', 'search'].forEach((type) => {
    const entry = quota?.[type];
    if (entry && entry.reset_at && normalizeNumber(entry.count) > 0) {
      parts.push(`${quotaTypeLabel(type)} ${formatResetTime(entry.reset_at)}`);
    }
  });
  return parts.join(' · ');
}

export function isQuotaExceeded(quota: UsageQuotaMap, type: string): boolean {
  if (!quota) {
    return false;
  }
  const entry = quota[type];
  if (!entry) {
    return false;
  }
  const limit = normalizeNumber(entry.limit);
  if (!limit) {
    return false;
  }
  return normalizeNumber(entry.count) >= limit;
}

export function buildQuotaToastMessage(
  type: string,
  quota: UsageQuotaMap,
  resetAt?: NumericLike
): string {
  const entry = quota?.[type];
  const referenceResetAt = typeof resetAt !== 'undefined' ? resetAt : entry?.reset_at;
  return `${quotaTypeLabel(type)} 配额已用完，将在 ${formatResetTime(referenceResetAt)} 重置`;
}
