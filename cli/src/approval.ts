// 工具审批协议与纯状态处理；双重审批以服务端状态为准。
import type { PendingApprovalMock } from './data';

export type ApprovalDecision = 'approved' | 'rejected';
export type AutoReviewStatus = 'pending' | 'reviewing' | ApprovalDecision;

/** decision 接口和 tool_approval_required 共享的后端条目。 */
export interface ToolApprovalItem {
  approval_id: string;
  tool_name?: string;
  arguments?: unknown;
  approval_type?: string;
  status?: string;
  auto_review_required?: boolean;
  auto_review_status?: AutoReviewStatus;
  human_decision?: ApprovalDecision | null;
  auto_review_reason?: string | null;
  reason?: string | null;
}

export interface AutoReviewProgress {
  stage: string;
  message?: string;
  round?: number;
  command?: string;
  decision?: string;
}

export const APPROVAL_ACTIONS = ['run', 'reject'] as const;
export type ApprovalAction = (typeof APPROVAL_ACTIONS)[number];

/** 已批准人工项后仍可拒绝尚未终结的申请；不能重复批准或覆盖拒绝。 */
export function approvalActions(pending: PendingApprovalMock): readonly ApprovalAction[] {
  if (pending.decisionPending || pending.status !== 'pending'
    || pending.humanDecision === 'rejected' || pending.autoReviewStatus === 'rejected') return [];
  return pending.humanDecision === 'approved' ? ['reject'] : APPROVAL_ACTIONS;
}

/** 合并同 ID 快照；延迟到达的 pending/null 快照不能撤销已收到的裁决。 */
export function mergeApprovalState(item: ToolApprovalItem, previous?: PendingApprovalMock | null) {
  let status = item.status ?? previous?.status ?? 'pending';
  if (previous && previous.status !== 'pending' && status === 'pending') status = previous.status;
  let humanDecision = item.human_decision ?? previous?.humanDecision ?? null;
  if (previous?.humanDecision === 'rejected') humanDecision = 'rejected';
  let autoReviewStatus = item.auto_review_status ?? previous?.autoReviewStatus ?? null;
  const priorAuto = previous?.autoReviewStatus;
  if (priorAuto === 'approved' || priorAuto === 'rejected'
    || (priorAuto === 'reviewing' && autoReviewStatus === 'pending')) autoReviewStatus = priorAuto;
  return {
    approvalType: item.approval_type ?? previous?.approvalType ?? 'sandbox_write',
    status,
    autoReviewRequired: item.auto_review_required ?? previous?.autoReviewRequired ?? false,
    autoReviewStatus,
    humanDecision,
    autoReviewReason: item.auto_review_reason ?? previous?.autoReviewReason ?? '',
    reason: item.reason ?? previous?.reason ?? '',
    autoReviewProgress: previous?.autoReviewProgress ?? null,
    decisionPending: previous?.decisionPending ?? false,
  };
}

/** 单次完全访问预览只展示操作参数，不额外展示执行上下文。 */
const FULL_ACCESS_CONTEXT_KEYS = new Set([
  'executor', 'executor_name', 'executed_by', 'agent_name',
  'cwd', 'working_dir', 'working_directory', 'work_dir', 'workingDirectory',
  'execution_environment', 'execution_env', 'exec_env', 'sandbox_mode',
  'execution_mode', 'execution_method', 'executionMode',
]);

export function approvalArguments(args: unknown, approvalType: string): unknown {
  if (approvalType !== 'full_access' || !args || typeof args !== 'object' || Array.isArray(args)) return args;
  return Object.fromEntries(Object.entries(args).filter(([key]) => !FULL_ACCESS_CONTEXT_KEYS.has(key)));
}

export function reviewProgressText(progress: AutoReviewProgress, tr: (key: string) => string): string {
  if (progress.message) return progress.message;
  if (progress.stage === 'model_call') {
    return `${tr('approval.auto.modelCall')}${typeof progress.round === 'number' ? ` ${progress.round}` : ''}`;
  }
  if (progress.stage === 'run_command') return tr('approval.auto.checking');
  if (progress.stage === 'start') return tr('approval.auto.reviewing');
  if (progress.stage === 'done') return tr('approval.auto.done');
  return progress.stage;
}
