// /context 面板：交互区只读展示上下文用量统计
import { RGBA, TextAttributes } from '@opentui/core';
import { T } from '../components';
import { PanelFrame } from './frame';
import { CONTEXT_STATS } from '../data';

const FG = RGBA.defaultForeground();
const DIM = TextAttributes.DIM;

export function ContextPanel({ width }: { width: number }) {
  // 标签列对齐：最长 5 个汉字，短的补全角空格
  const rows: Array<[string, string]> = [
    ['当前上下文', `${CONTEXT_STATS.used} / ${CONTEXT_STATS.total}（${CONTEXT_STATS.percent}%）`],
    ['累计输入　', CONTEXT_STATS.totalInput],
    ['累计输出　', CONTEXT_STATS.totalOutput],
    ['缓存输入　', CONTEXT_STATS.cacheInput],
    ['缓存命中率', CONTEXT_STATS.cacheHitRate],
  ];
  return (
    <PanelFrame title="上下文用量   Esc 关闭" width={width}>
      {rows.map(([label, value]) => (
        <box key={label} flexDirection="row">
          <T fg={FG}>{`  ${label}  `}</T>
          <T fg={FG} attributes={DIM}>{value}</T>
        </box>
      ))}
    </PanelFrame>
  );
}
