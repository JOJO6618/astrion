import { t } from '@/locales';
import { describeSchedule, runDuration, shortDate } from '../../schedules/scheduleFormat';
import { box, empty, escapeHtml, meta, param, preBox, summary } from './toolResultHtml';

const R = (key: string, params: Record<string, unknown> = {}) =>
  t(`toolResults.schedule.${key}`, params);

const actionLabel = (action: unknown) => {
  const key = String(action || '');
  const labels: Record<string, string> = {
    enable: R('actions.enable'),
    pause: R('actions.pause'),
    delete: R('actions.delete'),
    run: R('actions.run'),
    stop: R('actions.stop')
  };
  return labels[key] || key;
};

const stateLabel = (state: unknown) => {
  const key = String(state || 'unknown');
  return t(`schedules.state_${key}`);
};

const dateLabel = (value: unknown, timezone?: string) => {
  if (!value) return R('none');
  return shortDate(String(value), timezone);
};

function renderArguments(name: string, args: Record<string, any>): string {
  if (name === 'submit_schedule') {
    return meta(
      param(R('filePath'), args.file_path),
      param(
        R('overwrite'),
        typeof args.overwrite === 'boolean'
          ? args.overwrite
            ? t('toolResults.values.yes')
            : t('toolResults.values.no')
          : ''
      )
    );
  }
  if (name === 'query_schedules') {
    return meta(
      param(R('scheduleId'), args.schedule_id),
      param(R('workspaceId'), args.workspace_id),
      param(
        R('enabledFilter'),
        typeof args.enabled === 'boolean'
          ? args.enabled
            ? t('toolResults.values.yes')
            : t('toolResults.values.no')
          : ''
      ),
      param(
        R('includeRuns'),
        typeof args.include_runs === 'boolean'
          ? args.include_runs
            ? t('toolResults.values.yes')
            : t('toolResults.values.no')
          : ''
      ),
      param(R('fields'), Array.isArray(args.fields) ? args.fields.join(', ') : '')
    );
  }
  return meta(
    param(R('scheduleId'), args.schedule_id),
    param(R('action'), actionLabel(args.action)),
    param(R('revision'), args.revision),
    param(R('runId'), args.run_id),
    param(R('requestId'), args.request_id)
  );
}

function renderRun(run: any, timezone?: string, heading = ''): string {
  if (!run || typeof run !== 'object') return '';
  const state = String(run.state || 'unknown');
  const stateClass = [
    'success',
    'failed',
    'interrupted',
    'stopped',
    'skipped',
    'running',
    'waiting',
    'starting',
    'stopping',
    'queued'
  ].includes(state)
    ? state
    : 'unknown';
  const title = run.summary || run.reason || stateLabel(state);
  return `<div class="schedule-result-run">
    ${heading ? `<div class="schedule-result-run-heading">${escapeHtml(heading)}</div>` : ''}
    <div class="schedule-result-run-title">${escapeHtml(String(title))}</div>
    <div class="schedule-result-run-state is-${stateClass}">${escapeHtml(stateLabel(state))}</div>
    ${meta(
      param(R('scheduledFor'), dateLabel(run.scheduled_for || run.created_at, timezone)),
      param(R('duration'), runDuration(run)),
      param(R('conversationId'), run.conversation_id),
      param(R('reason'), run.reason),
      param(R('runId'), run.id)
    )}
  </div>`;
}

function renderPlan(plan: any): string {
  const definition = plan?.definition || {};
  const schedule = definition.schedule || {};
  const timezone = schedule.timezone;
  const enabled = definition.enabled === true;
  const runs = Array.isArray(plan?.runs) ? plan.runs : [];
  const runItems = runs.map((run: any) => renderRun(run, timezone)).join('');
  const nextAt = plan?.next_at ? dateLabel(plan.next_at, timezone) : R('none');

  return `<div class="schedule-result-plan">
    <div class="schedule-result-plan-title">${escapeHtml(String(definition.name || t('toolResults.values.unnamed')))}</div>
    <div class="schedule-result-plan-state ${enabled ? 'is-enabled' : 'is-paused'}">${escapeHtml(enabled ? R('enabled') : R('paused'))}</div>
    ${meta(
      param(R('scheduleId'), definition.id),
      param(R('rule'), schedule.type ? describeSchedule(schedule) : R('none')),
      param(R('timezone'), timezone),
      param(R('workspaceId'), definition.target?.workspace_id),
      param(R('nextRun'), nextAt),
      param(R('message'), definition.message, { block: true })
    )}
    ${plan?.active_run ? renderRun(plan.active_run, timezone, R('activeRun')) : ''}
    ${plan?.last_run ? renderRun(plan.last_run, timezone, R('lastRun')) : ''}
    ${runItems ? `<div class="schedule-result-run-list">${runItems}</div>` : ''}
  </div>`;
}

export function renderScheduleResult(name: string, result: any, args: Record<string, any>): string {
  const parameters = renderArguments(name, args);
  if (!result?.success) return parameters;

  if (name === 'submit_schedule') {
    return (
      parameters +
      summary(R('submitted')) +
      (result.plan ? box(renderPlan(result.plan)) : preBox(JSON.stringify(result, null, 2)))
    );
  }

  if (name === 'query_schedules') {
    const plans = Array.isArray(result.plans) ? result.plans : [];
    return (
      parameters +
      summary(R('planCount', { n: plans.length })) +
      (plans.length
        ? box(`<div class="schedule-result-list">${plans.map(renderPlan).join('')}</div>`)
        : empty(R('noPlans')))
    );
  }

  const action = String(args.action || '');
  const value = result.result;
  if (action === 'delete') {
    return (
      parameters +
      summary(R('actionCompleted', { action: actionLabel(action) })) +
      meta(param(R('scheduleId'), args.schedule_id), param(R('status'), R('deleted')))
    );
  }
  if (action === 'run' || action === 'stop') {
    return (
      parameters +
      summary(R('actionCompleted', { action: actionLabel(action) })) +
      (value ? box(renderRun(value)) : empty(R('noRun')))
    );
  }
  return (
    parameters +
    summary(R('actionCompleted', { action: actionLabel(action) })) +
    (value?.definition ? box(renderPlan(value)) : preBox(JSON.stringify(value || result, null, 2)))
  );
}
