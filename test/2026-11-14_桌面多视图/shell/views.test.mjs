// ViewManager 与纯逻辑单测（伪 view/window，无需 Electron）。
// 运行：node --test test/2026-11-14_桌面多视图/shell/
import test from 'node:test';
import assert from 'node:assert/strict';
import { ViewManager } from '../../../desktop-electron/src/views/manager.js';
import { acceptSnapshot, buildViewUrl, chooseEvictions, planReconcile, warmupOrder } from '../../../desktop-electron/src/views/plan.js';
import { mainContentBounds } from '../../../desktop-electron/src/views/layout.js';

const ORIGIN = 'http://127.0.0.1:1';
const conv = (key, extra = {}) => ({ key, kind: 'conv', conversation_kind: 'agent', conversation_id: `conv_${key}`, workspace_id: 'w1', placement: 'main', ...extra });
const snap = (revision, tabs, active_key) => ({ version: 3, revision, tabs, active_key });

function fakeView(url = '') {
  const wc = { sent: [], destroyed: false, focused: 0, url, send(channel, data) { this.sent.push([channel, data]); }, isDestroyed() { return this.destroyed; }, close() { this.destroyed = true; }, focus() { this.focused++; }, loadURL(u) { this.url = u; return Promise.resolve(); }, listeners: {}, on(ev, fn) { this.listeners[ev] = fn; } };
  return { webContents: wc, visible: false, bounds: null, setVisible(v) { this.visible = v; }, setBounds(b) { this.bounds = b; }, setBackgroundColor() {} };
}
function fixture(opts = {}) {
  const timers = []; let now = 1000;
  const window = { children: [], destroyed: false, focusedFlag: false, isFocused() { return this.focusedFlag; }, isDestroyed() { return this.destroyed; }, getContentBounds: () => ({ width: 1000, height: 800 }),
    contentView: { addChildView(v) { window.children.push(v); }, removeChildView(v) { window.children = window.children.filter(c => c !== v); } } };
  const chromeView = fakeView('chrome');
  const created = [], destroyed = [], saved = [], moved = [], detachedWins = new Map();
  const fakeWindow = (key, callbacks) => {
    const w = { key, callbacks, children: [], destroyedFlag: false, title: '', visible: true, bounds: { width: 600, height: 500 },
      isDestroyed() { return this.destroyedFlag; }, destroy() { this.destroyedFlag = true; }, getContentBounds() { return this.bounds; },
      hide() { this.visible = false; }, show() { this.visible = true; this.shownCalls = (this.shownCalls || 0) + 1; }, isVisible() { return this.visible; }, focus() { this.focusCalls = (this.focusCalls || 0) + 1; }, setPosition(x, y) { for (const v of [x, y]) if (!Number.isInteger(v) || Object.is(v, -0)) throw new TypeError('Error processing argument: conversion failure from ' + v); this.pos = [x, y]; }, astrionRemember() { this.remembered = (this.remembered || 0) + 1; }, setTitle(t) { this.title = t; },
      contentView: { addChildView(v) { w.children.push(v); }, removeChildView(v) { w.children = w.children.filter(c => c !== v); } } };
    return w;
  };
  const manager = new ViewManager({
    origin: ORIGIN, window, chromeView,
    createView: ({ key, url, legacy }) => { const v = fakeView(url); v.key = key; v.legacy = legacy; created.push(key); return v; },
    hooks: { onViewDestroyed: entry => destroyed.push(entry.key), onViewMoved: (entry, win) => moved.push([entry.key, entry.placement, win]) },
    createDetachedWindow: ({ key, point, offset, focus }, callbacks) => { const w = fakeWindow(key, callbacks); w.point = point; w.offset = offset; w.focusOnCreate = focus; detachedWins.set(key, w); return w; },
    getCursor: () => ({ x: 7, y: 8 }),
    loadRecency: () => opts.recency || {}, saveRecency: v => saved.push(v),
    timers: { set: (fn, ms) => { const t = { fn, ms, off: false }; timers.push(t); return t; }, clear: t => { t.off = true; } },
    now: () => ++now, warmupStep: 7777, ...opts.manager
  });
  const run = () => { const t = timers.find(x => !x.off); if (!t) return false; t.off = true; t.fn(); return true; };
  const fire = (ms) => { const t = timers.findLast(x => !x.off && x.ms === ms); if (!t) return false; t.off = true; t.fn(); return true; };
  return { manager, window, chromeView, created, destroyed, saved, timers, run, fire, moved, detachedWins };
}
const tabsOf = (n) => Array.from({ length: n }, (_, i) => conv(`t${i}`));

test('acceptSnapshot: 仅接受更高 revision 且结构合法', () => {
  assert.equal(acceptSnapshot(-1, snap(0, [], '')), true);
  assert.equal(acceptSnapshot(5, snap(5, [])), false);
  assert.equal(acceptSnapshot(5, snap(4, [])), false);
  assert.equal(acceptSnapshot(5, snap(6, [{ nokey: 1 }])), false);
  assert.equal(acceptSnapshot(5, null), false);
  assert.equal(acceptSnapshot(5, { revision: 6, tabs: 'x' }), false);
});

test('buildViewUrl: 各类标签 URL', () => {
  assert.equal(buildViewUrl(ORIGIN, conv('k1')), `${ORIGIN}/k1?workspace_id=w1&tab=k1`);
  assert.equal(buildViewUrl(ORIGIN, conv('k1', { kind: 'new', conversation_id: '' })), `${ORIGIN}/new?workspace_id=w1&tab=k1`);
  assert.equal(buildViewUrl(ORIGIN, conv('k1', { workspace_id: '' })), `${ORIGIN}/new?tab=k1`);
  assert.equal(buildViewUrl(ORIGIN, conv('k1', { conversation_kind: 'chat', conversation_id: 'conv_abc', workspace_id: '' })), `${ORIGIN}/chat/abc?tab=k1`);
  assert.equal(buildViewUrl(ORIGIN, conv('k1', { conversation_kind: 'chat', kind: 'new', conversation_id: '' })), `${ORIGIN}/chat/new?tab=k1`);
  assert.equal(buildViewUrl(ORIGIN, { key: 'p', kind: 'page', page_type: 'settings', route: '/settings/models' }), `${ORIGIN}/settings/models?tab=p`);
  assert.equal(buildViewUrl(ORIGIN, { key: 'p', kind: 'page', route: '/git?a=1' }), `${ORIGIN}/git?a=1&tab=p`);
});

