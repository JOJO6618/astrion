/** 字节数格式化：B/KB/MB/GB/TB；GB 以上保留两位小数。 */
export function formatBytes(bytes: number): string {
  const value = Number(bytes) || 0;
  if (value < 1) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let size = value;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  const digits = index >= 3 ? 2 : index === 0 ? 0 : 1;
  return `${size.toFixed(digits)} ${units[index]}`;
}

/** 占比：小于 0.1% 显示 <0.1%，避免出现一排 0.0%。 */
export function formatShare(part: number, total: number): string {
  if (!total || total <= 0) return '0%';
  const pct = (part / total) * 100;
  if (pct > 0 && pct < 0.1) return '<0.1%';
  return `${pct.toFixed(pct >= 10 ? 0 : 1)}%`;
}

/** 后端返回的 updated_at 是本地 naive ISO 字符串，Date.parse 按本地时区解析。 */
export function parseTimestamp(raw?: string | null): number | null {
  if (!raw) return null;
  const ts = Date.parse(raw);
  return Number.isFinite(ts) ? ts : null;
}
