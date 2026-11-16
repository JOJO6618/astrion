import { currentLocale, t } from '@/locales';
import type { ScheduleDefinition, ScheduleRun } from './useSchedules';

const WEEK_ORDER = [1, 2, 3, 4, 5, 6, 0];

/** 列表行里的时间规则摘要；权威时间计算在后端，这里只做展示。 */
export function describeSchedule(schedule: ScheduleDefinition['schedule']): string {
  const times = (schedule.times || []).join(', ');
  switch (schedule.type) {
    case 'daily':
      return t('schedules.describeDaily', { time: times });
    case 'weekly': {
      const days = WEEK_ORDER.filter((day) => schedule.days?.includes(day))
        .map((day) => t(`schedules.weekdayName${day}`))
        .join(t('schedules.listSeparator'));
      return `${days} ${times}`.trim();
    }
    case 'monthly':
      return schedule.day === 'last'
        ? t('schedules.describeMonthlyLast', { time: times })
        : t('schedules.describeMonthlyDay', { day: String(schedule.day ?? ''), time: times });
    case 'interval':
      return t(
        schedule.unit === 'minutes'
          ? 'schedules.describeEveryMinutes'
          : 'schedules.describeEveryHours',
        { count: String(schedule.every ?? '') }
      );
    case 'once':
      return `${schedule.date || ''} ${times}`.trim();
    case 'cron':
      return schedule.expression || '';
    default:
      return t('schedules.ruleIncomplete');
  }
}

/** 月/日 时:分，按计划时区显示。 */
export function shortDate(value: string | number | Date, timezone?: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const options: Intl.DateTimeFormatOptions = {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23'
  };
  try {
    return new Intl.DateTimeFormat(currentLocale.value, { ...options, timeZone: timezone }).format(
      date
    );
  } catch {
    return new Intl.DateTimeFormat(currentLocale.value, options).format(date);
  }
}

export function runDuration(run: ScheduleRun): string {
  if (!run.finished_at) return t('schedules.inProgress');
  const ms = new Date(run.finished_at).getTime() - new Date(run.created_at).getTime();
  if (!Number.isFinite(ms) || ms < 0) return '—';
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return t('schedules.durationSeconds', { s: String(seconds) });
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60)
    return t('schedules.durationMinutes', { m: String(minutes), s: String(seconds % 60) });
  return t('schedules.durationHours', {
    h: String(Math.floor(minutes / 60)),
    m: String(minutes % 60)
  });
}

const ACTIVE = new Set(['queued', 'starting', 'running', 'waiting', 'stopping']);

export const isActiveRun = (run: ScheduleRun | null | undefined) => !!run && ACTIVE.has(run.state);

/** 运行记录状态 → 圆形徽标样式与图标。 */
export function runBadge(state: string): { tone: string; icon: string } {
  if (state === 'success') return { tone: 'success', icon: 'check' };
  if (state === 'failed' || state === 'interrupted') return { tone: 'failed', icon: 'x' };
  if (ACTIVE.has(state)) return { tone: 'running', icon: 'refresh-cw' };
  return { tone: 'skipped', icon: 'clock' };
}

export function runTitle(run: ScheduleRun): string {
  const first = (run.summary || run.reason || '').split('\n').find((line) => line.trim());
  return first?.trim() || t(`schedules.state_${run.state}`);
}

/** 用全角空格连接日期、状态、耗时（与设计稿一致）。 */
export const joinWide = (...parts: string[]) => parts.filter(Boolean).join('\u3000');