test('planReconcile: 创建 active、隐藏其余、销毁消失的标签、detached 不显示', () => {
  const state = snap(3, [conv('a'), conv('b'), conv('d', { placement: 'detached' })], 'b');
  const entries = [{ key: 'a', kind: 'conv', placement: 'main' }, { key: 'gone', kind: 'conv', placement: 'main' }, { key: 'd', kind: 'conv', placement: 'main' }];
  const plan = planReconcile(state, entries);
  assert.equal(plan.active, 'b');
  assert.deepEqual(plan.create, ['b']);
  assert.deepEqual(plan.destroy, ['gone']);
  assert.deepEqual(plan.hide, ['a']);
  assert.deepEqual(plan.toDetached, ['d']);
  // active_key 缺失时退回第一个主窗口标签
  assert.equal(planReconcile(snap(1, [conv('a'), conv('b')], 'zzz'), []).active, 'a');
});

test('chooseEvictions: 超限淘汰最久未看，不淘汰 active，page/detached 不计数', () => {
  const rows = [
    { key: 'a', kind: 'conv', placement: 'main', lastShown: 1 },
    { key: 'b', kind: 'new', placement: 'main', lastShown: 2 },
    { key: 'c', kind: 'conv', placement: 'main', lastShown: 3 },
    { key: 'p', kind: 'page', placement: 'main', lastShown: 0 },
    { key: 'd', kind: 'conv', placement: 'detached', lastShown: 0 }
  ];
  assert.deepEqual(chooseEvictions(rows, 'c', 3), []);
  assert.deepEqual(chooseEvictions(rows, 'c', 2), ['a']);
  assert.deepEqual(chooseEvictions(rows, 'a', 2), ['b']);
  assert.deepEqual(chooseEvictions(rows, 'a', 1), ['b', 'c']);
});

test('warmupOrder: 按最近查看优先，补到上限', () => {
  const state = snap(1, [conv('a'), conv('b'), conv('c'), { key: 'p', kind: 'page' }], 'a');
  assert.deepEqual(warmupOrder(state, ['a'], { b: 5, c: 9 }, 3), ['c', 'b']);
  assert.deepEqual(warmupOrder(state, ['a'], { b: 5, c: 9 }, 2), ['c']);
  assert.deepEqual(warmupOrder(state, ['a'], {}, 3), ['b', 'c']);
  assert.deepEqual(warmupOrder(state, ['a', 'b', 'c'], {}, 3), []);
});

test('ViewManager: 快照显示 active、隐藏其余并发送可见性', () => {
  const f = fixture();
  assert.equal(f.manager.handleSnapshot(snap(1, [conv('a'), conv('b')], 'a')), true);
  const a = f.manager.entries.get('a');
  assert.equal(a.view.visible, true);
  assert.equal(a.shown, true);
  assert.deepEqual(a.view.bounds, mainContentBounds({ width: 1000, height: 800 }));
  assert.equal(a.view.webContents.url, `${ORIGIN}/a?workspace_id=w1&tab=a`);
  assert.deepEqual(a.view.webContents.sent.find(s => s[0] === 'shell:visibility')[1], { visible: true });
  assert.equal(a.view.webContents.focused, 0); // 窗口未聚焦时不得抢焦点
  assert.equal(f.manager.getActiveView(), a.view);
  assert.equal(f.manager.isManagedView(a.view.webContents), true);
  assert.equal(f.manager.isManagedView(f.chromeView.webContents), false);
  // 切到 b：a 隐藏并通知
  f.manager.handleSnapshot(snap(2, [conv('a'), conv('b')], 'b'));
  const b = f.manager.entries.get('b');
  assert.equal(b.view.visible, true);
  assert.equal(a.view.visible, false);
  assert.deepEqual(a.view.webContents.sent.filter(x => x[0] === 'shell:visibility').at(-1), ['shell:visibility', { visible: false }]);
  assert.equal(f.manager.getActiveView(), b.view);
});

test('ViewManager: 旧 revision 被拒绝，不广播；广播不回显给来源', () => {
  const f = fixture();
  f.manager.handleSnapshot(snap(5, [conv('a'), conv('b')], 'a'));
  const a = f.manager.entries.get('a');
  const before = a.view.webContents.sent.length;
  assert.equal(f.manager.handleSnapshot(snap(5, [conv('b')], 'b')), false);
  assert.equal(f.manager.handleSnapshot(snap(4, [conv('b')], 'b')), false);
  assert.equal(a.view.webContents.sent.length, before);
  const state = snap(6, [conv('a'), conv('b')], 'a');
  const echoed = () => a.view.webContents.sent.filter(x => x[0] === 'shell:tabs-state').length;
  const count = echoed();
  f.manager.handleSnapshot(state, a.view.webContents);
  assert.equal(echoed(), count);
  assert.deepEqual(f.chromeView.webContents.sent.at(-1), ['shell:tabs-state', state]);
});

test('ViewManager: 标签消失则销毁视图并通知宿主回调', () => {
  const f = fixture();
  f.manager.handleSnapshot(snap(1, [conv('a'), conv('b')], 'a'));
  const view = f.manager.entries.get('a').view;
  f.manager.handleSnapshot(snap(2, [conv('b')], 'b'));
  assert.deepEqual(f.destroyed, ['a']);
  assert.equal(view.webContents.destroyed, true);
  assert.equal(f.window.children.includes(view), false);
  assert.equal(f.manager.entries.has('a'), false);
});

