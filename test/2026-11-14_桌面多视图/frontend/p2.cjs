// P2：chrome 壳层请求→命令、拖出判定、独立窗口布局切换、focus:"detached" 前置。
const assert = require('node:assert/strict');
const { fixture, answer, commandRequest, tab, flush, compile } = require('./fixture.cjs');
const tests = [];
const test = (name, run) => tests.push([name, run]);
const flat = (value) => JSON.parse(JSON.stringify(value));

function bridge(f) {
  const handlers = {}; const calls = { focusTab: [], focusMain: [] };
  for (const name of ['onRequestDetach', 'onRequestDock', 'onWindowFocus', 'onRequestPage', 'onPlacement'])
    f.window.astrionShell[name] = (cb) => { handlers[name] = cb; return () => { delete handlers[name]; }; };
  f.window.astrionShell.focusTab = (key) => calls.focusTab.push(key);
  f.window.astrionShell.focusMain = () => calls.focusMain.push(true);
  return { handlers, calls };
}
const ok = (f, rows, rev) => f.state(rev, rows, rows[0].key);

test('chrome turns shell requests into backend commands and forwards the results', async () => {
  const f = fixture({ chrome: true }); const { handlers } = bridge(f);
  const off = f.store.installShellRequests();
  const cases = [['onRequestDetach', { key: 'a' }, 'detach', { key: 'a' }],
    ['onRequestDock', { key: 'a' }, 'dock', { key: 'a' }],
    ['onWindowFocus', { key: 'b' }, 'focus', { key: 'b' }],
    ['onRequestPage', { route: '/settings/chat' }, 'page', { page: '/settings/chat' }]];
  let rev = 10;
  for (const [name, payload, action, body] of cases) {
    handlers[name](payload);
    const request = await commandRequest(f, action);
    assert.deepEqual(flat(JSON.parse(request.options.body).payload), body);
    answer(request, { success: true, result: {}, state: ok(f, [tab('a', 'A')], ++rev) }); await flush();
  }
  assert.equal(f.forwarded.length, 4);
  off(); assert.equal(Object.keys(handlers).length, 0);
});

test('shell requests are ignored on views and tolerate a missing bridge method', () => {
  const view = fixture(); bridge(view);
  assert.equal(typeof view.store.installShellRequests(), 'function');
  const chrome = fixture({ chrome: true }); // 未提供 onRequestPage 等方法
  const off = chrome.store.installShellRequests(); off();
});

test('a retried command after a network error keeps the same envelope identity', async () => {
  const f = fixture({ chrome: true }); const { handlers } = bridge(f);
  f.store.installShellRequests(); handlers.onRequestDetach({ key: 'a' });
  await flush(); const first = f.requests[0]; first.reject(new TypeError('connection lost')); await flush();
  assert.equal(f.requests[1].options.body, first.options.body);
});

test('drag decision: conversations and blank new tabs tear off beyond the threshold', () => {
  const { shouldTear, verticalOutside, TEAR_OFF_PX } = compile('static/src/components/chrome/tabReorder.ts');
  assert.ok(TEAR_OFF_PX >= 40);
  const at = (outside) => ({ outside, above: outside > 0, screenY: 300 - outside });
  assert.equal(shouldTear('conv', at(TEAR_OFF_PX + 1)), true);
  assert.equal(shouldTear('conv', at(TEAR_OFF_PX)), false);
  assert.equal(shouldTear('conv', at(0)), false);
  assert.equal(shouldTear('new', at(TEAR_OFF_PX + 1)), true); // 空白新对话标签同样可拖出
  assert.equal(shouldTear('new', at(TEAR_OFF_PX)), false);
  assert.equal(shouldTear('page', at(500)), false); // 页面标签只能排序
  assert.equal(verticalOutside(20, 0, 46), 0);
  assert.equal(verticalOutside(100, 0, 46), 54);
  assert.equal(verticalOutside(-30, 0, 46), 30);
});

