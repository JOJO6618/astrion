#!/usr/bin/env node
// 跟随智能体：纯逻辑回归 + 接线点静态检查。
// 不含 Electron 运行覆盖：真实面板行为需要桌面壳实机验收。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '../..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');

const { followAiTarget } = await import(
  path.join(root, 'desktop-electron/src/browser/follow.js')
);

const profile = (on) => ({ followAi: () => on });
const tab = (followOn) => ({ id: 'tab-1', owner: 'owner-1', profile: profile(followOn) });

const cases = [];
const check = (name, fn) => {
  fn();
  cases.push(name);
};

check('跟随开启：切到 AI 操作的标签', () => {
  assert.equal(followAiTarget(tab(true), 'tab-2'), 'tab-1');
});

check('跟随开启：已是可见标签则不重复写', () => {
  assert.equal(followAiTarget(tab(true), 'tab-1'), null);
});

check('跟随关闭：不写可见标签', () => {
  assert.equal(followAiTarget(tab(false), 'tab-2'), null);
});

check('无 profile（内部页/异常数据）不跟随', () => {
  assert.equal(followAiTarget({ id: 'x', owner: 'o' }, 'tab-2'), null);
  assert.equal(followAiTarget(null, 'tab-2'), null);
});

// 接线点：AI 的四个标签入口都必须走 manager.follow，且跟随关闭时的不变量只在 follow.js 里判定。
const controller = read('desktop-electron/src/browser/ai/controller.js');
const followCalls = controller.match(/manager\.follow\(owner, tab\);/g) || [];
assert.equal(
  followCalls.length,
  4,
  `controller 应有四处 follow 调用（首建/操作/新建/切换），实际 ${followCalls.length}`
);
assert.ok(
  controller.includes('ai: true, select: false'),
  'AI 建标签必须不抢可见标签（select: false）'
);
cases.push('controller 接线：首建/操作/新建/切换四处跟随 + 建标签不抢焦点');

const index = read('desktop-electron/src/browser/index.js');
assert.ok(index.includes("else if (action === 'follow-ai') profile.setFollowAi(value === true)"), '缺少 follow-ai 命令');
assert.ok(
  index.includes('settings: { searchEngine: profile?.data.settings.searchEngine || \'\', followAi: profile?.data.settings.followAi !== false }'),
  '快照必须下发 followAi'
);
assert.ok(index.includes('const target = followAiTarget(tab, this.selected.get(owner));'), 'follow 必须走纯函数判定');
cases.push('主进程接线：follow-ai 命令 + 快照字段 + 统一判定');

assert.ok(
  /contentView\.addChildView\(tab\.view\);\n\s*\/\/ 挂载后再藏/.test(index),
  '挂载新视图后必须立即隐藏，由 layout 决定是否显示，避免恢复后台标签时露出一帧'
);
cases.push('主进程接线：挂载后立即隐藏（防一帧）');

const store = read('static/src/stores/nativeBrowser.ts');
assert.ok(store.includes("followAi: (state) => state.snapshot?.settings?.followAi !== false"), 'store 缺省必须视为开启');
assert.ok(store.includes("toggleFollow()"), 'store 缺切换动作');
cases.push('前端 store：默认开启 + 切换动作');

const toolbar = read('static/src/components/browser/BrowserToolbar.vue');
const downloadAt = toolbar.indexOf('class="nb-download-button"');
assert.ok(downloadAt > 0 && toolbar.indexOf('name="crosshair"') < downloadAt, '跟随按钮必须在下载按钮左侧');
cases.push('工具栏：跟随按钮在下载左侧');

console.log(`通过 ${cases.length} 项：`);
for (const name of cases) console.log(`- ${name}`);