test('ViewManager: LRU 超过 6 个淘汰最久未看且不淘汰 active', () => {
  const f = fixture();
  f.manager.handleSnapshot(snap(1, tabsOf(8), 't0'));
  for (let i = 1; i < 7; i++) f.manager.show(`t${i}`);
  // t0..t6 共 7 个视图创建过 → 淘汰最早的 t0
  assert.equal(f.manager.entries.has('t0'), false);
  assert.equal(f.manager.realEntries().length, 6);
  assert.equal(f.manager.activeKey, 't6');
  f.manager.show('t7');
  assert.equal(f.manager.entries.has('t1'), false);
  assert.equal(f.manager.entries.has('t7'), true);
  assert.equal(f.manager.realEntries().length, 6);
  // 被淘汰的标签仍在快照里：快照再次激活它 → 冷创建（先让乐观保护过期）
  while (f.fire(1500));
  f.manager.handleSnapshot(snap(2, tabsOf(8), 't0'));
  assert.equal(f.manager.entries.has('t0'), true);
  assert.equal(f.manager.realEntries().length, 6);
});

test('ViewManager: page 视图不计入上限，也不被淘汰', () => {
  const f = fixture();
  const tabs = [...tabsOf(7), { key: 'pg', kind: 'page', page_type: 'settings', route: '/settings', placement: 'main' }];
  f.manager.handleSnapshot(snap(1, tabs, 'pg'));
  for (let i = 0; i < 7; i++) f.manager.show(`t${i}`);
  f.manager.show('pg');
  assert.equal(f.manager.entries.has('pg'), true);
  assert.equal(f.manager.realEntries().filter(e => e.kind !== 'page').length, 6);
});

test('ViewManager: 乐观 show 后被后端快照纠正', () => {
  const f = fixture();
  f.manager.handleSnapshot(snap(1, [conv('a'), conv('b')], 'a'));
  assert.equal(f.manager.show('b'), true);
  assert.equal(f.manager.activeKey, 'b');
  assert.equal(f.manager.entries.get('a').view.visible, false);
  assert.equal(f.manager.show('missing'), false);
  // 后端回的快照 active 仍是 a（例如 activate 被拒）：保护期过后以快照为准
  f.fire(1500);
  assert.equal(f.manager.activeKey, 'a');
  f.manager.handleSnapshot(snap(2, [conv('a'), conv('b')], 'a'));
  assert.equal(f.manager.activeKey, 'a');
  assert.equal(f.manager.entries.get('a').view.visible, true);
  assert.equal(f.manager.entries.get('b').view.visible, false);
  // 乐观创建：未创建过的标签立即创建
  const f2 = fixture();
  f2.manager.handleSnapshot(snap(1, [conv('a'), conv('b')], 'a'));
  assert.equal(f2.manager.entries.has('b'), false);
  f2.manager.show('b');
  assert.equal(f2.manager.entries.get('b').view.visible, true);
});

test('ViewManager: 启动先建 active 再按 recency 预热到上限，并持久化 recency', () => {
  const f = fixture({ recency: { t2: 50, t1: 10, t3: 90 }, manager: { maxViews: 3 } });
  f.manager.handleSnapshot(snap(1, tabsOf(5), 't0'));
  assert.deepEqual(f.created, ['t0']);
  // 第一个定时器是 legacy（已被取消），其后是预热步骤
  while (f.run()) { /* 逐步执行预热与 recency 保存 */ }
  assert.deepEqual(f.created, ['t0', 't3', 't2']);
  assert.equal(f.manager.entries.get('t3').shown, false);
  assert.equal(f.manager.entries.get('t3').view.visible, false);
  const last = f.saved.at(-1);
  assert.equal(typeof last.t0, 'number');
  assert.equal('t4' in last, false);
});

test('ViewManager: legacy 兜底——无快照 3s 后加载 /，快照到达即移除', () => {
  const f = fixture();
  assert.equal(f.timers[0].ms, 3000);
  f.run();
  const legacy = f.manager.entries.get('__legacy__');
  assert.ok(legacy);
  assert.equal(legacy.view.webContents.url, `${ORIGIN}/`);
  assert.equal(legacy.view.visible, true);
  assert.deepEqual(f.manager.identityFor(legacy.view.webContents).key, '');
  f.manager.handleSnapshot(snap(1, [conv('a')], 'a'));
  assert.equal(f.manager.entries.has('__legacy__'), false);
  assert.equal(legacy.view.webContents.destroyed, true);
  assert.equal(f.manager.entries.get('a').view.visible, true);
});

test('ViewManager: 收到快照后 legacy 定时器被取消', () => {
  const f = fixture();
  f.manager.handleSnapshot(snap(1, [conv('a')], 'a'));
  f.run();
  assert.equal(f.manager.entries.has('__legacy__'), false);
});

test('ViewManager: identity 按 sender 返回，未知 sender 返回 null', () => {
  const f = fixture();
  f.manager.handleSnapshot(snap(1, [conv('a'), conv('b')], 'a'));
  const a = f.manager.entries.get('a').view.webContents;
  assert.deepEqual(f.manager.identityFor(a), { key: 'a', placement: 'main', visible: true, legacy: false, state: f.manager.snapshot });
  assert.equal(f.manager.identityFor(f.chromeView.webContents), null);
  assert.equal(f.manager.identityFor({}), null);
});

test('ViewManager: dispose 关闭所有视图与 chrome', () => {
  const f = fixture();
  f.manager.handleSnapshot(snap(1, [conv('a'), conv('b')], 'a'));
  f.manager.show('b');
  f.manager.dispose();
  assert.equal(f.manager.entries.size, 0);
  assert.equal(f.chromeView.webContents.destroyed, true);
  assert.deepEqual(f.destroyed.sort(), ['a', 'b']);
  assert.equal(f.manager.handleSnapshot(snap(9, [conv('a')], 'a')), false);
});

