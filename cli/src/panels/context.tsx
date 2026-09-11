// /context 面板：交互区只读展示上下文用量统计
// 数据：ContextStats 数字原始值（token_update 事件 + 打开时查询双源刷新）；
// 百分比算法对齐 web InputComposer（开自动深度压缩用触发阈值，否则模型上下文窗口）。
import { RGBA, TextAttributes } from '@opentui/core';
import { T } from '../components';
import { PanelFrame } from './frame';
import { contextUsageLimitFor, formatCompactTokens, type ContextStats } from '../data';
import { t } from '../i18n';

const FG = RGBA.defaultForeground();
const DIM = TextAttributes.DIM;

export function ContextPanel({ width, stats, model }: { width: number; stats: ContextStats; model: string }) {
  const limit = contextUsageLimitFor(model);
  const pct =
    limit > 0 ? Math.max(0, Math.min(100, Math.round((stats.currentTokens / limit) * 100))) : 0;
  const hitRate =
    stats.totalInput > 0 ? `${Math.round((stats.cacheInput / stats.totalInput) * 100)}%` : '—';
  const currentText =
    limit > 0
      ? `${formatCompactTokens(stats.currentTokens)} / ${formatCompactTokens(limit)}（${pct}%）`
      : `${formatCompactTokens(stats.currentTokens)} / —`;

  const rows: Array<[string, string]> = [
    [t('context.current'), currentText],
    [t('context.totalInput'), formatCompactTokens(stats.totalInput)],
    [t('context.totalOutput'), formatCompactTokens(stats.totalOutput)],
    [t('context.cacheInput'), formatCompactTokens(stats.cacheInput)],
    [t('context.cacheHitRate'), hitRate],
  ];
  return (
    <PanelFrame title={t('context.title')} width={width}>
      {rows.map(([label, value]) => (
        <box key={label} flexDirection="row">
          <T fg={FG}>{`  ${label}  `}</T>
          <T fg={FG} attributes={DIM}>
            {value}
          </T>
        </box>
      ))}
    </PanelFrame>
  );
}
