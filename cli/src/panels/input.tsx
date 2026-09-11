// 通用面板小组件：输入型面板（/rename /export）与确认面板（/delete）
// 输入型：交互区显示说明（含当前值），实际输入在下方输入栏（placeholder 已由 menuState 覆盖），Enter 提交
// 确认型：破坏性操作二次确认，Enter 确认 / Esc 取消
import { RGBA, TextAttributes } from '@opentui/core';
import { T } from '../components';
import { PanelFrame } from './frame';

const FG = RGBA.defaultForeground();
const DIM = TextAttributes.DIM;

export function InputPanel({ title, hint, width }: { title: string; hint: string; width: number }) {
  return (
    <PanelFrame title={`${title}   Enter 确认   Esc 返回`} width={width}>
      <T fg={FG} attributes={DIM}>{`  ${hint}`}</T>
    </PanelFrame>
  );
}

export function ConfirmPanel({ title, hint, width }: { title: string; hint: string; width: number }) {
  return (
    <PanelFrame title={`${title}   Enter 确认   Esc 取消`} width={width}>
      <T fg={FG} attributes={DIM}>{`  ${hint}`}</T>
    </PanelFrame>
  );
}