test('ViewManager: requestPage 首个快照前排队，之后经 chrome 发 request-page，不改动对话视图 URL', () => {
  const f = fixture();
  f.manager.requestPage('/settings/quick-chat');
  assert.equal(f.chromeView.webContents.sent.some(x => x[0] === 'shell:request-page'), false);
  f.manager.handleSnapshot(snap(1, [conv('a')], 'a'));
  assert.deepEqual(f.chromeView.webContents.sent.filter(x => x[0] === 'shell:request-page'), [['shell:request-page', { route: '/settings/quick-chat' }]]);
  assert.equal(f.manager.entries.get('a').view.webContents.url, `${ORIGIN}/a?workspace_id=w1&tab=a`);
  f.manager.requestPage('/settings/quick-chat');
  assert.equal(f.chromeView.webContents.sent.filter(x => x[0] === 'shell:request-page').length, 2);
  f.manager.requestPage('settings');
  assert.equal(f.chromeView.webContents.sent.filter(x => x[0] === 'shell:request-page').length, 2);
});

test('ViewManager: legacy 兜底页直接使用排队的路由', () => {
  const f = fixture();
  f.manager.requestPage('/settings/quick-chat');
  f.run();
  assert.equal(f.manager.entries.get('__legacy__').view.webContents.url, `${ORIGIN}/settings/quick-chat`);
});

// ---- P1 缺口：乐观显示闪烁保护 ----
test('乐观 show 保护期内，旧意图快照不切走可见视图，但其它收敛照常', () => {
  const f = fixture();
  f.manager.handleSnapshot(snap(1, [conv('a'), conv('b'), conv('c')], 'a'));
  f.manager.show('b');
  // 旧意图快照（active 仍是 a，同时 c 被关闭）
  f.manager.handleSnapshot(snap(2, [conv('a'), conv('b')], 'a'));
  assert.equal(f.manager.activeKey, 'b');
  assert.equal(f.manager.entries.get('b').view.visible, true);
  assert.equal(f.manager.entries.get('a').view.visible, false);
  // 其它收敛仍生效：不存在的 c 的视图被销毁
  f.manager.handleSnapshot(snap(3, [conv('a'), conv('b'), conv('d')], 'a'));
  assert.equal(f.manager.activeKey, 'b');
  // 快照确认 b 后解除保护；之后的快照可再切走
  f.manager.handleSnapshot(snap(4, [conv('a'), conv('b'), conv('d')], 'b'));
  assert.equal(f.manager.optimistic, null);
  f.manager.handleSnapshot(snap(5, [conv('a'), conv('b'), conv('d')], 'a'));
  assert.equal(f.manager.activeKey, 'a');
});

test('乐观保护过期（定时器）后按当前快照收敛；标签被关则立即放弃保护', () => {
  const f = fixture();
  f.manager.handleSnapshot(snap(1, [conv('a'), conv('b')], 'a'));
  f.manager.show('b');
  assert.equal(f.fire(1500), true);
  assert.equal(f.manager.activeKey, 'a');
  f.manager.show('b');
  f.manager.handleSnapshot(snap(2, [conv('a')], 'a'));
  assert.equal(f.manager.activeKey, 'a');
});

// ---- P2：独立窗口 ----
test('planReconcile: toDetached / createDetached / toMain', () => {
  const state = snap(3, [conv('a'), conv('d', { placement: 'detached' }), conv('e', { placement: 'detached' }), conv('m')], 'a');
  const plan = planReconcile(state, [
    { key: 'a', kind: 'conv', placement: 'main' }, { key: 'd', kind: 'conv', placement: 'main' },
    { key: 'm', kind: 'conv', placement: 'detached' }
  ]);
  assert.deepEqual(plan.toDetached, ['d']);
  assert.deepEqual(plan.createDetached, ['e']);
  assert.deepEqual(plan.toMain, ['m']);
  assert.deepEqual(plan.hide, []);
});

test('重启恢复：detached 标签创建视图与窗口，不抢占焦点、不计入 LRU', () => {
  const f = fixture();
  f.manager.handleSnapshot(snap(1, [conv('a'), conv('d', { placement: 'detached' })], 'a'));
  const d = f.manager.entries.get('d');
  assert.equal(d.placement, 'detached');
  assert.equal(d.window, f.detachedWins.get('d'));
  assert.equal(f.detachedWins.get('d').focusOnCreate, false);
  assert.equal(f.detachedWins.get('d').children.includes(d.view), true);
  assert.equal(f.window.children.includes(d.view), false);
  assert.equal(d.view.visible, true);
  assert.deepEqual(d.view.bounds, { x: 0, y: 0, width: 600, height: 500 });
  assert.equal(d.shown, true);
  assert.equal(f.manager.activeKey, 'a');
  assert.equal(f.manager.identityFor(d.view.webContents).placement, 'detached');
  assert.deepEqual(d.view.webContents.sent.filter(x => x[0] === 'shell:placement').at(-1)[1], { key: 'd', placement: 'detached' });
  // 7 个 main 标签 + 1 个 detached：detached 永不被淘汰
  const tabs = [...tabsOf(7), conv('d', { placement: 'detached' })];
  f.manager.handleSnapshot(snap(2, tabs, 't0'));
  for (let i = 1; i < 7; i++) f.manager.show(`t${i}`);
  assert.equal(f.manager.entries.has('d'), true);
});

test('detach 迁移：同一 view 对象迁入新窗口，不重建；placement=main 时迁回主窗口并隐藏', () => {
  const f = fixture();
  f.manager.handleSnapshot(snap(1, [conv('a'), conv('b')], 'b'));
  const view = f.manager.entries.get('b').view;
  f.manager.handleSnapshot(snap(2, [conv('a'), conv('b', { placement: 'detached' })], 'a'));
  const b = f.manager.entries.get('b');
  assert.equal(b.view, view);
  assert.equal(f.created.filter(k => k === 'b').length, 1);
  assert.equal(b.placement, 'detached');
  assert.deepEqual(f.moved.at(-1).slice(0, 2), ['b', 'detached']);
  // 迁回
  const win = f.detachedWins.get('b');
  f.manager.handleSnapshot(snap(3, [conv('a'), conv('b')], 'a'));
  assert.equal(b.placement, 'main');
  assert.equal(b.window, f.window);
  assert.equal(f.window.children.includes(view), true);
  assert.equal(view.visible, false);
  assert.equal(b.shown, false);
  assert.equal(win.isDestroyed(), true);
  assert.equal(f.manager.detachedWindows.size, 0);
  assert.deepEqual(f.moved.at(-1).slice(0, 2), ['b', 'main']);
});

