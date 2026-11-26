import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import http from 'node:http';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';
if (process.platform !== 'win32') throw new Error('Run the packaged smoke on Windows.');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const executable = await fs.realpath(process.env.ASTRION_SMOKE_EXE || path.join(root, 'output/windows-electron/build/win-unpacked/astrion-desktop.exe'));
const resources = path.join(path.dirname(executable), 'resources');
const expectedBackend = await fs.realpath(path.join(resources, 'runtime/rust/astrion-headless.exe'));
const output = path.join(root, `output/windows-electron/packaged-smoke-${Date.now()}`);
await fs.mkdir(path.join(output, 'workspace'), { recursive: true });
await fs.mkdir(path.join(output, 'data/config'), { recursive: true });
await fs.writeFile(path.join(output, 'data/config/host_workspaces.json'), JSON.stringify({
  default_workspace_id: 'electron-smoke', workspaces: [{ workspace_id: 'electron-smoke', label: 'Electron smoke', path: path.join(output, 'workspace') }]
}));
const checks = [], errors = [];
const check = (name, ok, detail) => { checks.push({ name, ok: !!ok, detail }); console.log(`${ok ? 'PASS' : 'FAIL'}: ${name}`); if (!ok) throw new Error(name); };
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(fn, ms = 90000) {
  const end = Date.now() + ms; let error;
  while (Date.now() < end) { try { const result = await fn(); if (result) return result; } catch (e) { error = e; } await delay(250); }
  throw error || new Error('Timed out waiting for test condition');
}
const reserve = net.createServer();
await new Promise(resolve => reserve.listen(0, '127.0.0.1', resolve));
const debugPort = reserve.address().port;
await new Promise(resolve => reserve.close(resolve));
const fixture = http.createServer((_req, res) => {
  res.writeHead(200, { 'Content-Type': 'text/html' });
  res.end('<title>Astrion packaged synthetic page</title><h1 id="fixture">Local browser fixture</h1><script>window.fixtureMarker="retained"</script>');
});
fixture.on('connect', (_request, socket) => socket.destroy());
await new Promise(resolve => fixture.listen(0, '127.0.0.1', resolve));
const fixtureURL = `http://127.0.0.1:${fixture.address().port}/fixture`;
await fs.writeFile(path.join(output, 'data/config/custom_models.json'), JSON.stringify({ models: [{
  model_name: 'electron-smoke-local', model_id: 'synthetic-local-model', display_name: 'Synthetic local smoke model',
  url: `http://127.0.0.1:${fixture.address().port}/v1`, apikey: 'synthetic-local-only',
  reasoning_capability: 'fast,thinking', context_window: 100000, max_output_tokens: 2048, multimodal: ['image']
}] }));
const gatewayFile = path.join(output, 'gateway.json');
const runtimeData = path.join(output, 'data/host/data');
const inherited = Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^(ASTRION_|AGENT_|WEB_|DATA_DIR$|LOGS_DIR$|DEPLOY_CONFIG_DIR$|HOST_WORKSPACES_FILE$)/i.test(key)));
const env = { ...inherited, ASTRION_ELECTRON_USER_DATA: path.join(output, 'profile'),
  ASTRION_DESKTOP_DATA_ROOT: path.join(output, 'data'), ASTRION_DESKTOP_SETTINGS_FILE: path.join(output, 'rundata-settings.json'),
  ASTRION_QUICK_SETTINGS_FILE: path.join(output, 'quick-settings.json'), ASTRION_QUICK_DISCOVERY_FILE: gatewayFile,
  ASTRION_DESKTOP_INITIAL_WORKSPACE: path.join(output, 'workspace'), DATA_DIR: runtimeData,
  DEPLOY_CONFIG_DIR: path.join(output, 'data/config'), LOGS_DIR: path.join(output, 'data/host/logs'),
  HOST_WORKSPACES_FILE: path.join(output, 'data/config/host_workspaces.json'), PYTHONUTF8: '1' };
