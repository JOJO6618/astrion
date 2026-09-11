// /path 面板：路径授权按「可读可写 / 可读」两组分页显示
//   ←→ 切换当前显示的分组（路径归属在添加时由当前组决定，不做行内权限切换）
//   ↑↓ 选择行；输入框为空时 ⌫/Delete 删除选中行；输入栏输入路径 Enter 添加到当前组
import { RGBA, TextAttributes } from '@opentui/core';
import { T } from '../components';
import { PanelFrame } from './frame';
import { centeredWindowStart, MENU_VISIBLE_ROWS } from '../selector';
import { padEndWidth } from '../width';
import { PATH_ACCESS_LABEL, type PathAccess, type PathAuth } from '../data';

const FG = RGBA.defaultForeground();
const DIM = TextAttributes.DIM;
const INVERSE = TextAttributes.INVERSE;

const GROUPS: PathAccess[] = ['rw', 'ro'];

/** 组标签行：两个分组平铺（固定间距、不定宽——多语言文案长度不预设），当前组文字反色 */
function GroupTabs({ group }: { group: PathAccess }) {
  return (
    <box flexDirection="row">
      <T fg={FG}>{'  '}</T>
      {GROUPS.map((g, i) => (
        <box key={g} flexDirection="row" flexShrink={0}>
          {i > 0 ? <T fg={FG}>{'  '}</T> : null}
          <T fg={FG} attributes={g === group ? INVERSE : DIM}>{PATH_ACCESS_LABEL[g]}</T>
        </box>
      ))}
    </box>
  );
}

export function PathsPanel({
  sel,
  width,
  pathAuths,
  group,
}: {
  sel: number;
  width: number;
  pathAuths: PathAuth[];
  group: PathAccess;
}) {
  const rows = pathAuths.filter((p) => p.access === group);
  const start = centeredWindowStart(rows.length, sel);
  const visible = rows.slice(start, start + MENU_VISIBLE_ROWS);
  return (
    <PanelFrame title="路径授权   ←→ 切换分组   ↑↓ 选择   ⌫ 删除   输入路径 Enter 添加   Esc 返回" width={width}>
      <GroupTabs group={group} />
      {rows.length === 0 ? (
        <T fg={FG} attributes={DIM}>{'  该分组暂无路径，在下方输入栏输入路径后 Enter 添加'}</T>
      ) : null}
      {visible.map((p, i) => {
        const idx = start + i;
        const text = `  ${p.path}`;
        if (idx === sel) {
          return (
            <T key={`${p.path}-${idx}`} fg={FG} attributes={INVERSE}>
              {padEndWidth(text, width)}
            </T>
          );
        }
        return (
          <T key={`${p.path}-${idx}`} fg={FG}>
            {text}
          </T>
        );
      })}
    </PanelFrame>
  );
}
