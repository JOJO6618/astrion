// 回归：页面回收挂起标签（view 为 null）时，窗口底色同步不得抛错。
// 触发过的事故：深色主题切换 → applySurfaceColor → setBrowserSurfaceColor 里
// `tab.view.webContents` 直接取空 → 主进程 TypeError，底色缓存与落盘中断。

import { setBrowserSurfaceColor } from '../../desktop-electron/src/browser/geometry.js';

const results = [];
const check = (name, fn) => {
  try { fn(); results.push(`PASS ${name}`); } catch (error) { results.push(`FAIL ${name}: ${error.message}`); }
};
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const fakeManager = (surfaceColor, tabs) => ({
  surfaceColor,
  window: { setBackgroundColor() {}, contentView: { setBackgroundColor() {} } },
  tabs: new Map(tabs.map((tab, index) => [`tab-${index}`, tab]))
});
const liveView = painted => ({ webContents: { isDestroyed: () => false }, setBackgroundColor: color => painted.push(color) });
const destroyedView = painted => ({ webContents: { isDestroyed: () => true }, setBackgroundColor: color => painted.push(color) });

check('挂起标签不抛错且其余标签正常着色', () => {
  const painted = [];
  const manager = fakeManager('#faf9f5', [{ view: null }, { view: liveView(painted) }, { view: destroyedView(painted) }]);
  setBrowserSurfaceColor(manager, '#1a1a1a');
  assert(manager.surfaceColor === '#1a1a1a', 'surfaceColor 未更新');
  assert(painted.length === 1 && painted[0] === '#1a1a1a', '只有存活视图应着色');
});

check('全部标签挂起也不抛错', () => {
  const manager = fakeManager('#faf9f5', [{ view: null }, { view: undefined }]);
  setBrowserSurfaceColor(manager, '#1a1a1a');
  assert(manager.surfaceColor === '#1a1a1a', 'surfaceColor 未更新');
});

check('同色提前返回，不触碰标签', () => {
  let touched = 0;
  const manager = fakeManager('#faf9f5', [{
    view: { webContents: { isDestroyed: () => false }, setBackgroundColor: () => { touched += 1; } }
  }]);
  setBrowserSurfaceColor(manager, '#faf9f5');
  assert(touched === 0, '同色不应遍历着色');
});

check('非法颜色忽略', () => {
  const manager = fakeManager('#faf9f5', [{ view: null }]);
  setBrowserSurfaceColor(manager, 'red');
  setBrowserSurfaceColor(manager, '#GGGGGG');
  assert(manager.surfaceColor === '#faf9f5', '非法颜色不应生效');
});

const failed = results.filter(row => row.startsWith('FAIL'));
console.log(results.join('\n'));
if (failed.length) { console.error(`\n${failed.length} 项失败`); process.exit(1); }
console.log(`\n全部 ${results.length} 项通过`);
