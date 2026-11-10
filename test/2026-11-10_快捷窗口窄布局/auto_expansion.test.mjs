import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import { canAutoExpandQuickDock } from '../../static/src/components/chat/quickdock/autoExpansion.ts';

function layout(chatWidth, dockWidth = 0, expandedWidth = '294px') {
  const chat = { getBoundingClientRect: () => ({ width: chatWidth }) };
  const dock = {
    parentElement: { querySelector: () => chat },
    getBoundingClientRect: () => ({ width: dockWidth })
  };
  return {
    dock,
    style: () => ({ getPropertyValue: () => expandedWidth })
  };
}

function withLayout(t, fixture) {
  const original = globalThis.getComputedStyle;
  globalThis.getComputedStyle = fixture.style;
  t.after(() => {
    if (original) globalThis.getComputedStyle = original;
    else delete globalThis.getComputedStyle;
  });
  return fixture.dock;
}

for (const [width, allowed] of [[893, false], [894, true], [895, true]]) {
  test(`收起时对话宽 ${width}px，自动展开许可为 ${allowed}`, t => {
    const dock = withLayout(t, layout(width));
    assert.equal(canAutoExpandQuickDock(dock), allowed);
  });
}

for (const occupied of [0, 80, 147, 294]) {
  test(`过渡中 dock 占 ${occupied}px，600px 边界不受动画影响`, t => {
    const dock = withLayout(t, layout(894 - occupied, occupied));
    assert.equal(canAutoExpandQuickDock(dock), true);
  });
  test(`过渡中 dock 占 ${occupied}px，599px 剩余宽度仍阻止展开`, t => {
    const dock = withLayout(t, layout(893 - occupied, occupied));
    assert.equal(canAutoExpandQuickDock(dock), false);
  });
}

test('侧栏或浏览器占位后读取实际对话宽度，忽略整个窗口宽度', t => {
  const dock = withLayout(t, layout(650));
  assert.equal(canAutoExpandQuickDock(dock), false);
});

test('完整列宽跟随 CSS 参数，避免列宽修改后判定失准', t => {
  const dock = withLayout(t, layout(950, 0, '360px'));
  assert.equal(canAutoExpandQuickDock(dock), false);
});

test('首帧未挂载或找不到对话容器时保持收起', () => {
  assert.equal(canAutoExpandQuickDock(null), false);
  assert.equal(canAutoExpandQuickDock({ parentElement: null }), false);
});

for (const [chatWidth, dockWidth, expandedWidth] of [
  [0, 0, '294px'],
  [NaN, 0, '294px'],
  [900, -1, '294px'],
  [900, 0, ''],
  [900, 0, '0px']
]) {
  test(`无效测量 ${chatWidth}/${dockWidth}/${expandedWidth} 不放行`, t => {
    const dock = withLayout(t, layout(chatWidth, dockWidth, expandedWidth));
    assert.equal(canAutoExpandQuickDock(dock), false);
  });
}

// 执行组件中的真实状态同步函数，检查设置、首次加载与手动展开的边界。
const component = readFileSync(
  new URL('../../static/src/components/chat/quickdock/QuickDock.vue', import.meta.url), 'utf8'
);
const script = component.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
const parsed = ts.createSourceFile('QuickDock.ts', script, ts.ScriptTarget.Latest, true);
const syncFunction = parsed.statements.find(
  node => ts.isFunctionDeclaration(node) && node.name?.text === 'syncAutoExpansion'
);
assert.ok(syncFunction, '未找到真实自动展开状态同步函数');
const syncCode = ts.transpileModule(syncFunction.getText(parsed), {
  compilerOptions: { target: ts.ScriptTarget.ES2022 }
}).outputText;

function stateFixture({ enabled = true, ready = true, collapsed = true, mobile = false,
  chatWidth = 650, dockWidth = 0 } = {}) {
  const fixture = layout(chatWidth, dockWidth);
  const context = {
    autoExpand: { value: enabled },
    settingsReady: { value: ready },
    userCollapsed: { value: collapsed },
    isMobileViewport: { value: mobile },
    dockRef: { value: fixture.dock },
    canAutoExpandQuickDock,
    quickDock: {}
  };
  Object.defineProperty(context.quickDock, 'userCollapsed', {
    get: () => context.userCollapsed.value,
    set: value => { context.userCollapsed.value = value; }
  });
  runInNewContext(syncCode, context);
  return { ...fixture, context, sync: preserve => context.syncAutoExpansion(preserve) };
}

test('新内容与切换回填触发时，窄布局仍保持收起', t => {
  const state = stateFixture({ collapsed: false });
  withLayout(t, state);
  state.sync();
  assert.equal(state.context.userCollapsed.value, true);
});

test('窄布局下开启自动展开设置也不展开', t => {
  const state = stateFixture();
  withLayout(t, state);
  state.sync(true);
  assert.equal(state.context.userCollapsed.value, true);
});

test('设置首次就绪仍检查宽度', t => {
  const state = stateFixture({ ready: false, chatWidth: 950 });
  withLayout(t, state);
  state.sync();
  assert.equal(state.context.userCollapsed.value, true);
  state.context.settingsReady.value = true;
  state.context.dockRef.value = layout(650).dock;
  state.sync(true);
  assert.equal(state.context.userCollapsed.value, true);
});

test('宽布局且自动展开已开启时允许展开', t => {
  const state = stateFixture({ chatWidth: 894 });
  withLayout(t, state);
  state.sync();
  assert.equal(state.context.userCollapsed.value, false);
});

test('关闭自动展开设置优先于足够的布局宽度', t => {
  const state = stateFixture({ enabled: false, collapsed: false, chatWidth: 950 });
  withLayout(t, state);
  state.sync(true);
  assert.equal(state.context.userCollapsed.value, true);
});

test('已展开的面板不会因设置回填时宽度不足而被强制关闭', t => {
  const state = stateFixture({ collapsed: false, chatWidth: 400, dockWidth: 294 });
  withLayout(t, state);
  state.sync(true);
  assert.equal(state.context.userCollapsed.value, false);
});

test('手机整层仍由用户打开，内部内容不受桌面占位宽度限制', () => {
  const state = stateFixture({ mobile: true, chatWidth: 375 });
  state.context.dockRef.value = null;
  state.sync();
  assert.equal(state.context.userCollapsed.value, false);
});
