const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const net = require('node:net');
const { spawn, execFileSync } = require('node:child_process');
const assert = require('node:assert/strict');
const { chromium } = require('../../node_modules/playwright-core');
const root = path.resolve(__dirname, '../..');
const lab = path.join(root, '_experiments/native-browser-feasibility-20261107/mixed-views');
const build = JSON.parse(fs.readFileSync(path.join(lab, 'build.json')));
const output = path.join(lab, 'run-' + Date.now());
fs.mkdirSync(output, { recursive: true });
const previous = path.resolve(__dirname, '../2026-11-07_内置浏览器可行性');
const directChrome = process.argv.includes('--direct');
const explicitNavigation = process.argv.includes('--explicit-navigation');
const finishLaunch = process.argv.includes('--finish-launch');
const requests = [];
const result = { started: new Date().toISOString(), output, directChrome, explicitNavigation, finishLaunch,
  globalContext: process.argv.includes('--global-context'), lateAttach: process.argv.includes('--late-attach'),
  args: process.argv.slice(2), observations: [] };
const record = (name, detail) => {
  result.observations.push({ name, detail });
  fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ name, detail }));
};
const server = http.createServer((req, res) => {
  requests.push({ url: req.url, time: new Date().toISOString() });
  res.setHeader('Content-Security-Policy', "frame-ancestors 'none'");
  if (req.url === '/fixture.js') { res.setHeader('Content-Type', 'text/javascript'); return res.end(fs.readFileSync(path.join(previous, 'fixture.js'))); }
  if (req.url === '/fixture.css') { res.setHeader('Content-Type', 'text/css'); return res.end(fs.readFileSync(path.join(previous, 'fixture.css'))); }
  if (req.url === '/popup') { res.setHeader('Content-Type', 'text/html'); return res.end('<title>LAB POPUP</title><p>popup</p>'); }
  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  let html = fs.readFileSync(path.join(previous, 'fixture.html'), 'utf8');
  if (req.url === '/trusted') html = html.replace('Native browser lab fixture', 'TRUSTED MIXED VIEW');
  res.end(html);
});
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const state = () => { try { return JSON.parse(fs.readFileSync(path.join(output, 'state.json'))); } catch { return null; } };
let commandId = 0;
const command = async (action, predicate = () => true) => {
  const seq = String(++commandId);
  fs.writeFileSync(path.join(output, 'command.tmp'), `${seq} ${action}`);
  fs.renameSync(path.join(output, 'command.tmp'), path.join(output, 'command.txt'));
  for (let i = 0; i < 100; i++) {
    const current = state();
    if (current?.sequence === seq && predicate(current)) return current;
    await sleep(100);
  }
  throw new Error('command did not settle: ' + action + ' ' + JSON.stringify(state()));
};
let child, browser;
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const temporary = net.createServer();
  await new Promise(resolve => temporary.listen(0, '127.0.0.1', resolve));
  const port = temporary.address().port;
  await new Promise(resolve => temporary.close(resolve));
  const log = fs.openSync(path.join(output, 'process.log'), 'w');
  child = spawn(build.executable, [], { cwd: root, detached: true, stdio: ['ignore', log, log], env: {
    ...process.env, BROWSER_LAB_OUTPUT: output, BROWSER_LAB_ORIGIN: origin,
    ...(directChrome ? { BROWSER_LAB_DIRECT_CHROME: '1' } : {}),
    ...(explicitNavigation ? { BROWSER_LAB_EXPLICIT_NAV: '1' } : {}),
    ...(finishLaunch ? { BROWSER_LAB_FINISH_LAUNCH: '1' } : {}),
    ...(process.argv.includes('--global-context') ? { BROWSER_LAB_GLOBAL_CONTEXT: '1' } : {}),
    BROWSER_LAB_CDP_PORT: String(port), BROWSER_LAB_FRAMEWORK: build.framework, BROWSER_LAB_HELPER: build.helper,
  } });
  fs.closeSync(log);
  let endpoint;
  for (let i = 0; i < 180; i++) {
    if (child.exitCode !== null || child.signalCode) throw new Error('host exited early: ' + child.exitCode + ' signal=' + child.signalCode + '\n' + fs.readFileSync(path.join(output, 'process.log'), 'utf8'));
    try { const response = await fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(1500) }); if (response.ok) { endpoint = (await response.json()).webSocketDebuggerUrl; break; } } catch {}
    await sleep(100);
  }
  if (!endpoint) throw new Error('CDP endpoint unavailable');
  if (process.argv.includes('--late-attach')) {
    for (let i=0;i<100;i++) {
      if (state()?.leftUrl && state()?.rightUrl) break;
      await sleep(100);
    }
    record('before_playwright_attach',{state:state(),requests:[...requests]});
  }
  browser = await chromium.connectOverCDP(endpoint, { noDefaults: true, timeout: 10000 });
  for (let i = 0; i < 100; i++) {
    const loaded=browser.contexts().flatMap(x=>x.pages());
    if (state() && loaded.some(x=>x.url()===`${origin}/trusted`) && loaded.some(x=>x.url()===`${origin}/fixture`)) break;
    await sleep(100);
  }
  await sleep(500);
  const initial = state();
  record('mixed_views_initial_geometry', initial);
  assert.ok(initial, 'native state snapshot');
  assert.equal(initial.windowStyle, 1, 'Chrome window enum expected');
  assert.equal(initial.leftStyle, 2, 'Alloy left view enum expected');
  assert.equal(initial.rightStyle, 1, 'Chrome right view enum expected');
  assert.ok(initial.left.sameWindow && initial.right.sameWindow, 'both views belong to exact same CEF window');
  const pages = browser.contexts().flatMap(x => x.pages());
  record('native_browser_target_urls', pages.map(page => ({url:page.url()})));
  const left = pages.find(x => x.url() === `${origin}/trusted`);
  const right = pages.find(x => x.url() === `${origin}/fixture`);
  if (!left || !right) {
    const processes = execFileSync('/bin/ps', ['-axo','pid,ppid,command'], {encoding:'utf8'}).split('\n').filter(line=>line.includes('/mixed-views/'));
    record('startup_processes', processes);
    if (process.argv.includes('--sample')) {
      for (const kind of ['NetworkService','--type=renderer']) {
        const line=processes.find(line=>line.includes(kind));
        if (line) {
          const pid=line.trim().split(/\s+/)[0];
          try { execFileSync('/usr/bin/sample',[pid,'1','-file',path.join(output,kind==='NetworkService' ? 'network-sample.txt' : 'renderer-sample.txt')],{timeout:10000,stdio:'ignore'}); }
          catch (error) { record('sample_error',{kind,error:String(error)}); }
        }
      }
    }
    const diagnostics = await Promise.all(pages.map(async (page,index) => {
      try { await page.goto(`${origin}/${index === 0 ? 'fixture' : 'trusted'}`, {timeout:15000,waitUntil:'domcontentloaded'}); return {url:page.url()}; }
      catch (error) { return {url:page.url(),error:String(error)}; }
    }));
    record('cdp_navigation_diagnostics',diagnostics);
  }
  assert.ok(left && right);
  left.setDefaultTimeout(8000); right.setDefaultTimeout(8000);
  await right.getByLabel('Message').fill('右侧 Chrome BrowserView 中文');
  await right.getByRole('button', { name: 'Apply', exact: true }).click();
  assert.equal(await right.locator('#result').textContent(), '右侧 Chrome BrowserView 中文');
  await left.getByLabel('Message').fill('左侧 Astrion 合成页面');
  await left.getByRole('button', { name: 'Apply', exact: true }).click();
  record('both_native_views_operable_with_unmodified_playwright', { leftValue: await left.locator('#result').textContent(), rightValue: await right.locator('#result').textContent() });
  await right.evaluate(() => { document.cookie='mixedRight=right; path=/'; localStorage.setItem('mixed','right'); });
  const isolated = await left.evaluate(() => ({ cookie: document.cookie, local: localStorage.getItem('mixed') }));
  assert.equal(isolated.cookie, ''); assert.equal(isolated.local, null);
  record('separate_cef_request_context_storage', { isolated, playwrightContexts: browser.contexts().length });
  for (const [width, height] of [[1440,860], [960,680], [1200,760]]) {
    const changed = await command(`resize ${width} ${height}`, x => x.window.relative.width === width);
    record('resize_' + width, changed);
  }
  const hidden = await command('hide-right', x => !x.rightPanel.visible);
  record('collapse_browser_panel', hidden);
  const shown = await command('show-right', x => !!x.rightPanel.visible);
  record('restore_browser_panel', shown);
  await command('focus-left');
  record('native_focus_left', { focused: await left.evaluate(() => document.hasFocus()) });
  await command('focus-right');
  record('native_focus_right', { focused: await right.evaluate(() => document.hasFocus()) });
  const full = await command('fullscreen 1', x => !!x.fullscreen);
  await sleep(1000);
  record('fullscreen_geometry', state() || full);
  await command('fullscreen 0', x => !x.fullscreen);
  await sleep(1000);
  const popupPromise = right.waitForEvent('popup');
  await right.getByRole('link', { name: 'Open popup' }).click();
  const popup = await popupPromise;
  await popup.waitForLoadState();
  record('popup_default_behavior', { popupUrl: popup.url(), totalPages: browser.contexts().flatMap(x => x.pages()).length, note: 'Default popup handling; determine whether it stays in panel before product integration' });
  await popup.close();
  fs.writeFileSync(path.join(output, 'command.txt'), `${++commandId} close`);
  for (let i=0; i<100 && child.exitCode === null; i++) await sleep(100);
  record('graceful_window_close', { exitCode: child.exitCode, signal: child.signalCode });
  assert.equal(child.exitCode, 0);
  result.status = 'completed';
})().catch(error => { result.status='failed'; result.error=String(error.stack || error); console.error(result.error); process.exitCode=1; }).finally(async () => {
  if (browser) await browser.close().catch(() => {});
  if (child?.pid && child.exitCode === null && !child.signalCode) {
    try { process.kill(-child.pid, 'SIGTERM'); } catch (error) { if (error.code !== 'ESRCH') result.cleanupError=String(error); }
    for (let i=0;i<30 && child.exitCode === null && !child.signalCode;i++) await sleep(100);
    if (child.exitCode === null && !child.signalCode) {
      try { process.kill(-child.pid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') result.cleanupError=String(error); }
      for (let i=0;i<20 && child.exitCode === null && !child.signalCode;i++) await sleep(100);
    }
  }
  await new Promise(resolve => server.close(resolve));
  result.finished = new Date().toISOString();
  result.requests = requests;
  result.hostProcess = child ? { pid: child.pid, exitCode: child.exitCode, signalCode: child.signalCode } : null;
  fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(result, null, 2));
  fs.writeFileSync(path.join(lab, 'latest-run.json'), JSON.stringify({ output, status: result.status }, null, 2));
});