test('关闭独立窗口：先放回主窗口(隐藏)并跑 LRU，再请 chrome 发 dock；旧快照不会再次拖出', () => {
  const f = fixture({ manager: { maxViews: 2 } });
  f.manager.handleSnapshot(snap(1, [conv('a'), conv('b'), conv('c'), conv('d', { placement: 'detached' })], 'a'));
  f.manager.show('b');
  const d = f.manager.entries.get('d');
  const order = [];
  const sendChrome = f.chromeView.webContents.send.bind(f.chromeView.webContents);
  f.chromeView.webContents.send = (ch, data) => { order.push([ch, d.placement, f.window.children.includes(d.view)]); sendChrome(ch, data); };
  f.detachedWins.get('d').callbacks.onClose();
  assert.equal(d.placement, 'main');
  assert.equal(d.view.visible, false);
  assert.equal(d.shown, false);
  assert.deepEqual(order.at(-1), ['shell:request-dock', 'main', true]);
  assert.deepEqual(f.chromeView.webContents.sent.at(-1), ['shell:request-dock', { key: 'd' }]);
  // 现在计入 LRU（上限 2）：a(早) 被淘汰，active b 保留
  assert.equal(f.manager.entries.has('a'), false);
  assert.equal(f.manager.entries.get('b').shown, true);
  // 后端尚未处理 dock 的旧快照（仍 detached）不得重新建窗
  const before = f.detachedWins.size;
  f.manager.handleSnapshot(snap(2, [conv('a'), conv('b'), conv('c'), conv('d', { placement: 'detached' })], 'b'));
  assert.equal(f.manager.detachedWindows.size, 0);
  assert.equal(f.detachedWins.size, before);
  assert.equal(d.placement, 'main');
  // 后端确认 main 后清除待确认
  f.manager.handleSnapshot(snap(3, [conv('a'), conv('b'), conv('c'), conv('d')], 'b'));
  assert.equal(f.manager.pendingDock.size, 0);
});

test('拖出：落点建窗迁入、active 乐观显示右侧继任，并请 chrome 发 detach', () => {
  const f = fixture();
  f.manager.handleSnapshot(snap(1, [conv('a'), conv('b'), conv('c')], 'b'));
  const b = f.manager.entries.get('b');
  assert.equal(f.manager.dragTab({ key: 'b', phase: 'move', screenX: 1, screenY: 2 }), false);
  assert.equal(f.manager.dragTab({ key: 'b', phase: 'drop', screenX: 500, screenY: 400 }), true);
  assert.equal(b.placement, 'detached');
  assert.deepEqual(f.detachedWins.get('b').point, { x: 500, y: 400 });
  assert.equal(f.manager.activeKey, 'c');
  assert.equal(f.manager.entries.get('c').view.visible, true);
  assert.deepEqual(f.chromeView.webContents.sent.at(-1), ['shell:request-detach', { key: 'b' }]);
  // 后端尚未确认的旧快照：不把它迁回，也不把 active 切回
  f.manager.handleSnapshot(snap(2, [conv('a'), conv('b'), conv('c')], 'b'));
  assert.equal(b.placement, 'detached');
  assert.equal(f.manager.activeKey, 'c');
  // 后端确认
  f.manager.handleSnapshot(snap(3, [conv('a'), conv('b', { placement: 'detached' }), conv('c')], 'c'));
  assert.equal(f.manager.pendingDetach.size, 0);
  assert.equal(f.manager.activeKey, 'c');
});

test('拖出：无右侧取左侧；非 conv/非 main 标签或无落点时的处理', () => {
  const f = fixture();
  f.manager.handleSnapshot(snap(1, [conv('a'), conv('b'), { key: 'pg', kind: 'page', route: '/settings', placement: 'main' }, conv('n', { kind: 'new', conversation_id: '' })], 'b'));
  assert.equal(f.manager.dragTab({ key: 'pg', phase: 'drop' }), false);
  assert.equal(f.manager.dragTab({ key: 'n', phase: 'drop' }), false);
  const g = fixture();
  g.manager.handleSnapshot(snap(1, [conv('a'), conv('b')], 'b'));
  assert.equal(g.manager.dragTab({ key: 'b', phase: 'drop' }), true);
  assert.deepEqual(g.detachedWins.get('b').point, { x: 7, y: 8 }); // 光标兜底
  assert.equal(g.manager.activeKey, 'a');
});

test('拖出被后端拒绝：超时后快照仍为 main，视图迁回', () => {
  const f = fixture();
  f.manager.handleSnapshot(snap(1, [conv('a'), conv('b')], 'a'));
  f.manager.dragTab({ key: 'b', phase: 'drop', screenX: 1, screenY: 1 });
  assert.equal(f.manager.entries.get('b').placement, 'detached');
  f.fire(2500);
  assert.equal(f.manager.entries.get('b').placement, 'main');
  assert.equal(f.manager.entries.get('b').view.visible, false);
});

test('focusTab / 标题 / 快捷模式隐藏恢复 / 主窗口关闭销毁独立窗口', () => {
  const f = fixture();
  f.manager.handleSnapshot(snap(1, [conv('a'), conv('b'), conv('d', { placement: 'detached' })], 'a'));
  const win = f.detachedWins.get('d');
  win.focus = () => { win.focused = true; };
  assert.equal(f.manager.focusTab('d'), true);
  assert.equal(win.focused, true);
  assert.equal(f.manager.focusTab('b'), true);
  assert.equal(f.manager.activeKey, 'b');
  f.manager.titleChanged(f.manager.entries.get('d').view.webContents, '标题');
  assert.equal(win.title, '标题');
  f.manager.hideDetached();
  assert.equal(win.visible, false);
  f.manager.showDetached();
  assert.equal(win.visible, true);
  win.callbacks.onVisibility(false);
  assert.equal(f.manager.entries.get('d').shown, false);
  f.manager.dispose();
  assert.equal(win.isDestroyed(), true);
  // dispose 不得触发 dock 请求
  assert.equal(f.chromeView.webContents.sent.some(x => x[0] === 'shell:request-dock'), false);
});

