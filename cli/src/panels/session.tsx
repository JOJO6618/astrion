// /session 面板：交互区顶部显示工作区名称+路径，下方对话列表上下选择，Enter 加载
import { RGBA, TextAttributes } from '@opentui/core';
import { T } from '../components';
import { PanelFrame } from './frame';
import { SelectorList } from '../selector';
import { WORKSPACE, type SessionMock } from '../data';

const FG = RGBA.defaultForeground();
const DIM = TextAttributes.DIM;

export function SessionPanel({ sel, width, sessions }: { sel: number; width: number; sessions: SessionMock[] }) {
  const currentTitle = sessions.find((s) => s.current)?.title;
  return (
    <PanelFrame title="切换对话   ↑↓ 选择   Enter 加载   Esc 返回" width={width}>
      <T fg={FG} attributes={DIM}>{`  工作区  ${WORKSPACE.name}  ${WORKSPACE.path}`}</T>
      <SelectorList
        items={sessions.map((s) => ({ value: s.title, label: s.title, desc: s.when }))}
        sel={sel}
        current={currentTitle}
        width={width}
      />
    </PanelFrame>
  );
}
