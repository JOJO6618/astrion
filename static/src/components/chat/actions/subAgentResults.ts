// 子智能体工具（主侧 6 个）的结果显示。meta 只放调用参数，content 只放结果（§5.6）；
// 失败原因由 renderEnhancedToolResult 统一追加，这里失败时仍保留参数，便于看清是对谁操作失败。

import { t } from '@/locales';
import {
  box,
  empty,
  escapeHtml,
  lv,
  meta,
  param,
  pendingStatus,
  seconds,
  summary
} from './toolResultHtml';

const tools = new Set([
  'create_sub_agent',
  'send_message_to_sub_agent',
  'get_sub_agent_status',
  'stop_sub_agent',
  'terminate_sub_agent',
  'answer_sub_agent_question'
]);

const ACCESS_LEVELS = ['working_dir_write', 'workspace_write', 'sandbox_write', 'full_access'];
const INTELLIGENCE_LEVELS = ['same', 'high', 'medium', 'low'];

const L = (key: string) => t(`toolResults.labels.${key}`);

function statusLabel(status: string): string {
  if (status === 'running') return t('common.running');
  if (status === 'idle') return t('toolResults.status.idle');
  if (status === 'terminated') return t('toolResults.status.terminated');
  if (status === 'failed') return t('toolResults.status.failedMark');
  return status;
}

function renderStatusList(result: any, args: any): string {
  const names: string[] = Array.isArray(args.names) ? args.names : [];
  const head = meta(param(L('names'), names.join(', ')));
  if (!result.success) return head;
  const agents = Array.isArray(result.results) ? result.results : [];
  if (!agents.length) return head + empty(t('toolResults.sentences.noSubAgentStatus'));

  const items = agents
    .map((agent: any) => {
      const state =
        agent.found === false ? t('toolResults.status.notFound') : statusLabel(agent.status);
      let entry = '<div class="sub-agent-status-item">';
      entry += `<div class="sub-agent-status-header"><span class="sub-agent-status-name">${escapeHtml(agent.name)}</span><span class="sub-agent-status-state">${escapeHtml(state || '')}</span></div>`;
      if (agent.summary) {
        entry += `<div class="sub-agent-status-detail">${escapeHtml(lv(L('task'), agent.summary))}</div>`;
      }
      if (agent.working_dir) {
        entry += `<div class="sub-agent-status-detail">${escapeHtml(lv(L('workingDir'), agent.working_dir))}</div>`;
      }
      const output = agent.error || agent.final_result;
      if (output) {
        entry += `<pre class="sub-agent-status-result${agent.error ? ' is-error' : ''}">${escapeHtml(String(output).replace(/\s+$/, ''))}</pre>`;
      }
      return entry + '</div>';
    })
    .join('');
  return head + box(`<div class="sub-agent-status-list">${items}</div>`);
}

export function renderSubAgentResult(name: string, result: any, args: any): string | null {
  if (!tools.has(name)) return null;
  if (name === 'get_sub_agent_status') return renderStatusList(result, args);

  const agentName = param(L('subAgent'), args.name || result.name);
  let params = '';
  if (name === 'create_sub_agent') {
    const access = args.access_level || result.access_level;
    const intelligence = args.intelligence;
    params = meta(
      agentName,
      param(L('summary'), args.summary),
      INTELLIGENCE_LEVELS.includes(intelligence)
        ? param(L('intelligence'), t(`personalization.subAgentIntelligence.${intelligence}`))
        : '',
      ACCESS_LEVELS.includes(access)
        ? param(L('accessLevel'), t(`toolResults.access.${access}`))
        : '',
      param(L('workingDir'), args.working_dir || result.working_dir),
      param(L('timeout'), seconds(args.timeout_seconds)),
      param(L('task'), args.task, { block: true }),
      pendingStatus(result)
    );
  } else if (name === 'send_message_to_sub_agent') {
    params = meta(
      agentName,
      param(L('timeout'), seconds(args.timeout_seconds)),
      param(L('message'), args.message, { block: true }),
      pendingStatus(result)
    );
  } else if (name === 'answer_sub_agent_question') {
    params = meta(
      param(L('questionId'), args.question_id || result.question_id),
      param(L('answer'), args.answer, { block: true }),
      pendingStatus(result)
    );
  } else {
    params = meta(agentName, pendingStatus(result));
  }

  const message = result.success && typeof result.message === 'string' ? result.message : '';
  return params + summary(message);
}
