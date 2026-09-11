// /rewind 面板：版本回溯检查点列表，↑↓ 选择，Enter 回溯到选中检查点
import { PanelFrame } from './frame';
import { SelectorList } from '../selector';
import type { CheckpointMock } from '../data';

export function RewindPanel({ sel, width, checkpoints }: { sel: number; width: number; checkpoints: CheckpointMock[] }) {
  return (
    <PanelFrame title="版本回溯   ↑↓ 选择   Enter 回溯到此检查点   Esc 返回" width={width}>
      <SelectorList
        items={checkpoints.map((c) => ({
          value: c.id,
          label: c.summary,
          desc: `${c.when}  ${c.files} 个文件变更`,
        }))}
        sel={sel}
        width={width}
      />
    </PanelFrame>
  );
}