delete env.ELECTRON_RUN_AS_NODE;
let child, browser, gateway, main;
const logs = [];
try {
  child = spawn(executable, [`--remote-debugging-port=${debugPort}`, '--remote-debugging-address=127.0.0.1',
    `--proxy-server=http://127.0.0.1:${fixture.address().port}`], { env, windowsHide: false, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', b => logs.push(b.toString())); child.stderr.on('data', b => logs.push(b.toString()));
  child.on('error', e => errors.push(e.message));
  gateway = await until(async () => JSON.parse(await fs.readFile(gatewayFile, 'utf8')));
  check('packaged backend becomes ready using isolated bundled runtime', gateway.port > 0 && gateway.pid === child.pid, { port: gateway.port, shellPid: child.pid });
  const origin = `http://127.0.0.1:${gateway.port}`;
  const backend = await until(async () => {
    const response = await fetch(origin + '/api/host-mode-enabled', { signal: AbortSignal.timeout(1500) });
    return response.ok && response.json();
  });
  check('default backend identifies itself as Rust', backend.backend === 'rust' && Number.isSafeInteger(backend.pid) && backend.pid > 0, backend);
  const powershell = path.join(env.SystemRoot, 'System32/WindowsPowerShell/v1.0/powershell.exe');
  const ancestryQuery = `$r=Get-CimInstance Win32_Process -Filter 'ProcessId=${backend.pid}';` +
    "$g=Get-CimInstance Win32_Process -Filter ('ProcessId=' + $r.ParentProcessId);" +
    '@{backend=$r.ExecutablePath; guardian=$g.ExecutablePath; shell=$g.ParentProcessId} | ConvertTo-Json -Compress';
  const ancestry = spawnSync(powershell, ['-NoProfile', '-NonInteractive', '-Command', ancestryQuery], {
    timeout: 15000, windowsHide: true, encoding: 'utf8', env,
  });
  check('Windows reports the running backend and guardian paths', ancestry.status === 0 && !!ancestry.stdout.trim(), { status: ancestry.status });
  const identity = JSON.parse(ancestry.stdout);
  const samePath = (candidate, expected) => typeof candidate === 'string' && path.resolve(candidate).toLowerCase() === path.resolve(expected).toLowerCase();
  check('Rust runs from the package under the native guardian', samePath(identity.backend, expectedBackend) &&
    samePath(identity.guardian, path.join(resources, 'astrion-windows-helper.exe')) && identity.shell === child.pid, identity);
  const token = (await fs.readFile(path.join(runtimeData, 'host_api_token'), 'utf8')).trim();
  const sessions = await fetch(origin + '/api/runtime/sessions', { headers: {
    authorization: `Bearer ${token}`, 'x-astrion-workspace-id': 'electron-smoke',
  }, signal: AbortSignal.timeout(3000) });
  check('bundled Rust answers an authenticated Gateway request offline', sessions.ok && (await sessions.json()).success === true);
  // 先探测 /json/version，确认调试端点就绪后再连接；从启动瞬间反复 connectOverCDP
  // 会在 Chromium 尚未监听时留下失败状态，之后的重试同样超时（0.10.3 后每次冒烟都卡在此处）。
  browser = await until(async () => {
    try {
      const probe = await fetch(`http://127.0.0.1:${debugPort}/json/version`, { signal: AbortSignal.timeout(2000) });
      if (!probe.ok) return null;
    } catch { return null; }
    return chromium.connectOverCDP(`http://127.0.0.1:${debugPort}`, { timeout: 10000 });
  });
  const context = browser.contexts()[0];
  let triedNewTab = false;
  main = await until(async () => {
    const pages = context.pages();
    for (const page of pages) if (await page.evaluate(() => !!window.astrionQuickSettings && !!window.astrionBrowser).catch(() => false)) return page;
    // 0.10.3 起初始无标签时不创建内容视图：先经 chrome 顶部按钮新建一个标签，
    // 主页面才会被创建（否则永远等不到内容视图）。
    if (!triedNewTab) {
      triedNewTab = true;
      const chromePage = pages.find(p => p.url().includes('/chrome'));
      if (chromePage) await chromePage.locator('.new-tab-btn').click({ timeout: 8000 }).catch(() => {});
    }
    return null;
  });
  main.on('pageerror', e => errors.push(e.message));
  await main.waitForFunction(() => document.querySelector('#app')?.__vue_app__ && document.body.innerText.length > 10, null, { timeout: 60000 });
  const platform = await main.evaluate(() => ({ platform: window.__ASTRION_PLATFORM__, popout: window.astrionBrowser.popout, text: document.body.innerText.length }));
  check('Windows main renderer and browser bridge load', platform.platform === 'win32' && platform.popout && platform.text > 10, platform);
  await main.waitForFunction(() => document.querySelector('#app')?.__vue_app__?.config.globalProperties.$pinia?._s.get('conversationTabs')?.activeKey, null, { timeout: 60000 });
  const chrome = await until(async () => {
    for (const page of context.pages()) if (await page.evaluate(() => window.__ASTRION_CHROME__ === true).catch(() => false)) return page;
  });
  chrome.on('pageerror', e => errors.push(e.message));
  check('independent chrome renderer receives Windows platform', await chrome.evaluate(() => window.__ASTRION_PLATFORM__) === 'win32');
  await chrome.locator('.chrome-tab-strip.is-windows').waitFor({ state: 'visible', timeout: 30000 });
  const controls = chrome.locator('.window-controls .wc-btn');
  check('Windows topbar displays settings and all three window buttons', await chrome.locator('.settings-btn').isVisible() && await controls.count() === 3 && (await Promise.all([0, 1, 2].map(index => controls.nth(index).isVisible()))).every(Boolean));
  await chrome.locator('.settings-btn').click();
  await main.locator('.settings-shell-back').waitFor({ state: 'visible', timeout: 30000 });
  check('topbar settings button opens the main settings page', new URL(main.url()).pathname === '/settings');
  await main.locator('.settings-shell-back').click();
  await main.waitForFunction(() => document.querySelector('#app')?.__vue_app__?.config.globalProperties.$pinia?._s.get('conversationTabs')?.activeKey, null, { timeout: 60000 });
  async function api(route, data) {
    return main.evaluate(async ({ route, data }) => {
      const headers = { 'Content-Type': 'application/json' };
      if (data) headers['X-CSRF-Token'] = (await (await fetch('/api/csrf-token')).json()).token;
      return (await fetch(route, { method: data ? 'POST' : 'GET', headers, ...(data ? { body: JSON.stringify(data) } : {}) })).json();
    }, { route, data });
  }
  const initialState = await api('/api/desktop/window/state');
  check('main-window control bridge returns state', initialState.success && typeof initialState.data?.maximized === 'boolean', initialState);
  await controls.nth(1).click();
  await until(async () => (await api('/api/desktop/window/state')).data?.maximized !== initialState.data.maximized, 10000);
  check('topbar maximize button changes the native window state', true);
  await controls.nth(1).click();
  await until(async () => (await api('/api/desktop/window/state')).data?.maximized === initialState.data.maximized, 10000);
  check('topbar restore button restores the native window state', true);
  await main.locator('button.sidebar-nav-row[aria-label="浏览器"]').click();
  await main.waitForFunction(() => document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('nativeBrowser')?.snapshot?.lease, null, { timeout: 20000 });
  await main.evaluate(url => document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('nativeBrowser').navigate(url), fixtureURL);
  const webPage = await until(() => context.pages().find(page => page.url() === fixtureURL), 20000);
  check('packaged browser navigates to local fixture', await webPage.locator('#fixture').innerText() === 'Local browser fixture');
  await webPage.evaluate(() => { window.fixtureMarker = 'preserved-after-popout'; });
  await main.evaluate(() => document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('nativeBrowser').command('popout'));
  const popout = await until(() => context.pages().find(page => new URL(page.url()).pathname === '/browser-window'), 15000);
  await popout.waitForFunction(() => !!window.astrionBrowser, null, { timeout: 15000 });
  check('browser opens a separate native window without reloading page', await webPage.evaluate(() => window.fixtureMarker) === 'preserved-after-popout');
  await main.evaluate(() => document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('nativeBrowser').command('dock'));
  await until(() => popout.isClosed(), 10000);
  check('browser docks back retaining page state', await webPage.evaluate(() => window.fixtureMarker) === 'preserved-after-popout');
  const passwordProtected = await main.evaluate(async origin => {
    const store = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('nativeBrowser');
    const lease = store.snapshot.lease;
    const saved = await window.astrionBrowser.credentials({ lease, action: 'save', value: { origin, username: 'synthetic-test-account', password: 'synthetic-local-password' } });
    const id = saved.records.find(row => row.username === 'synthetic-test-account').id;
    const revealed = await window.astrionBrowser.credentials({ lease, action: 'reveal', id });
    await window.astrionBrowser.credentials({ lease, action: 'remove', id });
    return revealed.password === 'synthetic-local-password';
  }, fixtureURL);
  check('Windows protected password save and reveal work without Touch ID', passwordProtected);
  await main.evaluate(() => window.astrionQuickSettings.configure({ enabled: true, modifier: 'alt' }));
  await main.evaluate(() => window.astrionQuickSettings.open());
  const quick = await until(async () => {
    for (const page of context.pages()) if (await page.evaluate(() => !!window.astrionQuick).catch(() => false)) return page;
  }, 15000);
  const quickInfo = await quick.evaluate(() => window.astrionQuick.info());
  check('Windows quick chat opens with configured modifier', quickInfo.enabled === true || quickInfo.config?.enabled === true, { keys: Object.keys(quickInfo), enabled: quickInfo.enabled ?? quickInfo.config?.enabled });
  await quick.keyboard.press('Escape');
  await main.evaluate(() => window.astrionQuickSettings.configure({ enabled: false }));
  await controls.nth(0).evaluate(button => button.click());
  const nativeState = spawnSync(path.join(process.env.SystemRoot, 'System32/WindowsPowerShell/v1.0/powershell.exe'), ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', path.join(root, 'test/2026-11-13_Windows顶栏/minimize_probe.ps1'), '-ShellPid', String(child.pid)], { timeout: 20000, windowsHide: true, encoding: 'utf8' });
  const minimized = nativeState.status === 0 && JSON.parse(nativeState.stdout.trim());
  check('topbar minimizes its real native window and the test restores it', minimized.minimized && minimized.restored, { status: nativeState.status, output: nativeState.stdout, error: nativeState.stderr });
  await chrome.locator('.wc-close').click({ noWaitAfter: true }).catch(() => {});
  await until(() => child.exitCode !== null, 20000);
  check('closing the app exits its shell', child.exitCode === 0, { code: child.exitCode });
  await until(async () => { try { await fetch(`http://127.0.0.1:${gateway.port}/api/host-mode-enabled`, { signal: AbortSignal.timeout(1000) }); return false; } catch { return true; } }, 15000);
  check('closing packaged app releases its backend port', true);
} catch (error) {
  checks.push({ name: 'exception', ok: false, detail: error.stack });
  if (main && !main.isClosed()) {
    const diagnostic = await main.evaluate(() => {
      const stores = document.querySelector('#app')?.__vue_app__?.config.globalProperties.$pinia?._s;
      const browser = stores?.get('nativeBrowser'), navigation = stores?.get('conversationTabs');
      return { url: location.href, text: document.body.innerText.slice(0, 3000), browser: browser && { open: browser.open, connecting: browser.connecting, error: browser.error, snapshot: browser.snapshot }, navigation: navigation?.$state, storeIds: stores ? [...stores.keys()] : [] };
    }).catch(e => ({ diagnosticError: e.message }));
    await fs.writeFile(path.join(output, 'diagnostic.json'), JSON.stringify(diagnostic, null, 2));
  }
} finally {
  if (child && child.exitCode === null) {
    try { await Promise.race([browser?.close(), delay(5000)]); } catch {}
    child.kill();
    await until(() => child.exitCode !== null, 10000).catch(() => {});
  }
  fixture.closeAllConnections();
  await new Promise(resolve => fixture.close(resolve));
  await fs.writeFile(path.join(output, 'app.log'), logs.join(''));
  const hash = async file => createHash('sha256').update(await fs.readFile(file)).digest('hex');
  const artifacts = { executable_sha256: await hash(executable), asar_sha256: await hash(path.join(resources, 'app.asar')), backend_sha256: await hash(expectedBackend) };
  const result = { success: checks.length > 0 && checks.every(row => row.ok), executable, output, artifacts, checks, rendererErrors: errors };
  await fs.writeFile(path.join(output, 'results.json'), JSON.stringify(result, null, 2));
  await fs.writeFile(path.join(root, 'output/windows-electron/packaged-smoke-latest.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
  process.exitCode = result.success ? 0 : 1;
}
