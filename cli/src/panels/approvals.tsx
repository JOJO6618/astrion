// /approvals 面板：待审批处理（对齐 web 端 ToolApprovalPanel）
//   同一时刻最多一条待审批；显示工具名 + 完整参数预览（多行原样）
//   ←→ 选择操作（运行 / 拒绝 / 切换到无限制），Enter 执行后关闭；无待审批显示空态
import { RGBA, TextAttributes } from '@opentui/core';
import { T } from '../components';
import { PanelFrame } from './frame';
import type { PendingApprovalMock } from '../data';

const FG = RGBA.defaultForeground();
const DIM = TextAttributes.DIM;
const INVERSE = TextAttributes.INVERSE;

/** 操作项（顺序与文案对齐 web 端按钮：运行 / 拒绝 / 切换到无限制） */
export const APPROVAL_ACTIONS = ['运行', '拒绝', '切换到无限制'] as const;

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
      <PanelFrame title="工具审批   Esc 关闭" width={width}>
        <T fg={FG} attributes={DIM}>{'  暂无待审批操作'}</T>
      </PanelFrame>
    );
  }
  return (
    <PanelFrame title="工具审批   ←→ 选择操作   Enter 执行   Esc 关闭" width={width}>
      <T fg={FG}>{`  ${pending.toolLabel}  ${pending.toolName}`}</T>
      <T fg={FG} attributes={DIM}>{`  └ ${pending.previewTitle}`}</T>
      {pending.previewLines.map((l, i) => (
        <T key={i} fg={FG}>{`    ${l}`}</T>
      ))}
      <T fg={FG}>{' '}</T>
      <box flexDirection="row">
        <T fg={FG}>{'  '}</T>
        {APPROVAL_ACTIONS.map((label, i) => (
          <box key={label} flexDirection="row">
            {i > 0 ? <T fg={FG}>{'  '}</T> : null}
            <T fg={FG} attributes={i === sel ? INVERSE : DIM}>{label}</T>
          </box>
        ))}
      </box>
    </PanelFrame>
  );
}
