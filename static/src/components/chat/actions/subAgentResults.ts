import { t } from '@/locales';

const tools = new Set([
  'create_sub_agent',
  'send_message_to_sub_agent',
  'get_sub_agent_status',
  'stop_sub_agent',
  'terminate_sub_agent',
  'answer_sub_agent_question'
]);
const escape = (value: unknown): string => {
  const div = document.createElement('div');
  div.textContent = String(value ?? '');
  return div.innerHTML;
};
const row = (label: string, value: unknown) =>
  value === undefined || value === null || value === ''
    ? ''
    : `<div><strong>${escape(label)}</strong>${escape(value)}</div>`;
const content = (value: unknown) =>
  value ? `<div class="tool-result-content scrollable"><pre>${escape(value)}</pre></div>` : '';

function statusLabel(status: string): string {
  if (status === 'running') return t('common.running');
  if (status === 'idle') return t('toolResults.status.idle');
  if (status === 'terminated') return t('toolResults.status.terminated');
  if (status === 'failed') return t('toolResults.status.failedMark');
  return status;
}

export function renderSubAgentResult(name: string, result: any, args: any): string | null {
  if (!tools.has(name)) return null;
  if (!result.success)
    return `<div class="tool-result-error">${escape(result.error || t('toolResults.status.failedMark'))}</div>`;
  if (name === 'get_sub_agent_status') {
    const agents = result.results;
    if (!agents?.length)
      return `<div class="tool-result-empty">${escape(t('toolResults.sentences.noSubAgentStatus'))}</div>`;
    return `<div class="sub-agent-status-list">${agents
      .map((agent: any) => {
        const header = `<div class="sub-agent-status-header">${escape(agent.name)}</div>`;
        const meta = `<div class="tool-result-meta">${row(t('toolResults.labels.status'), agent.found === false ? t('toolResults.status.notFound') : statusLabel(agent.status))}${row(t('toolResults.labels.task'), agent.summary)}${row(t('toolResults.labels.workingDir'), agent.working_dir)}</div>`;
        return `<div class="sub-agent-status-item">${header}${meta}${content(agent.error || agent.final_result)}</div>`;
      })
      .join('')}</div>`;
  }
  let meta = row(t('toolResults.labels.subAgent'), result.name || args.name);
  meta += row(t('toolResults.labels.questionId'), result.question_id || args.question_id);
  if (name === 'create_sub_agent') {
    const access = result.access_level || args.access_level;
    if (['working_dir_write', 'workspace_write', 'sandbox_write', 'full_access'].includes(access)) {
      meta += row(t('toolResults.labels.accessLevel'), t(`toolResults.access.${access}`));
    }
    if (args.intelligence)
      meta += row(
        t('personalization.subAgentModelTitle'),
        t(`personalization.subAgentIntelligence.${args.intelligence}`)
      );
    meta += row(t('toolResults.labels.workingDir'), result.working_dir);
    meta += row(t('toolResults.labels.task'), args.task);
  }
  return `<div class="tool-result-meta">${meta}</div>${content(result.message || args.answer)}`;
}
