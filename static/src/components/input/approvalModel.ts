export type ReviewStatus = 'pending' | 'reviewing' | 'approved' | 'rejected';
export type ApprovalDecision = 'approved' | 'rejected';
export type ReviewKind = 'auto' | 'goal' | 'workflow';

export interface ApprovalProgress {
  stage?: string;
  message?: string;
  command?: string;
  decision?: ApprovalDecision;
}

export interface ToolApproval {
  approval_id: string;
  tool_name: string;
  approval_type?: string;
  status?: string;
  arguments?: Record<string, unknown>;
  preview?: Record<string, any>;
  reason?: string;
  auto_review_required?: boolean;
  auto_review_status?: ReviewStatus;
  human_decision?: ApprovalDecision | null;
  auto_review_progress?: ApprovalProgress[];
  [key: string]: unknown;
}

export interface ApprovalReviewRecord {
  id: string;
  kind: ReviewKind;
  approval_id?: string;
  progress: ApprovalProgress[];
  decision?: ApprovalDecision;
  final_decision?: ApprovalDecision;
  reason?: string;
  tool_name?: string;
  approval?: ToolApproval;
}

export const isFullAccessApproval = (item?: ToolApproval | null) =>
  item?.approval_type === 'full_access';

// Legacy automatic approvals omit auto_review_required. The permission mode is
// only a fallback; an explicit server value always wins.
export const normalizeApproval = (
  incoming: ToolApproval,
  previous?: ToolApproval,
  automaticMode = false
): ToolApproval => ({
  ...previous,
  ...incoming,
  status:
    previous?.status && ['approved', 'rejected', 'expired'].includes(previous.status)
      ? previous.status
      : (incoming.status ?? previous?.status),
  auto_review_required:
    incoming.auto_review_required ?? previous?.auto_review_required ?? automaticMode,
  // Human decisions and terminal reviews cannot be undone by an older pending
  // snapshot returned while a decision POST or progress event was in flight.
  human_decision:
    previous?.human_decision === 'rejected'
      ? 'rejected'
      : (incoming.human_decision ?? previous?.human_decision ?? null),
  auto_review_status:
    previous?.auto_review_status === 'approved' ||
    previous?.auto_review_status === 'rejected' ||
    (previous?.auto_review_status === 'reviewing' && incoming.auto_review_status === 'pending')
      ? previous.auto_review_status
      : (incoming.auto_review_status ?? previous?.auto_review_status),
  auto_review_progress: incoming.auto_review_progress?.length
    ? incoming.auto_review_progress
    : (previous?.auto_review_progress ?? [])
});

export const needsHumanDecision = (item?: ToolApproval | null) =>
  !!item && !item.human_decision && item.auto_review_status !== 'rejected';

export const progressText = (progress: ApprovalProgress) =>
  [progress.message, progress.command].filter(Boolean).join('\n') || progress.stage || '';

export const renderApprovalProgress = (
  progress: ApprovalProgress,
  translate: (key: string) => string
): string => {
  if (progress.message || progress.command)
    return [progress.message, progress.command].filter(Boolean).join('\n');
  const keys: Record<string, string> = {
    start: 'start',
    model_call: 'modelCall',
    run_command: 'runCommand',
    done: 'complete'
  };
  const key = keys[progress.stage || ''];
  return key ? translate(`shell.reviewStages.${key}`) : '';
};

export const statusFromProgress = (
  progress: ApprovalProgress,
  previous?: ReviewStatus
): ReviewStatus => {
  if (progress.decision === 'approved' || progress.decision === 'rejected')
    return progress.decision;
  if (progress.stage === 'approved' || progress.stage === 'rejected') return progress.stage;
  if (previous === 'approved' || previous === 'rejected') return previous;
  return progress.stage === 'pending' ? 'pending' : 'reviewing';
};

// Progress comes only from runtime events. No simulated stages or timers.
export const updateReviewRecords = (
  records: ApprovalReviewRecord[],
  kind: ReviewKind,
  data: { approval_id?: string; progress?: ApprovalProgress } & ApprovalProgress
): ApprovalReviewRecord[] => {
  const progress = data.progress || data;
  const approvalId = data.approval_id;
  const last = [...records]
    .reverse()
    .find((record) => record.kind === kind && record.approval_id === approvalId);
  const record =
    last && (progress.stage !== 'start' || approvalId)
      ? { ...last, progress: [...last.progress] }
      : ({
          id: `${kind}-${approvalId || Date.now()}-${records.length}`,
          kind,
          approval_id: approvalId,
          progress: []
        } as ApprovalReviewRecord);
  record.progress.push({ ...progress });
  record.progress = record.progress.slice(-100);
  if (progress.decision === 'approved' || progress.decision === 'rejected')
    record.decision = progress.decision;
  return (
    last && record.id === last.id
      ? records.map((entry) => (entry.id === record.id ? record : entry))
      : [...records, record]
  ).slice(-30);
};
