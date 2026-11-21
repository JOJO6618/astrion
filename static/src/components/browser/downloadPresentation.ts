import { t } from '@/locales';
import { formatBytes, formatRate } from '@/utils/formatters';
import type { BrowserDownload } from './types';

export function downloadProgress(row: BrowserDownload) {
  const status = t(`browser.${row.state}`, row.state);
  const received = formatBytes(row.received);
  const size = row.total ? `${received} / ${formatBytes(row.total)}` : received;
  if (row.state !== 'progressing' || !row.total) return `${size} · ${status}`;
  return `${size} · ${status} ${Math.min(99, Math.floor((row.received / row.total) * 100))}%`;
}

export function downloadTransfer(row: BrowserDownload) {
  const details = [];
  if (row.bytesPerSecond) details.push(formatRate(row.bytesPerSecond));
  if (row.estimatedCompletionAt) {
    details.push(
      t('browser.downloadEstimatedCompletion', { time: downloadTime(row.estimatedCompletionAt) })
    );
  }
  return details.join(' · ');
}

export function downloadTime(timestamp: number) {
  const time = new Date(timestamp);
  const options: Intl.DateTimeFormatOptions = { hour: '2-digit', minute: '2-digit' };
  if (time.toDateString() !== new Date().toDateString()) {
    options.month = 'numeric';
    options.day = 'numeric';
  }
  return time.toLocaleString(undefined, options);
}
