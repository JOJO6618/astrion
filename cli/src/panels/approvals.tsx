// /approvals：单条审批；操作参数在左，真实审核状态与本次决策在右。
import { RGBA, TextAttributes } from '@opentui/core';
import { T } from '../components';
import { PanelFrame } from './frame';
import type { PendingApprovalMock } from '../data';
import { approvalActions, reviewProgressText } from '../approval';
import { t } from '../i18n';

const FG = RGBA.defaultForeground();
const DIM = TextAttributes.DIM;
const INVERSE = TextAttributes.INVERSE;

export function ApprovalsPanel({
  sel,
  width,
  pending,
}: {
  sel: number;
  width: number;
  pending: PendingApprovalMock | null;
}) {
  if (!pending) {
    return (
      <PanelFrame title={`${t('approval.title')}   ${t('approval.closeHint')}`} width={width}>
        <T fg={FG} attributes={DIM}>{`  ${t('approval.empty')}`}</T>
      </PanelFrame>
    );
  }
  const actions = approvalActions(pending);
  const fullAccess = pending.approvalType === 'full_access';
  const showAutoReview = pending.autoReviewRequired || (!fullAccess && pending.autoReviewProgress !== null);
  const progress = pending.autoReviewProgress;
  const reviewEnded = pending.autoReviewStatus === 'approved' || pending.autoReviewStatus === 'rejected';
  // 人工接管或取消可以结束审核，但不等于自动项批准/拒绝。
  const autoState = progress?.stage === 'done' && !reviewEnded ? 'done' : pending.autoReviewStatus ?? 'unknown';
  const title = t(fullAccess ? 'approval.fullAccessTitle' : 'approval.title');
  const contentWidth = Math.max(2, width - 4);
  const leftWidth = Math.max(1, Math.floor(contentWidth * 0.6));

  return (
    <PanelFrame title={`${title}   ${t(actions.length ? 'approval.hint' : 'approval.closeHint')}`} width={width}>
      <box flexDirection="row" width={contentWidth}>
        <box flexDirection="column" width={leftWidth} flexShrink={0}>
          <T fg={FG}>{`  ${pending.toolLabel}  ${pending.toolName}`}</T>
          <T fg={FG} attributes={DIM}>{`  └ ${pending.previewTitle}`}</T>
          {pending.previewLines.map((line, i) => (
            <T key={i} fg={FG}>{`    ${line}`}</T>
          ))}
        </box>
        <box flexDirection="column" width={contentWidth - leftWidth} paddingLeft={2}>
          {showAutoReview ? (
            <>
              <T fg={FG}>{t(`approval.auto.${autoState}`)}</T>
              {progress && (!reviewEnded || progress.stage === 'done') ? (
                <>
                  <T fg={FG} attributes={DIM}>{reviewProgressText(progress, t)}</T>
                  {progress.stage === 'run_command' && progress.command ? (
                    <T fg={FG} attributes={DIM}>{progress.command}</T>
                  ) : null}
                </>
              ) : null}
              {pending.autoReviewReason ? <T fg={FG}>{pending.autoReviewReason}</T> : null}
            </>
          ) : null}
          {fullAccess && (showAutoReview || pending.humanDecision) ? (
            <T fg={FG}>{t(`approval.human.${pending.humanDecision ?? 'pending'}`)}</T>
          ) : null}
          {pending.decisionPending ? <T fg={FG} attributes={DIM}>{t('approval.submitting')}</T> : null}
          {pending.status === 'approved' ? <T fg={FG}>{t('approval.ready')}</T> : null}
          {pending.status === 'rejected' ? <T fg={FG}>{t('approval.denied')}</T> : null}
          {pending.status !== 'pending' && pending.status !== 'approved' && pending.status !== 'rejected' ? (
            <T fg={FG}>{t('approval.ended')}</T>
          ) : null}
          {pending.reason && pending.reason !== pending.autoReviewReason ? <T fg={FG}>{pending.reason}</T> : null}
          {actions.length ? (
            <box flexDirection="row" marginTop={showAutoReview || pending.humanDecision ? 1 : 0}>
              {actions.map((action, i) => (
                <box key={action} flexDirection="row">
                  {i > 0 ? <T fg={FG}>{'  '}</T> : null}
                  <T fg={FG} attributes={i === sel ? INVERSE : DIM}>{t(`approval.${action}`)}</T>
                </box>
              ))}
            </box>
          ) : null}
        </box>
      </box>
    </PanelFrame>
  );
}