test('标签消失：独立窗口一并销毁且不请求 dock', () => {
  const f = fixture();
  f.manager.handleSnapshot(snap(1, [conv('a'), conv('d', { placement: 'detached' })], 'a'));
  const win = f.detachedWins.get('d');
  f.manager.handleSnapshot(snap(2, [conv('a')], 'a'));
  assert.equal(win.isDestroyed(), true);
  assert.equal(f.manager.entries.has('d'), false);
  assert.equal(f.chromeView.webContents.sent.some(x => x[0] === 'shell:request-dock'), false);
});

// ---- P1 缺口：崩溃恢复 ----
const crash = (f, key) => { const wc = f.manager.entries.get(key).view.webContents; wc.listeners['render-process-gone']({}, { reason: 'crashed' }); };
test('崩溃：active 视图被重建并显示；非 active 仅丢弃', () => {
  const f = fixture();
  f.created.length = 0;
  // fakeView 需要记录监听
  f.manager.handleSnapshot(snap(1, [conv('a'), conv('b')], 'a'));
  f.manager.show('b');
  const old = f.manager.entries.get('b').view;
  crash(f, 'b');
  const fresh = f.manager.entries.get('b');
  assert.ok(fresh);
  assert.notEqual(fresh.view, old);
  assert.equal(old.webContents.destroyed, true);
  assert.equal(fresh.view.visible, true);
  assert.equal(f.manager.activeKey, 'b');
  assert.ok(f.destroyed.includes('b'));
  // 非 active：丢弃，不重建
  crash(f, 'a');
  assert.equal(f.manager.entries.has('a'), false);
  assert.equal(f.manager.activeKey, 'b');
});

test('崩溃：独立窗口里的视图重建进原窗口', () => {
  const f = fixture();
  f.manager.handleSnapshot(snap(1, [conv('a'), conv('d', { placement: 'detached' })], 'a'));
  const win = f.detachedWins.get('d');
  crash(f, 'd');
  const fresh = f.manager.entries.get('d');
  assert.equal(fresh.placement, 'detached');
  assert.equal(fresh.window, win);
  assert.equal(win.isDestroyed(), false);
  assert.equal(win.children.includes(fresh.view), true);
  assert.equal(f.detachedWins.size, 1);
});

test('崩溃循环：窗口期内超过上限不再重建，退避延迟递增', () => {
  const f = fixture();
  f.manager.handleSnapshot(snap(1, [conv('a')], 'a'));
  crash(f, 'a'); // 第 1 次：立即重建
  assert.ok(f.manager.entries.get('a'));
  crash(f, 'a'); // 第 2 次：延迟 1s
  assert.equal(f.manager.entries.has('a'), false);
  assert.equal(f.fire(1000), true);
  assert.ok(f.manager.entries.get('a'));
  crash(f, 'a'); // 第 3 次：延迟 2s
  assert.equal(f.fire(2000), true);
  crash(f, 'a'); // 第 4 次：放弃
  assert.equal(f.manager.entries.has('a'), false);
  assert.equal(f.timers.some(t => !t.off && (t.ms === 2000 || t.ms === 4000)), false);
});

test('应用退出/安装更新期间关闭独立窗口：不放回、不请求 dock', () => {
  let quitting = false;
  const f = fixture({ manager: { isQuitting: () => quitting } });
  f.manager.handleSnapshot(snap(1, [conv('a'), conv('d', { placement: 'detached' })], 'a'));
  const d = f.manager.entries.get('d');
  quitting = true;
  f.detachedWins.get('d').callbacks.onClose();
  assert.equal(d.placement, 'detached');
  assert.equal(f.chromeView.webContents.sent.some(x => x[0] === 'shell:request-dock'), false);
  assert.equal(f.manager.pendingDock.size, 0);
  f.manager.dispose();
  assert.equal(f.chromeView.webContents.sent.some(x => x[0] === 'shell:request-dock'), false);
});

test('不抢焦点：窗口未聚焦时，快照收敛/重复快照/乐观切换都不调用 webContents.focus()', () => {
  const f = fixture();
  const total = () => [...f.manager.entries.values()].reduce((n, e) => n + e.view.webContents.focused, 0);
  f.manager.handleSnapshot(snap(1, [conv('a'), conv('b')], 'a'));
  f.manager.handleSnapshot(snap(2, [conv('a'), conv('b')], 'a')); // 未变化
  f.manager.handleSnapshot(snap(3, [conv('a'), conv('b')], 'b')); // 切换
  f.manager.show('a');
  f.manager.handleSnapshot(snap(4, [conv('a'), conv('b'), conv('d', { placement: 'detached' })], 'a'));
  f.manager.layout();
  assert.equal(total(), 0);
  assert.equal(f.detachedWins.get('d').focusOnCreate, false); // 快照路径建的独立窗口不抢焦点
});

test('窗口已聚焦：只在切换/首次显示时移入键盘焦点，重复快照不再重复聚焦', () => {
  const f = fixture();
  f.window.focusedFlag = true;
  f.manager.handleSnapshot(snap(1, [conv('a'), conv('b')], 'a'));
  const a = f.manager.entries.get('a').view.webContents;
  assert.equal(a.focused, 1);
  f.manager.handleSnapshot(snap(2, [conv('a'), conv('b')], 'a'));
  f.manager.handleSnapshot(snap(3, [conv('a'), conv('b'), conv('c')], 'a'));
  assert.equal(a.focused, 1);
  f.manager.show('b');
  assert.equal(f.manager.entries.get('b').view.webContents.focused, 1);
});

// ---- 浏览器式拖出：tear / move / drop / cancel ----
const tear = (f, extra = {}) => f.manager.dragTab({ key: 'b', phase: 'tear', screenX: 500, screenY: 400, offsetX: 100, offsetY: 19, ...extra });
const sentTo = (f, channel) => f.chromeView.webContents.sent.filter(x => x[0] === channel);
function dragFixture(opts = {}) {
  const f = fixture(opts);
  f.manager.handleSnapshot(snap(1, [conv('a'), conv('b'), conv('c')], 'b'));
  return f;
}

