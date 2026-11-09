const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { _electron } = require('../../node_modules/playwright-core');
const { createFixtureServer } = require('./fixture-server.cjs');
const { ScopedAutomation } = require('./scoped-automation.cjs');
const root = path.resolve(__dirname, '../..');
const lab = path.join(root, '_experiments/native-browser-feasibility-20261107/electron-panel');
const output = path.join(lab, `run-${Date.now()}`);
fs.mkdirSync(output, { recursive: true });
const result = { started: new Date().toISOString(), output, observations: [], errors: [] };
const write = () => fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(result, null, 2));
const record = (name, detail) => { result.observations.push({ name, detail }); write(); console.log(JSON.stringify({ name, detail })); };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(get, test, label) {
  let last;
  for (let i = 0; i < 160; i++) { last = await get(); if (test(last)) return last; await sleep(60); }
  throw Error(`Timed out: ${label}: ${JSON.stringify(last)}`);
}
const { server, requests } = createFixtureServer();
let electronApp, childProcess;
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  electronApp = await _electron.launch({ executablePath: require(path.join(root, 'desktop-electron/node_modules/electron')),
    args: [path.join(__dirname, 'main.cjs')], cwd: root,
    env: { ...process.env, BROWSER_LAB_OUTPUT: output, BROWSER_LAB_ORIGIN: origin }, timeout: 25000 });
  childProcess = electronApp.process();
  childProcess.stderr?.on('data', chunk => fs.appendFileSync(path.join(output, 'process.log'), chunk));
  const watch = page => page.on('pageerror', error => result.errors.push({ url: page.url(), error: String(error) }));
  electronApp.context().pages().forEach(watch); electronApp.context().on('page', watch);
  const snapshot = () => electronApp.evaluate(() => global.browserPrototype?.snapshot() || null);
  let state = await until(snapshot, state => state?.rect?.width > 0 && state.tabs.some(tab => !tab.loading), 'prototype ready');
  const shell = electronApp.context().pages().find(page => page.url() === `${origin}/shell`);
  assert.ok(shell); shell.setDefaultTimeout(8000);
  const cache = new Map();
  async function resolvePage(id) {
    if (cache.has(id) && !cache.get(id).isClosed()) return cache.get(id);
    const target = await electronApp.evaluate((_electron, id) => global.browserPrototype.target(id), id);
    for (const page of electronApp.context().pages()) {
      const session = await page.context().newCDPSession(page);
      try {
        if ((await session.send('Target.getTargetInfo')).targetInfo.targetId === target) { cache.set(id, page); return page; }
      } finally { await session.detach(); }
    }
    throw Error('registered_native_target_not_found');
  }
  const initialId = state.active;
  const page = await resolvePage(initialId); page.setDefaultTimeout(8000);
  const automation = new ScopedAutomation({ describe: snapshot, resolvePage,
    expectedOwner: { workspace: 'lab-workspace', conversation: 'A', actor: 'main' } });
  let lease = await automation.lease(initialId);
  record('native_right_panel', { ...state, versions: JSON.parse(fs.readFileSync(path.join(output, 'ready.json'))).versions,
    offscreen: await electronApp.evaluate(() => global.browserPrototype.registry.active().view.webContents.isOffscreen()) });
  assert.equal(state.nativeWindows, 1); assert.equal(state.nativeChildren, 2);
  await automation.execute({ lease, action: 'fill', selector: '#message', value: 'AI工具写入同一网页' });
  await automation.execute({ lease, action: 'click', selector: '#apply' });
  assert.equal(await automation.execute({ lease, action: 'text', selector: '#result' }), 'AI工具写入同一网页');
  await page.getByLabel('Message').focus();
  await electronApp.evaluate(() => {
    const wc = global.browserPrototype.registry.active().view.webContents;
    wc.focus(); wc.insertText(' + 原生输入');
  });
  const button = await page.locator('#apply').boundingBox();
  await electronApp.evaluate((_electron, point) => {
    const wc = global.browserPrototype.registry.active().view.webContents;
    for (const type of ['mouseDown', 'mouseUp']) wc.sendInputEvent({ type, x: Math.round(point.x), y: Math.round(point.y), button: 'left', clickCount: 1 });
  }, { x: button.x + button.width / 2, y: button.y + button.height / 2 });
  await until(() => automation.execute({ lease, action: 'text', selector: '#result' }), value => value.includes('原生输入'), 'native input');
  record('native_input_and_scoped_tools_same_page', { value: await page.locator('#result').textContent(), note: 'Simulated native input; human IME acceptance remains separate.' });
  await assert.rejects(automation.execute({ lease, action: 'rawCDP' }), /unsupported_action/);
  await assert.rejects(automation.execute({ lease, action: 'navigate', url: 'file:///not-an-experiment-page' }), /unsupported_navigation/);
  await assert.rejects(automation.execute({ lease: { ...lease, tabId: 'trusted-shell' }, action: 'text', selector: 'body' }), /unknown_or_unowned_tab/);
  record('restricted_tool_surface', { rawCdpRejected: true, fileNavigationRejected: true, unregisteredPageRejected: true });
  await shell.getByLabel('网址', { exact: true }).fill(`${origin}/second`);
  await shell.getByRole('button', { name: '前往', exact: true }).click();
  await page.waitForURL(`${origin}/second`);
  await shell.getByRole('button', { name: '后退', exact: true }).click();
  await page.waitForURL(`${origin}/fixture`);
  await shell.getByRole('button', { name: '前进', exact: true }).click();
  await page.waitForURL(`${origin}/second`);
  record('address_and_history_controls', { url: page.url(), sameNativeTab: (await snapshot()).active === initialId });
  const popupPromise = page.waitForEvent('popup', { timeout: 8000 });
  await page.getByRole('link', { name: 'Open popup', exact: true }).click();
  const popup = await popupPromise; await popup.waitForLoadState('domcontentloaded');
  state = await until(snapshot, state => state.tabs.length === 2, 'popup registered');
  assert.equal(state.nativeWindows, 1); assert.equal(state.nativeChildren, 3);
  assert.equal(await popup.evaluate(() => window.opener === null), true);
  record('target_blank_popup_joins_native_panel', { nativeWindows: state.nativeWindows, tabCount: state.tabs.length, popupUrl: popup.url(), defaultNoopener: true });
  await shell.locator('.tab[aria-selected="true"] .tab-close').click();
  await until(snapshot, state => state.tabs.length === 1 && state.active === initialId, 'popup closed');
  const scriptedPromise = page.waitForEvent('popup', { timeout: 8000 });
  await page.getByRole('button', { name: 'Open scripted popup', exact: true }).click();
  const scripted = await scriptedPromise; await scripted.waitForLoadState('domcontentloaded');
  state = await until(snapshot, state => state.tabs.length === 2, 'script popup registered');
  assert.equal(state.nativeWindows, 1);
  assert.equal(await scripted.evaluate(() => window.opener !== null), true);
  assert.deepEqual(await scripted.evaluate(() => ({ node: typeof process, bridge: typeof window.browserLab })), { node: 'undefined', bridge: 'undefined' });
  record('scripted_popup_preserves_opener', { nativeWindows: 1, openerRetained: true, shellBridgeAbsent: true });
  await shell.locator('.tab[aria-selected="true"] .tab-close').click();
  await until(snapshot, state => state.tabs.length === 1 && state.active === initialId, 'script popup closed');
  await page.getByRole('link', { name: 'Download', exact: true }).click();
  await page.getByRole('link', { name: 'Download', exact: true }).click();
  state = await until(snapshot, state => state.downloads.length === 2 && state.downloads.every(item => item.state === 'completed'), 'two downloads');
  const targets = await electronApp.evaluate(() => [...global.browserPrototype.features.downloads.values()].map(item => item.target));
  assert.equal(new Set(targets).size, 2);
  for (const target of targets) assert.equal(fs.readFileSync(target, 'utf8'), 'browser lab download\n');
  await shell.getByRole('button', { name: '下载', exact: true }).click();
  await shell.getByRole('region', { name: '下载记录', exact: true }).waitFor({ state: 'visible' });
  assert.equal(await electronApp.evaluate(() => global.browserPrototype.registry.active().view.getVisible()), false);
  record('downloads_owned_unique_and_overlay_visible', { downloads: state.downloads, nativePageHiddenWhileOverlayOpen: true });
  await shell.getByRole('button', { name: '关闭下载记录', exact: true }).click();
  await page.getByRole('link', { name: 'Slow download', exact: true }).click();
  await until(snapshot, state => state.downloads.some(item => item.filename === 'slow-download.bin' && item.received > 0), 'slow download progressing');
  await shell.getByRole('button', { name: '下载', exact: true }).click();
  await shell.locator('.download-row').filter({ hasText: 'slow-download.bin' }).getByRole('button', { name: '取消下载' }).click();
  state = await until(snapshot, state => state.downloads.some(item => item.filename === 'slow-download.bin' && item.state === 'cancelled'), 'cancelled download');
  record('download_cancel', state.downloads.find(item => item.filename === 'slow-download.bin'));
  await shell.getByRole('button', { name: '关闭下载记录', exact: true }).click();
  await page.getByRole('button', { name: 'Request location', exact: true }).click();
  await shell.getByRole('dialog', { name: '网页位置权限', exact: true }).waitFor({ state: 'visible' });
  assert.equal(await electronApp.evaluate(() => global.browserPrototype.registry.active().view.getVisible()), false);
  await shell.getByRole('button', { name: '拒绝位置权限', exact: true }).click();
  await until(() => page.locator('#location-result').textContent(), value => value === 'denied:1', 'permission denial');
  record('permission_request_custom_ui_and_denial', { deniedAtBrowser: true, nativePageHiddenForPrompt: true, osPermissionGranted: false });
  const boundary = await page.evaluate(() => ({ process: typeof process, require: typeof require, labBridge: typeof window.browserLab, desktopBridge: typeof window.__ASTRION_DESKTOP__ }));
  assert.deepEqual(boundary, { process: 'undefined', require: 'undefined', labBridge: 'undefined', desktopBridge: 'undefined' });
  record('untrusted_page_has_no_shell_or_node_bridge', boundary);
  await page.evaluate(() => { document.cookie='owner=A;path=/'; localStorage.setItem('owner','A'); });
  await shell.getByRole('button', { name: '对话 B', exact: true }).click();
  state = await until(snapshot, state => state.owner.conversation === 'B' && state.tabs.length === 1 && !state.tabs[0].loading, 'conversation B');
  const otherId = state.active; const other = await resolvePage(otherId);
  const storage = await other.evaluate(() => ({ cookie: document.cookie, local: localStorage.getItem('owner') }));
  assert.deepEqual(storage, { cookie: '', local: null });
  assert.equal(state.downloads.length, 0);
  await assert.rejects(automation.execute({ lease, action: 'text', selector: '#result' }), /inactive_owner/);
  await shell.getByRole('button', { name: '对话 A', exact: true }).click();
  await until(snapshot, state => state.owner.conversation === 'A' && state.active === initialId, 'restore A');
  await assert.rejects(automation.execute({ lease, action: 'text', selector: '#result' }), /stale_generation/);
  lease = await automation.lease(initialId);
  await assert.rejects(automation.execute({ lease: { ...lease, tabId: otherId }, action: 'text', selector: 'body' }), /unknown_or_unowned_tab/);
  assert.equal(await page.evaluate(() => localStorage.getItem('owner')), 'A');
  record('conversation_partition_and_generation_guard', { storageB: storage, downloadsB: 0, inactiveOwnerRejected: true, staleLeaseRejectedAfterABA: true, crossConversationTabRejected: true, restoredA: true });
  await shell.getByRole('button', { name: '收起浏览器', exact: true }).click();
  await until(snapshot, state => state.collapsed, 'collapsed');
  assert.equal(await electronApp.evaluate(() => global.browserPrototype.registry.active().view.getVisible()), false);
  await shell.getByRole('button', { name: '展开浏览器', exact: true }).click();
  await until(snapshot, state => !state.collapsed, 'expanded');
  const resized = [];
  for (const [width, height] of [[1000, 680], [1440, 900], [1280, 820]]) {
    await electronApp.evaluate((_electron, size) => global.browserPrototype.window.setContentSize(size.width, size.height), { width, height });
    const measured = await until(async () => ({
      window: await electronApp.evaluate(() => global.browserPrototype.window.getContentBounds()),
      viewport: await shell.locator('#viewport').evaluate(node => { const r=node.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height}; }),
    }), value => value.window.width === width && Math.abs(value.viewport.height - (value.window.height - 46 - 40 - 44 - 24)) < 1, 'DOM matches actual native window');
    const geometry = measured.viewport;
    const native = await until(snapshot, state => Math.abs(state.rect.width-geometry.width)<2 && Math.abs(state.rect.height-geometry.height)<2, 'native rectangle settled');
    resized.push({ requested: { width, height }, actualWindow: measured.window, native: native.rect });
  }
  await shell.getByRole('separator', { name: '调整浏览器宽度' }).focus();
  await shell.keyboard.press('ArrowRight');
  await until(snapshot, state => state.rect.x > 520, 'keyboard divider');
  record('collapse_resize_and_keyboard_divider', { resized, finalBounds: (await snapshot()).rect });
  for (const theme of ['经典', '浅色', '深色']) await shell.getByRole('button', { name: theme, exact: true }).click();
  record('theme_switch_controls', { theme: await shell.locator('html').getAttribute('data-theme'), note: 'State verified; visual acceptance remains manual.' });
  await shell.getByRole('button', { name: '新建浏览器标签', exact: true }).click();
  state = await until(snapshot, state => state.tabs.length === 2, 'new tab');
  const closingLease = await automation.lease(state.active);
  await shell.locator('.tab[aria-selected="true"] .tab-close').click();
  await until(snapshot, state => state.tabs.length === 1, 'close tab');
  await assert.rejects(automation.execute({ lease: closingLease, action: 'text', selector: 'body' }), /unknown_or_unowned_tab/);
  record('closed_tab_tool_lease_invalidated', { rejected: true });
  assert.equal(result.errors.length, 0, 'no renderer uncaught exceptions');
  const child = childProcess;
  await electronApp.evaluate(() => global.browserPrototype.window.close()).catch(() => {});
  await until(async () => ({ exitCode: child.exitCode, signal: child.signalCode }), state => state.exitCode !== null || !!state.signal, 'graceful process exit');
  assert.equal(child.exitCode, 0);
  assert.equal(JSON.parse(fs.readFileSync(path.join(output, 'closed.json'))).closed, true);
  record('graceful_close', { exitCode: child.exitCode, signal: child.signalCode });
  result.status = 'completed';
})().catch(error => { result.status = 'failed'; result.error = String(error.stack || error); console.error(result.error); process.exitCode = 1; }).finally(async () => {
  if (electronApp && childProcess) {
    if (childProcess.exitCode === null && !childProcess.signalCode) await electronApp.close().catch(error => { result.cleanupError = String(error); });
    result.process = { pid: childProcess.pid, exitCode: childProcess.exitCode, signal: childProcess.signalCode };
  }
  server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
  result.requests = requests; result.finished = new Date().toISOString(); write();
  fs.writeFileSync(path.join(lab, 'latest-run.json'), JSON.stringify({ output, status: result.status }, null, 2));
});