test('placement events switch the detached layout live for this view only', async () => {
  const f = fixture({ identity: { key: 'tab_A', placement: 'main', visible: true, legacy: false, state: null } });
  const { handlers } = bridge(f);
  await f.store.init();
  assert.equal(f.store.detached, false);
  handlers.onPlacement({ key: 'other', placement: 'detached' }); assert.equal(f.store.detached, false);
  handlers.onPlacement({ key: 'tab_A', placement: 'detached' }); assert.equal(f.store.detached, true);
  handlers.onPlacement({ key: 'tab_A', placement: 'main' }); assert.equal(f.store.detached, false);
  const born = fixture({ identity: { key: 'tab_A', placement: 'detached', visible: true, legacy: false,
    state: f.state(1, [tab('tab_A', 'A', 'ws1', { placement: 'detached' }), tab('m', 'M')], 'm') } });
  await born.store.init(); assert.equal(born.store.detached, true);
  const chrome = fixture({ chrome: true }); await chrome.store.init(); assert.equal(chrome.store.detached, false);
  // 拖出已开窗、后端尚未 detach：轮询广播的快照仍报 main，不得把独立窗口布局改回主窗口
  handlers.onPlacement({ key: 'tab_A', placement: 'detached' });
  f.store.applyServerSnapshot(f.state(1, [tab('tab_A', 'A'), tab('m', 'M')], 'm'));
  assert.equal(f.store.detached, true);
  // 后端确认 detach 后保持；取消/拖回（主动恢复）才回到主窗口布局
  f.store.applyServerSnapshot(f.state(2, [tab('tab_A', 'A', 'ws1', { placement: 'detached' }), tab('m', 'M')], 'm'));
  assert.equal(f.store.detached, true);
  handlers.onPlacement({ key: 'tab_A', placement: 'main' });
  assert.equal(f.store.detached, false);
  // 身份请求尚未返回时先到本标签的拖出通知：随后返回的旧身份不得把布局改回去
  const racing = fixture({ identity: { key: 'tab_A', placement: 'main', visible: true, legacy: false, state: null } });
  const racingBridge = bridge(racing);
  const pending = racing.store.init();
  racingBridge.handlers.onPlacement({ key: 'tab_A', placement: 'detached' });
  await pending; assert.equal(racing.store.detached, true);
});

test('open returning focus:detached raises that window; page from a detached view focuses main', async () => {
  const f = fixture({ identity: { key: 'tab_A', placement: 'detached', visible: true, legacy: false, state: null } });
  const { calls } = bridge(f);
  await f.store.init();
  const opening = f.store.openConversationTab({ conversationId: 'B', workspaceId: 'ws1' });
  const open = await commandRequest(f, 'open');
  answer(open, { success: true, result: { key: 'tab_B', focus: 'detached' }, state: f.state(2, [tab('tab_B', 'B')], 'tab_B') });
  await opening; assert.deepEqual(calls.focusTab, ['tab_B']); assert.equal(calls.focusMain.length, 0);
  const paging = f.store.openPage('/settings'); const page = await commandRequest(f, 'page');
  answer(page, { success: true, result: { key: 'p' }, state: f.state(3, [tab('tab_B', 'B')], 'tab_B') });
  await paging; assert.equal(calls.focusMain.length, 1);
  const normal = f.store.openConversationTab({ conversationId: 'C', workspaceId: 'ws1' });
  answer(await commandRequest(f, 'open'), { success: true, result: { key: 'tab_C' }, state: f.state(4, [tab('tab_C', 'C')], 'tab_C') });
  await normal; assert.deepEqual(calls.focusTab, ['tab_B']);
});

test('usage-stats jump goes through a page command unless this document is the settings page', async () => {
  const f = fixture({ identity: { key: 'tab_A', placement: 'main', visible: true, legacy: false, state: null } });
  await f.store.init();
  f.store.applyServerSnapshot(f.state(1, [tab('tab_A', 'A')], 'tab_A'));
  assert.equal(f.store.isSettingsView, false); // 对话视图：应发 page 命令
  const page = (type) => ({ key: 'tab_A', kind: 'page', page_type: type, route: '/x', opener_key: '', placement: 'main' });
  f.store.applyServerSnapshot(f.state(2, [page('settings')], 'tab_A')); assert.equal(f.store.isSettingsView, true);
  f.store.applyServerSnapshot(f.state(3, [page('git')], 'tab_A')); assert.equal(f.store.isSettingsView, false);
});

