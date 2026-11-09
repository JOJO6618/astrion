const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const { performance } = require('node:perf_hooks');
const { _electron, chromium } = require('../../node_modules/playwright-core');
const root = path.resolve(__dirname, '../..');
const output = path.join(root, '_experiments/native-browser-feasibility-20261107/results');
fs.mkdirSync(output, { recursive: true });
const observations = { started: new Date().toISOString(), checks: [] };
const record = (name, detail) => {
  observations.checks.push({ name, status: 'pass', detail });
  fs.writeFileSync(path.join(output, 'electron-results.json'), JSON.stringify(observations, null, 2));
  console.log(JSON.stringify({ name, status: 'pass', detail }));
};
const fixture = fs.readFileSync(path.join(__dirname, 'fixture.html'));
const fixtureJs = fs.readFileSync(path.join(__dirname, 'fixture.js'));
const fixtureCss = fs.readFileSync(path.join(__dirname, 'fixture.css'));
const server = http.createServer((req, res) => {
  if (req.url === '/download') {
    res.writeHead(200, { 'Content-Type': 'text/plain', 'Content-Disposition': 'attachment; filename="lab-download.txt"' });
    return res.end('browser lab download\n');
  }
  if (req.url === '/fixture.js') { res.setHeader('Content-Type', 'text/javascript'); return res.end(fixtureJs); }
  if (req.url === '/fixture.css') { res.setHeader('Content-Type', 'text/css'); return res.end(fixtureCss); }
  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  res.setHeader('Content-Security-Policy', "frame-ancestors 'none'");
  if (req.url === '/trusted') return res.end('<title>TRUSTED-ASTRION-LAB</title><p id="secret">trusted-marker-731</p>');
  if (req.url === '/popup') return res.end('<title>LAB-POPUP</title><p>popup</p>');
  res.end(fixture);
});
let electronApp;
const main = async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const started = performance.now();
  electronApp = await _electron.launch({
    executablePath: require(path.join(root, 'desktop-electron/node_modules/electron')),
    args: [path.join(__dirname, 'electron-harness.cjs')],
    cwd: root,
    env: { ...process.env, BROWSER_LAB_OUTPUT: output, BROWSER_LAB_ORIGIN: origin },
    timeout: 25000,
  });
  await electronApp.evaluate(async () => {
    for (let attempt = 0; attempt < 100 && !global.browserLab?.ready; attempt++) await new Promise(r => setTimeout(r, 50));
    if (!global.browserLab?.ready) throw new Error('harness did not become ready');
  });
  const ready = JSON.parse(fs.readFileSync(path.join(output, 'harness-ready.json')));
  record('native_child_view_and_runtime_reuse', { ...ready, readyMs: performance.now() - started });
  assert.equal(ready.offscreen, false);
  assert.equal(ready.sameSession, false);
  const pages = electronApp.context().pages();
  const page = pages.find(item => item.url() === `${origin}/fixture`);
  assert.ok(page, 'native browser WebContentsView is visible to Playwright');
  page.setDefaultTimeout(8000);
  await page.getByLabel('Message').fill('中文输入 Electron 原生视图');
  await page.getByRole('button', { name: 'Apply', exact: true }).click();
  assert.equal(await page.locator('#result').textContent(), '中文输入 Electron 原生视图');
  record('playwright_locator_on_embedded_webcontentsview', { chromium: ready.versions.chrome, playwright: require(path.join(root, 'node_modules/playwright-core/package.json')).version });
  await electronApp.evaluate(async () => {
    const wc = global.browserLab.views.browser.webContents;
    wc.focus();
    wc.sendInputEvent({ type: 'mouseDown', x: 35, y: 35, button: 'left', clickCount: 1 });
    wc.sendInputEvent({ type: 'mouseUp', x: 35, y: 35, button: 'left', clickCount: 1 });
    await wc.executeJavaScript('document.querySelector("input").focus()');
    wc.insertText(' + native input');
  });
  await page.getByRole('button', { name: 'Apply', exact: true }).click();
  assert.match(await page.locator('#result').textContent(), /native input/);
  record('native_input_and_playwright_share_same_page', { value: await page.locator('#result').textContent(), note: 'Electron native input injection, not a human IME acceptance test' });
  const privileged = pages.find(item => item.url() === `${origin}/trusted`);
  const secret = await privileged.locator('#secret').textContent();
  assert.equal(secret, 'trusted-marker-731');
  record('unrestricted_electron_automation_reaches_trusted_view', { pageCount: pages.length, readTrustedMarker: true, conclusion: 'Direct _electron context is not a scoped browser-tool boundary' });
  const { ScopedPageService } = require('./scoped-page-service.cjs');
  const adapter = new ScopedPageService([['browser-a', page]]);
  await adapter.execute({ tabId: 'browser-a', action: 'fill', selector: '#message', value: 'bounded tools reuse original Playwright' });
  await adapter.execute({ tabId: 'browser-a', action: 'click', selector: '#apply' });
  assert.equal(await adapter.execute({ tabId: 'browser-a', action: 'text', selector: '#result' }), 'bounded tools reuse original Playwright');
  await assert.rejects(adapter.execute({ tabId: 'trusted', action: 'text', selector: '#secret' }), /unknown_or_unowned_tab/);
  await assert.rejects(adapter.execute({ tabId: 'browser-a', action: 'rawCDP' }), /unsupported_action/);
  assert.equal(await privileged.locator('#secret').textContent(), 'trusted-marker-731');
  record('bounded_tool_adapter_no_playwright_fork', { exposedTabs: adapter.list(), allowedActions: ['fill', 'click', 'text'], unownedTabRejected: true, rawCDPRejected: true, note: 'Minimal API feasibility only; not complete production authorization' });
  await page.evaluate(() => { localStorage.setItem('labStorage', 'browser-a'); document.cookie = 'labCookie=browser-a; path=/'; });
  const trustedStorage = await privileged.evaluate(() => ({ local: localStorage.getItem('labStorage'), cookie: document.cookie }));
  assert.equal(trustedStorage.local, null);
  assert.equal(trustedStorage.cookie, '');
  await electronApp.evaluate(() => global.browserLab.createIsolatedView());
  const other = electronApp.context().pages().find(item => item !== page && item.url() === `${origin}/fixture`);
  assert.ok(other);
  assert.equal(await other.evaluate(() => localStorage.getItem('labStorage')), null);
  record('session_partition_isolates_cookie_and_localstorage', { trustedStorage, secondBrowserStorage: null });
  const rendererBoundary = await page.evaluate(() => ({ node: typeof process, require: typeof require, desktopBridge: typeof window.__ASTRION_DESKTOP__, opener: window.opener === null }));
  assert.equal(rendererBoundary.node, 'undefined');
  assert.equal(rendererBoundary.require, 'undefined');
  assert.equal(rendererBoundary.desktopBridge, 'undefined');
  record('untrusted_renderer_has_no_astrion_or_node_bridge', rendererBoundary);
  const originalBounds = await electronApp.evaluate(() => global.browserLab.views.browser.getBounds());
  const bounds = await electronApp.evaluate(async () => {
    global.browserLab.window.setContentSize(1260, 820);
    await new Promise(r => setTimeout(r, 200));
    return { bounds: global.browserLab.views.browser.getBounds(), children: global.browserLab.window.contentView.children.length, nativeHandleBytes: global.browserLab.window.getNativeWindowHandle().length };
  });
  assert.notEqual(bounds.bounds.width, originalBounds.width);
  assert.equal(bounds.children, 2);
  record('native_view_resizes_as_child_not_floating_window', bounds);
  const file = path.join(output, 'upload.txt');
  fs.writeFileSync(file, 'browser lab upload');
  await page.getByLabel('Upload').setInputFiles(file);
  assert.equal(await page.locator('#file-result').textContent(), 'upload.txt');
  record('file_upload_on_embedded_view', { filename: 'upload.txt' });
  const downloadPath = path.join(output, 'downloads');
  fs.mkdirSync(downloadPath, { recursive: true });
  await electronApp.evaluate((_electron, { path: savePath }) => {
    global.browserLab.sessions.browser.once('will-download', (_event, item) => {
      item.setSavePath(savePath);
      item.once('done', (_ev, state) => { global.browserLab.downloadState = state; });
    });
  }, { path: path.join(downloadPath, 'lab-download.txt') });
  await page.getByRole('link', { name: 'Download' }).click();
  await electronApp.evaluate(async () => {
    for (let i = 0; i < 100 && !global.browserLab.downloadState; i++) await new Promise(r => setTimeout(r, 50));
    if (global.browserLab.downloadState !== 'completed') throw new Error('download failed: ' + global.browserLab.downloadState);
  });
  assert.equal(fs.readFileSync(path.join(downloadPath, 'lab-download.txt'), 'utf8'), 'browser lab download\n');
  record('download_on_embedded_view', { state: 'completed', navigationWait: 'default; prior failure was a harness argument bug, not browser incompatibility' });
  await electronApp.evaluate(async () => {
    const wc = global.browserLab.views.browser.webContents;
    wc.debugger.attach('1.3');
    const result = await wc.debugger.sendCommand('Runtime.evaluate', { expression: 'document.querySelector("#message").value', returnByValue: true });
    global.browserLab.debuggerResult = result.result.value;
    global.browserLab.primaryTargetId = (await wc.debugger.sendCommand('Target.getTargetInfo')).targetInfo.targetId;
    wc.debugger.detach();
  });
  record('native_debugger_coexists_with_playwright', { value: await electronApp.evaluate(() => global.browserLab.debuggerResult) });
  const scopeProbe = await electronApp.evaluate(async () => {
    const wc = global.browserLab.views.browser.webContents;
    wc.debugger.attach('1.3');
    try {
      const targets = await wc.debugger.sendCommand('Target.getTargets');
      const trusted = targets.targetInfos.find(item => item.title === 'TRUSTED-ASTRION-LAB');
      if (!trusted) return { targetsVisible: targets.targetInfos.length, trustedVisible: false };
      const { sessionId } = await wc.debugger.sendCommand('Target.attachToTarget', { targetId: trusted.targetId, flatten: true });
      const result = await wc.debugger.sendCommand('Runtime.evaluate', { expression: 'document.querySelector("#secret").textContent', returnByValue: true }, sessionId);
      await wc.debugger.sendCommand('Target.detachFromTarget', { sessionId });
      return { targetsVisible: targets.targetInfos.length, trustedVisible: true, trustedReadable: result.result.value === 'trusted-marker-731' };
    } catch (error) { return { error: String(error) }; }
    finally { wc.debugger.detach(); }
  });
  record('raw_webcontents_debugger_target_scope_probe', scopeProbe);
  const [port, wsPath] = fs.readFileSync(path.join(output, 'electron-profile/DevToolsActivePort'), 'utf8').trim().split('\n');
  const cdpBrowser = await chromium.connectOverCDP(`ws://127.0.0.1:${port}${wsPath}`);
  const primaryTargetId = await electronApp.evaluate(() => global.browserLab.primaryTargetId);
  let cdpPage;
  for (const candidate of cdpBrowser.contexts().flatMap(context => context.pages())) {
    const probe = await candidate.context().newCDPSession(candidate);
    const { targetInfo } = await probe.send('Target.getTargetInfo');
    await probe.detach();
    if (targetInfo.targetId === primaryTargetId) cdpPage = candidate;
  }
  assert.ok(cdpPage, 'select exact owned native view by stable target ID, never by URL');
  cdpPage.setDefaultTimeout(8000);
  await cdpPage.getByLabel('Message').fill('connectOverCDP runtime reuse');
  record('cdp_attach_viewport_diagnostic', await cdpPage.evaluate(() => {
    const rect = document.querySelector('#apply').getBoundingClientRect();
    return { innerWidth, innerHeight, outerWidth, outerHeight, button: { x: rect.x, y: rect.y, width: rect.width, height: rect.height } };
  }));
  await cdpPage.getByRole('button', { name: 'Apply', exact: true }).click();
  assert.equal(await cdpPage.locator('#result').textContent(), 'connectOverCDP runtime reuse');
  record('connect_over_cdp_reuses_electron_chromium', { contexts: cdpBrowser.contexts().length, pagesVisible: cdpBrowser.contexts().flatMap(context => context.pages()).length, trustedVisible: cdpBrowser.contexts().flatMap(context => context.pages()).some(item => item.url() === `${origin}/trusted`) });
  const contextCookiesBefore = await cdpPage.context().cookies(origin);
  await cdpPage.context().addCookies([{ name: 'pwContextCookie', value: 'context-write', url: origin }]);
  const cookieProbe = {
    pageCookies: await cdpPage.evaluate(() => document.cookie),
    contextCookiesBefore: contextCookiesBefore.map(item => item.name),
    contextCookiesAfter: (await cdpPage.context().cookies(origin)).map(item => item.name),
    trustedPageCookies: await privileged.evaluate(() => document.cookie),
    otherPageCookies: await other.evaluate(() => document.cookie),
  };
  record('playwright_context_cookie_api_vs_electron_partitions', cookieProbe);
  await cdpBrowser.close();
  const png = await page.screenshot();
  assert.equal(png.readUInt32BE(0), 0x89504e47);
  record('on_demand_page_screenshot', { bytes: png.length, persisted: false, usedForDisplay: false });
  observations.finished = new Date().toISOString();
  observations.status = 'completed';
};
main().catch(error => {
  observations.status = 'failed';
  observations.error = String(error.stack || error);
  console.error(observations.error);
  process.exitCode = 1;
}).finally(async () => {
  if (electronApp) await electronApp.close().catch(() => {});
  await new Promise(resolve => server.close(resolve));
  fs.writeFileSync(path.join(output, 'electron-results.json'), JSON.stringify(observations, null, 2));
});
