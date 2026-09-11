// /help 面板：快捷键一览（只读，↑↓ 滚动）
import { RGBA, TextAttributes } from '@opentui/core';
import { T } from '../components';
import { PanelFrame } from './frame';
import { padEndWidth } from '../width';
import { HELP_LINES } from '../data';

const FG = RGBA.defaultForeground();
const DIM = TextAttributes.DIM;

const KEY_COL_WIDTH = 12;

export function HelpPanel({ sel, width }: { sel: number; width: number }) {
  // sel 在此面板作为滚动偏移（无选中态）
  const visible = HELP_LINES.slice(sel, sel + 9);
  return (
    <PanelFrame title="帮助与快捷键   ↑↓ 滚动   Esc 关闭" width={width}>
      {visible.map(([k, desc], i) =>
        k === '' ? (
          <T key={`blank-${i}`} fg={FG}>{' '}</T>
        ) : (
          <box key={k} flexDirection="row">
            <T fg={FG}>{`  ${padEndWidth(k, KEY_COL_WIDTH)}`}</T>
            <T fg={FG} attributes={DIM}>{desc}</T>
          </box>
        ),
      )}
    </PanelFrame>
  );
}

/** help 面板可滚动的最大偏移（供键盘循环计算） */
export function helpMaxOffset(): number {
  return Math.max(0, HELP_LINES.length - 9);
}