test('strip drag state machine: tear -> move -> back in band = cancel -> reorder -> tear again -> drop', () => {
  const { dragStep, startDrag, TEAR_OFF_PX } = compile('static/src/components/chrome/tabReorder.ts');
  const mv = (dx, outside, extra = {}) => ({ type: 'move', dx, outside, inBand: outside === 0, screenX: 500 + dx, screenY: 300 + outside, offsetX: 100, offsetY: 19, ...extra });
  let state = startDrag('a', 'conv'); const sent = [];
  const run = (input) => { const r = dragStep(state, input); state = r.state; sent.push(...flat(r.shell)); return r; };
  // 未过阈值：什么都不发
  assert.equal(run(mv(2, 0)).shell.length, 0); assert.equal(state.mode, 'pressed');
  // 条内排序
  run(mv(60, 5)); assert.equal(state.mode, 'reorder'); assert.equal(sent.length, 0);
  // 越过拖出阈值：立即 tear（一次），随后 move
  run(mv(70, TEAR_OFF_PX + 1)); assert.equal(state.mode, 'torn');
  assert.deepEqual(sent.at(-1), { phase: 'tear', screenX: 570, screenY: 341, offsetX: 100, offsetY: 19 });
  run(mv(80, 90)); assert.deepEqual(sent.at(-1), { phase: 'move', screenX: 580, screenY: 390 });
  assert.equal(sent.filter((m) => m.phase === 'tear').length, 1);
  // 回到标签条带内：cancel，回到排序态
  run(mv(75, 0)); assert.equal(state.mode, 'reorder'); assert.deepEqual(sent.at(-1), { phase: 'cancel' });
  // 回到排序态后继续在条内移动：不再发消息
  const n = sent.length; run(mv(120, 10)); assert.equal(sent.length, n);
  // 再次拖出：再发一次 tear
  run(mv(130, 80)); assert.equal(sent.at(-1).phase, 'tear'); assert.equal(sent.filter((m) => m.phase === 'tear').length, 2);
  // 松手：drop（最终位置）
  const end = run({ type: 'end', screenX: 700, screenY: 420 });
  assert.deepEqual(sent.at(-1), { phase: 'drop', screenX: 700, screenY: 420 }); assert.equal(end.finished, true); assert.equal(end.commitReorder, false);
});

test('strip drag state machine: reorder commit, escape, lost pointer, page never tears while blank new tabs do', () => {
  const { dragStep, startDrag, TEAR_OFF_PX } = compile('static/src/components/chrome/tabReorder.ts');
  const mv = (dx, outside) => ({ type: 'move', dx, outside, inBand: outside === 0, screenX: 10 + dx, screenY: 20 + outside, offsetX: 100, offsetY: 19 });
  // 排序态松手提交 reorder
  let r = dragStep(dragStep(startDrag('a', 'conv'), mv(50, 0)).state, { type: 'end', screenX: 0, screenY: 0 });
  assert.equal(r.commitReorder, true); assert.deepEqual(flat(r.shell), []);
  // 纯点击不提交
  r = dragStep(startDrag('a', 'conv'), { type: 'end', screenX: 0, screenY: 0 }); assert.equal(r.commitReorder, false);
  // torn + Esc = cancel；reorder + Esc 无消息
  const torn = dragStep(startDrag('a', 'conv'), mv(60, TEAR_OFF_PX + 5)).state;
  assert.deepEqual(flat(dragStep(torn, { type: 'escape' }).shell), [{ phase: 'cancel' }]);
  assert.deepEqual(flat(dragStep(dragStep(startDrag('a', 'conv'), mv(60, 0)).state, { type: 'escape' }).shell), []);
  // torn + pointercancel / lostpointercapture = 在最后已知位置 drop
  assert.deepEqual(flat(dragStep(torn, { type: 'lost' }).shell), [{ phase: 'drop', screenX: 70, screenY: 65 }]);
  assert.deepEqual(flat(dragStep(dragStep(startDrag('a', 'conv'), mv(60, 0)).state, { type: 'lost' }).shell), []);
  // page 标签永不拖出；空白新对话标签与对话一致，越过阈值即 tear
  const pageOut = dragStep(startDrag('p', 'page'), mv(60, 400));
  assert.equal(pageOut.state.mode, 'reorder'); assert.deepEqual(flat(pageOut.shell), []);
  const newOut = dragStep(startDrag('n', 'new'), mv(60, 400));
  assert.equal(newOut.state.mode, 'torn'); assert.equal(flat(newOut.shell).at(-1).phase, 'tear');
});

