// 通用上下选择器：交互区面板的复用组件（/session /mode /permission /env /network /model 等）
// 滚动逻辑 = Web 端同款「居中窗口」：可见 9 行，高亮移动到中间（第 5 行）后固定，再往下是列表滚动，
// 接近末尾时高亮恢复下移（scrollSkillSlashSelectionIntoMiddle 的 CLI 版）
import { RGBA, TextAttributes } from '@opentui/core';
import { T } from './components';
import { displayWidth, padEndWidth } from './width';

const FG = RGBA.defaultForeground();
const INVERSE = TextAttributes.INVERSE;

/** 交互区面板统一可见行数 */
export const MENU_VISIBLE_ROWS = 9;

/**
 * 居中窗口：返回列表起始下标。
 * sel 进入中间位（floor(9/2)=4，第 5 行）后窗口跟随滚动，两端 clamp。
 */
export function centeredWindowStart(count: number, sel: number, visible: number = MENU_VISIBLE_ROWS): number {
  if (count <= visible) return 0;
  const middle = Math.floor(visible / 2);
  return Math.min(Math.max(0, sel - middle), count - visible);
}

export interface SelectorItem {
  value: string;
  label: string;
  desc?: string;
}

/**
 * 上下选择器列表（纯列表，不含面板框；标题/提示行由调用方用 PanelFrame 组合）。
 * - sel 行整行 INVERSE 反色；当前生效项标 ●；列按显示宽度对齐（中西文混排不歪）
 * - label 列宽动态取当前 items 最长 label + 2 列边距（不硬编码——多语言文案长度不预设）
 */
export function SelectorList({
  items,
  sel,
  current,
  width,
}: {
  items: SelectorItem[];
  sel: number;
  current?: string;
  width: number;
}) {
  const start = centeredWindowStart(items.length, sel);
  const visible = items.slice(start, start + MENU_VISIBLE_ROWS);
  const labelColWidth = (items.length ? Math.max(...items.map((it) => displayWidth(it.label))) : 0) + 2;
  return (
    <>
      {visible.map((item, i) => {
        const idx = start + i;
        const isCurrent = item.value === current;
        const text = ` ${isCurrent ? '●' : ' '}  ${padEndWidth(item.label, labelColWidth)}${item.desc ?? ''}`;
        if (idx === sel) {
          return (
            <T key={item.value} fg={FG} attributes={INVERSE}>
              {padEndWidth(text, width)}
            </T>
          );
        }
        return (
          <T key={item.value} fg={FG}>
            {text}
          </T>
        );
      })}
    </>
  );
}
