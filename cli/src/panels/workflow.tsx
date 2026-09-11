// /workflow 面板：工作流激活/退出
//   ↑↓ 选择；Enter 激活选中工作流（已是当前激活则退出）；当前激活项标 ●
import { PanelFrame } from './frame';
import { SelectorList } from '../selector';
import type { WorkflowMock } from '../data';

export function WorkflowPanel({
  sel,
  width,
  workflows,
  active,
}: {
  sel: number;
  width: number;
  workflows: WorkflowMock[];
  active: string | null;
}) {
  return (
    <PanelFrame
      title={`工作流   ↑↓ 选择   Enter ${active ? '激活 / 退出当前' : '激活'}   Esc 返回`}
      width={width}
    >
      <SelectorList
        items={workflows.map((w) => ({ value: w.name, label: w.name, desc: `${w.desc}  ${w.stages} 阶段` }))}
        sel={sel}
        current={active ?? undefined}
        width={width}
      />
    </PanelFrame>
  );
}
