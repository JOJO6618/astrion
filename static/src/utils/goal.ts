import { currentLocale, t } from '@/locales';

const states = new Set([
  'running',
  'reviewing',
  'retrying',
  'paused',
  'blocked',
  'done',
  'stopped'
]);
const reasons = new Set([
  'user_cancel',
  'process_restarted',
  'max_turns',
  'max_tokens',
  'no_progress',
  'execution_interrupted',
  'quota_exhausted',
  'authentication',
  'configuration'
]);

export function goalStatusLabel(status: unknown): string {
  void currentLocale.value;
  const value = String(status || 'running');
  return t(`goal.states.${states.has(value) ? value : 'blocked'}`);
}

export function goalReason(reason: unknown): string {
  void currentLocale.value;
  const value = String(reason || '');
  return reasons.has(value) ? t(`goal.reasons.${value}`) : '';
}
