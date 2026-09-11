// 交互区面板框架件：面板外框（分隔线 + 标题行）与通用行渲染
// 独立成文件是为了让 menu.tsx 与各 panels/* 共用而不产生循环引用
import { RGBA, TextAttributes } from '@opentui/core';
import { T } from '../components';
import { padEndWidth } from '../width';

const FG = RGBA.defaultForeground();
const DIM = TextAttributes.DIM;
const INVERSE = TextAttributes.INVERSE;

/** 面板外框：顶部一条分隔线 + DIM 标题行 + 内容。整体位于输入栏上方的交互区。 */
export function PanelFrame({ title, width, children }: { title: string; width: number; children: React.ReactNode }) {
  return (
    <box flexDirection="column" flexShrink={0} paddingLeft={3} paddingRight={1}>
      <T fg={FG} attributes={DIM}>{'─'.repeat(Math.max(8, width - 4))}</T>
      <T fg={FG} attributes={DIM}>{title}</T>
      {children}
    </box>
  );
}

/** 通用行：selected 时整行反色（亮暗终端自适应），否则可选 DIM */
export function Line({ text, selected, dim, width }: { text: string; selected?: boolean; dim?: boolean; width: number }) {
  if (selected) {
    return (
      <T fg={FG} attributes={INVERSE}>
        {padEndWidth(text, width)}
      </T>
    );
  }
  return (
    <T fg={FG} attributes={dim ? DIM : 0}>
      {text}
    </T>
  );
}