test('tear：立即建窗（不聚焦）、视图迁入、继任标签显示、不请求 detach', () => {
  const f = dragFixture();
  const b = f.manager.entries.get('b');
  assert.equal(tear(f), true);
  const win = f.detachedWins.get('b');
  assert.equal(win.focusOnCreate, false);
  assert.deepEqual(win.point, { x: 500, y: 400 });
  assert.deepEqual(win.offset, { x: 100, y: 19 });
  assert.equal(b.placement, 'detached');
  assert.equal(win.children.includes(b.view), true);
  assert.equal(win.astrionDragging, true);
  assert.equal(f.manager.activeKey, 'c');
  assert.equal(f.manager.entries.get('c').view.visible, true);
  assert.equal(f.manager.entries.get('c').view.webContents.focused, 0);
  assert.equal(sentTo(f, 'shell:request-detach').length, 0);
  assert.equal(f.manager.pendingDetach.get('b'), Infinity);
  // 只允许一次拖出
  assert.equal(f.manager.dragTab({ key: 'a', phase: 'tear', screenX: 1, screenY: 1 }), false);
  // 页面/new 标签不可 tear
  const g = fixture();
  g.manager.handleSnapshot(snap(1, [conv('a'), conv('n', { kind: 'new', conversation_id: '' })], 'a'));
  assert.equal(g.manager.dragTab({ key: 'n', phase: 'tear' }), false);
});

test('move：窗口位置 = 指针 - 偏移；其他 key / 无拖拽的 move 被忽略', () => {
  const f = dragFixture();
  assert.equal(f.manager.dragTab({ key: 'b', phase: 'move', screenX: 1, screenY: 1 }), false); // 尚未 tear
  tear(f);
  const win = f.detachedWins.get('b');
  assert.equal(f.manager.dragTab({ key: 'b', phase: 'move', screenX: 620, screenY: 450 }), true);
  assert.deepEqual(win.pos, [520, 431]);
  assert.equal(f.manager.dragTab({ key: 'zzz', phase: 'move', screenX: 9, screenY: 9 }), false);
  assert.deepEqual(win.pos, [520, 431]);
  assert.equal(sentTo(f, 'shell:request-detach').length, 0);
});

test('拖拽期间快照（含旧意图、保护期过后）不会把视图拉回，也不切走显示的标签', () => {
  const f = dragFixture();
  tear(f);
  f.manager.handleSnapshot(snap(2, [conv('a'), conv('b'), conv('c')], 'b')); // 未变化的旧快照
  assert.equal(f.manager.entries.get('b').placement, 'detached');
  assert.equal(f.manager.activeKey, 'c');
  while (f.fire(1500));
  f.manager.handleSnapshot(snap(3, [conv('a'), conv('b'), conv('c')], 'b'));
  assert.equal(f.manager.entries.get('b').placement, 'detached');
  assert.equal(f.manager.activeKey, 'c');
  assert.equal(f.detachedWins.size, 1);
  assert.equal(f.manager.pendingDetach.get('b'), Infinity);
});

test('drop：聚焦窗口与视图、记住位置、请求 detach（带确认超时）', () => {
  const f = dragFixture();
  tear(f);
  const win = f.detachedWins.get('b');
  assert.equal(f.manager.dragTab({ key: 'b', phase: 'drop', screenX: 700, screenY: 500 }), true);
  assert.deepEqual(win.pos, [600, 481]);
  assert.equal(win.focusCalls, 1);
  assert.equal(win.remembered, 1);
  assert.equal(win.astrionDragging, false);
  assert.equal(f.manager.entries.get('b').view.webContents.focused, 1);
  assert.deepEqual(sentTo(f, 'shell:request-detach'), [['shell:request-detach', { key: 'b' }]]);
  assert.equal(f.manager.drag, null);
  assert.equal(Number.isFinite(f.manager.pendingDetach.get('b')), true);
  // 重复 drop 被忽略
  assert.equal(f.manager.dragTab({ key: 'b', phase: 'drop', screenX: 1, screenY: 1 }), false);
  assert.equal(sentTo(f, 'shell:request-detach').length, 1);
  // 后端拒绝：确认超时后视图迁回
  f.fire(2500);
  assert.equal(f.manager.entries.get('b').placement, 'main');
});

test('cancel：视图放回主窗口、销毁拖拽窗口、不 dock、不写位置、恢复原 active', () => {
  const f = dragFixture();
  tear(f);
  const win = f.detachedWins.get('b');
  assert.equal(f.manager.dragTab({ key: 'b', phase: 'cancel' }), true);
  const b = f.manager.entries.get('b');
  assert.equal(b.placement, 'main');
  assert.equal(b.window, f.window);
  assert.equal(win.isDestroyed(), true);
  assert.equal(f.manager.detachedWindows.size, 0);
  assert.equal(win.remembered, undefined);
  assert.equal(sentTo(f, 'shell:request-dock').length, 0);
  assert.equal(sentTo(f, 'shell:request-detach').length, 0);
  assert.equal(f.manager.activeKey, 'b');
  assert.equal(b.view.visible, true);
  assert.equal(f.manager.entries.get('c').view.visible, false);
  assert.equal(f.manager.pendingDetach.size, 0);
  assert.equal(f.manager.drag, null);
  // 同一手势可以再次拖出
  assert.equal(tear(f), true);
  assert.notEqual(f.detachedWins.get('b'), win);
  assert.equal(f.manager.detachedWindows.size, 1);
  // 取消后快照仍按 main 收敛（不会被当成待 detach）
  f.manager.dragTab({ key: 'b', phase: 'cancel' });
  f.manager.handleSnapshot(snap(2, [conv('a'), conv('b'), conv('c')], 'b'));
  assert.equal(f.manager.entries.get('b').placement, 'main');
});

