import assert from 'node:assert/strict';
import { test } from 'node:test';
import { QUICK_DOCK_MIN_CHAT_WIDTH } from '../../static/src/components/chat/quickdock/autoExpansion.ts';
import {
  NARROW_MIN_CHAT_WIDTH,
  decideNarrowCollapse,
  getRightPanelsFootprint,
  hasRoomFor
} from '../../static/src/components/chat/quickdock/narrowLayout.ts';

const base = {
  chatWidth: 200,
  quickGroupOpen: false,
  quickGroupForced: false,
  rightPanelsOpen: false,
  rightPanelsForced: false
};

test('宽度足够时不折叠（299/300/301 边界）', () => {
  const open = { ...base, quickGroupOpen: true, rightPanelsOpen: true };
  assert.equal(decideNarrowCollapse({ ...open, chatWidth: 299 }), 'quick-group');
  assert.equal(decideNarrowCollapse({ ...open, chatWidth: 300 }), null);
  assert.equal(decideNarrowCollapse({ ...open, chatWidth: 301 }), null);
});

test('先收快捷面板组，再收右侧面板组', () => {
  const both = { ...base, quickGroupOpen: true, rightPanelsOpen: true };
  assert.equal(decideNarrowCollapse(both), 'quick-group');
  assert.equal(decideNarrowCollapse({ ...both, quickGroupOpen: false }), 'right-panels');
});

test('强制保留的组被跳过，轮到下一组', () => {
  const both = { ...base, quickGroupOpen: true, rightPanelsOpen: true };
  assert.equal(decideNarrowCollapse({ ...both, quickGroupForced: true }), 'right-panels');
  assert.equal(
    decideNarrowCollapse({ ...both, quickGroupForced: true, rightPanelsForced: true }),
    null
  );
});

test('没有展开的面板、或宽度未测得时不动作', () => {
  assert.equal(decideNarrowCollapse(base), null);
  assert.equal(decideNarrowCollapse({ ...base, chatWidth: 0, quickGroupOpen: true }), null);
  assert.equal(decideNarrowCollapse({ ...base, chatWidth: NaN, quickGroupOpen: true }), null);
});

test('无 DOM 时 hasRoomFor 视为放不下（保守，避免首帧自动展开）', () => {
  assert.equal(hasRoomFor(0), false);
});

test('右侧面板占位按 600 上限加手柄计算', () => {
  assert.equal(getRightPanelsFootprint(undefined), 426);
  assert.equal(getRightPanelsFootprint(900), 606);
});

test('折叠阈值与自动展开阈值一致', () => {
  assert.equal(NARROW_MIN_CHAT_WIDTH, QUICK_DOCK_MIN_CHAT_WIDTH);
});