test('upward tear adapts to the room above the strip (menu bar, maximized, full screen, stacked display)', () => {
  const { shouldTear, upwardThreshold, topLimitFor, dragStep, startDrag } = compile('static/src/components/chrome/tabReorder.ts');
  const up = (outside, screenY) => ({ outside, above: outside > 0, screenY });
  // 1. 窗口顶到菜单栏下：条上沿 y=25，屏幕上沿 y=0（指针可进入菜单栏 25px）。
  const bar = { stripTop: 25, startY: 48, topLimit: 0 };
  assert.equal(upwardThreshold(25), 22);
  assert.equal(shouldTear('conv', up(4, 21), bar), false); // 抖动
  assert.equal(shouldTear('conv', up(15, 10), bar), false);
  assert.equal(shouldTear('conv', up(23, 2), bar), true); // 超过自适应阈值
  assert.equal(shouldTear('conv', up(25, 0), bar), true); // 到顶
  // 余量很小时阈值有下限，贴边规则兜底（余量 5：阈值 6 到不了，但指针贴边向上移动足够）
  const tight = { stripTop: 5, startY: 28, topLimit: 0 };
  assert.equal(upwardThreshold(5), 6);
  assert.equal(shouldTear('conv', up(3, 2), tight), false);
  assert.equal(shouldTear('conv', up(5, 0), tight), true);
  // 2. 最大化（Windows 条在 y=0，指针被钳在 y=0；outside 恒为 0）与 3. macOS 全屏：只靠贴边规则
  const top = { stripTop: 0, startY: 23, topLimit: 0 };
  assert.equal(shouldTear('conv', { outside: 0, above: false, screenY: 8 }, top), false); // 只上移 15 但没到边
  assert.equal(shouldTear('conv', { outside: 0, above: false, screenY: 15 }, top), false);
  assert.equal(shouldTear('conv', { outside: 0, above: false, screenY: 1 }, top), true); // 贴边，上移 22
  assert.equal(shouldTear('conv', { outside: 0, above: false, screenY: 0 }, top), true);
  // 起点本就贴着上沿（<6px）时贴边规则不触发，横向排序不会误拖出
  const edgeStart = { stripTop: 0, startY: 3, topLimit: 0 };
  assert.equal(shouldTear('conv', { outside: 0, above: false, screenY: 0 }, edgeStart), false);
  // 起点离边缘较近时按实际余量缩小所需位移（起点 8px：位移 >= 6.5）
  const near = { stripTop: 0, startY: 8, topLimit: 0 };
  assert.equal(shouldTear('conv', { outside: 0, above: false, screenY: 0 }, near), true);
  // 4. 上方叠放第二块显示器：余量很大，仍是 40px，不因当前屏幕上沿接近而提前拖出
  const displays = [{ x: 0, y: -900, width: 1440, height: 900 }, { x: 0, y: 0, width: 1440, height: 900 }];
  const stacked = { stripTop: 0, startY: 23, topLimit: topLimitFor({ x: 700, y: 23 }, displays) };
  assert.equal(stacked.topLimit, -900);
  assert.equal(shouldTear('conv', up(30, -30), stacked), false);
  assert.equal(shouldTear('conv', up(41, -41), stacked), true);
  assert.equal(shouldTear('conv', { outside: 0, above: false, screenY: 0 }, stacked), false); // y=0 不是边缘
  // 上方显示器不覆盖该 x 时，当前屏幕上沿才是边缘；单屏与侧放屏
  assert.equal(topLimitFor({ x: 2000, y: 23 }, [{ x: 0, y: -900, width: 1440, height: 900 }, { x: 0, y: 0, width: 1440, height: 900 }, { x: 1440, y: 0, width: 1440, height: 900 }]), 0);
  assert.equal(topLimitFor({ x: 10, y: 23 }, [{ x: 0, y: 0, width: 1440, height: 900 }]), 0);
  // 5. 页面标签不可拖出；空白新对话标签与对话同规则；向下仍是 40px
  assert.equal(shouldTear('page', { outside: 0, above: false, screenY: 0 }, top), false);
  assert.equal(shouldTear('page', up(300, -300), stacked), false);
  assert.equal(shouldTear('new', up(300, -300), stacked), true);
  assert.equal(shouldTear('new', up(23, 2), bar), true); // 自适应阈值同样作用于新对话标签
  assert.equal(shouldTear('conv', { outside: 41, above: false, screenY: 400 }, bar), true);
  assert.equal(shouldTear('conv', { outside: 40, above: false, screenY: 400 }, bar), false);
  // 无几何信息：旧行为
  assert.equal(shouldTear('conv', up(23, 2)), false);
  assert.equal(shouldTear('conv', up(41, 2)), true);
  // 贴边拖出后指针仍在条内：不立即取消；离开条带再回来才取消
  const mv = (screenY, outside, inBand) => ({ type: 'move', dx: 20, outside, inBand, above: false, screenX: 10, screenY, offsetX: 100, offsetY: 19 });
  let state = startDrag('a', 'conv', top); const sent = [];
  const run = (i) => { const r = dragStep(state, i); state = r.state; sent.push(...flat(r.shell)); };
  run(mv(0, 0, true)); assert.equal(state.mode, 'torn'); assert.equal(sent.at(-1).phase, 'tear');
  run(mv(0, 0, true)); assert.equal(state.mode, 'torn'); assert.equal(sent.at(-1).phase, 'move');
  run(mv(120, 74, false)); run(mv(100, 54, false)); assert.equal(state.mode, 'torn');
  run(mv(20, 0, true)); assert.equal(state.mode, 'reorder'); assert.equal(sent.at(-1).phase, 'cancel');
});

(async () => {
  for (const [name, run] of tests) { await run(); console.log('PASS ' + name); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