test('cancel 对非当前 active 的被拖标签：保持原 active，不乱切', () => {
  const f = dragFixture();
  f.manager.handleSnapshot(snap(2, [conv('a'), conv('b'), conv('c')], 'a'));
  tear(f);
  assert.equal(f.manager.activeKey, 'a');
  f.manager.dragTab({ key: 'b', phase: 'cancel' });
  assert.equal(f.manager.activeKey, 'a');
  assert.equal(f.manager.entries.get('b').placement, 'main');
  assert.equal(f.manager.entries.get('b').view.visible, false);
});

test('拖出超时 30s 无消息：按 drop 处理；每条消息会重置计时', () => {
  const f = dragFixture();
  tear(f);
  f.manager.dragTab({ key: 'b', phase: 'move', screenX: 600, screenY: 400 });
  assert.equal(f.fire(30000), true);
  assert.equal(f.manager.drag, null);
  assert.deepEqual(sentTo(f, 'shell:request-detach'), [['shell:request-detach', { key: 'b' }]]);
  assert.equal(f.detachedWins.get('b').focusCalls, 1);
});

test('光标轮询兜底：chrome 的 move 停止后由 getCursor 驱动窗口位置', () => {
  let clock = 1000; const cursor = { x: 300, y: 200 };
  const f = dragFixture({ manager: { now: () => clock, getCursor: () => ({ ...cursor }) } });
  tear(f);
  const win = f.detachedWins.get('b');
  clock += 50; assert.equal(f.fire(16), true); // 刚有 move/tear：不接管
  assert.equal(win.pos, undefined);
  clock += 500; cursor.x = 340; cursor.y = 260;
  assert.equal(f.fire(16), true);
  assert.deepEqual(win.pos, [240, 241]);
  // drop 后轮询停止
  f.manager.dragTab({ key: 'b', phase: 'drop', screenX: 340, screenY: 260 });
  assert.equal(f.timers.some(t => !t.off && t.ms === 16), false);
});

test('拖拽期间窗口被点击获得焦点：按 drop 处理；窗口 close 不会 dock（尚未提交）', () => {
  const f = dragFixture();
  tear(f);
  f.detachedWins.get('b').callbacks.onFocus();
  assert.equal(f.manager.drag, null);
  assert.equal(sentTo(f, 'shell:request-detach').length, 1);
  assert.equal(sentTo(f, 'shell:window-focus').length, 0);
});

test('拖出中途退出/销毁：不 dock、不丢标签，窗口被销毁', () => {
  let quitting = false;
  const f = dragFixture({ manager: { isQuitting: () => quitting } });
  tear(f);
  const win = f.detachedWins.get('b');
  quitting = true;
  win.callbacks.onClose(); // 系统关闭窗口
  assert.equal(sentTo(f, 'shell:request-dock').length, 0);
  f.manager.dispose();
  assert.equal(win.isDestroyed(), true);
  assert.equal(sentTo(f, 'shell:request-dock').length, 0);
  assert.equal(sentTo(f, 'shell:request-detach').length, 0);
  // 标签在后端仍是 main（壳层从未请求 detach）
});

test('拖出中标签被后端删除：窗口销毁、拖拽状态清理', () => {
  const f = dragFixture();
  tear(f);
  const win = f.detachedWins.get('b');
  f.manager.handleSnapshot(snap(2, [conv('a'), conv('c')], 'c'));
  assert.equal(win.isDestroyed(), true);
  assert.equal(f.manager.drag, null);
  assert.equal(f.manager.pendingDetach.has('b'), false);
});

test('gin 式整数校验：指针 y == 偏移 y（-0）、小数坐标、NaN 都不会让 setPosition 抛错', () => {
  const f = dragFixture();
  tear(f); // offset (100, 19)
  const win = f.detachedWins.get('b');
  // y 差为 -0：19 - 19 = 0 为 +0，而 0 - 0 的负零来源：(-0) - 0；用 -0 坐标直接验证
  assert.doesNotThrow(() => f.manager.dragTab({ key: 'b', phase: 'move', screenX: 100, screenY: 19 }));
  assert.deepEqual(win.pos, [0, 0]);
  assert.equal(Object.is(win.pos[1], 0), true);
  assert.doesNotThrow(() => f.manager.dragTab({ key: 'b', phase: 'move', screenX: 100.4, screenY: 18.6 }));
  assert.deepEqual(win.pos, [0, 0]);
  assert.equal(Object.is(win.pos[1], 0), true);
  assert.doesNotThrow(() => f.manager.dragTab({ key: 'b', phase: 'move', screenX: 250.5, screenY: -3.2 }));
  assert.deepEqual(win.pos, [151, -22]);
  const before = win.pos;
  // NaN / Infinity：跳过这次摆放（回退到 getCursor 的有限值也是整数）
  assert.doesNotThrow(() => f.manager.dragTab({ key: 'b', phase: 'move', screenX: NaN, screenY: Infinity }));
  assert.equal(Number.isInteger(win.pos[0]) && Number.isInteger(win.pos[1]), true);
  assert.ok(before);
  // drop 同样走整数化
  assert.doesNotThrow(() => f.manager.dragTab({ key: 'b', phase: 'drop', screenX: 100, screenY: 19.5 }));
});

test('光标轮询的小数/负零坐标同样被整数化', () => {
  let clock = 1000; const cursor = { x: 300.7, y: 19 };
  const f = dragFixture({ manager: { now: () => clock, getCursor: () => ({ ...cursor }) } });
  tear(f);
  const win = f.detachedWins.get('b');
  clock += 500;
  assert.doesNotThrow(() => f.fire(16));
  assert.deepEqual(win.pos, [201, 0]);
  assert.equal(Object.is(win.pos[1], 0), true);
});

test('setPosition 抛错（原生拒绝）不会向外抛出，拖拽继续', () => {
  const f = dragFixture();
  tear(f);
  f.detachedWins.get('b').setPosition = () => { throw new TypeError('boom'); };
  const log = console.error; console.error = () => {};
  try { assert.doesNotThrow(() => f.manager.dragTab({ key: 'b', phase: 'move', screenX: 10, screenY: 10 })); } finally { console.error = log; }
  assert.notEqual(f.manager.drag, null);
});
