import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';
import asar from '../../desktop-electron/node_modules/@electron/asar/lib/asar.js';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const output = path.join(root, 'output/windows-electron/cross-shell');
const runRoot = path.join(output, 'run');
const installDir = path.join(output, 'installed');
const executable = path.join(installDir, 'astrion-crossshell-legacy.exe');
const installer = path.join(output, 'electron/Astrion-CrossShell-Electron-0.10.1.exe');
const signature = fs.readFileSync(`${installer}.sig`, 'utf8').trim();
const checks = [], requests = [], logs = [];
let badSignature = true, oldShell, oldBrowser, newBrowser, main, gateway;
const hash = file => createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const buildInputs = { legacyInstaller: hash(path.join(output, 'legacy-0.9.3-setup.exe')), electronInstaller: hash(installer), nsisHook: hash(path.join(root, 'desktop-electron/build/installer-windows.nsh')) };
const check = (name, ok, detail) => {
  checks.push({ name, ok: !!ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}: ${name}`);
  fs.writeFileSync(path.join(output, 'progress.json'), JSON.stringify(checks, null, 2));
  if (!ok) throw new Error(name);
};
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(fn, timeout = 120000) {
  const end = Date.now() + timeout; let error;
  while (Date.now() < end) { try { const result = await fn(); if (result) return result; } catch (e) { error = e; } await delay(500); }
  throw error || new Error('Cross-shell test condition timed out');
}
function run(exe, args, timeout = 1200000) {
  return new Promise((resolve, reject) => {
    const child = spawn(exe, args, { windowsHide: false, stdio: ['ignore', 'pipe', 'pipe'] });
    console.log('Starting own installer:', path.basename(exe), 'pid=', child.pid);
    const timer = setTimeout(() => { child.kill(); reject(new Error('Own installer timeout')); }, timeout);
    child.stdout.on('data', b => logs.push(b.toString())); child.stderr.on('data', b => logs.push(b.toString()));
    child.once('error', reject);
    child.once('exit', code => { clearTimeout(timer); code === 0 ? resolve() : reject(new Error(`Own installer exit ${code}`)); });
  });
}
function corruptSignature(value) {
  const lines = Buffer.from(value, 'base64').toString('utf8').split('\n');
  const bytes = Buffer.from(lines[1], 'base64');
  bytes[20] ^= 1;
  lines[1] = bytes.toString('base64');
  return Buffer.from(lines.join('\n')).toString('base64');
}
await fsp.mkdir(path.join(runRoot, 'workspace'), { recursive: true });
await fsp.mkdir(path.join(runRoot, 'data/config'), { recursive: true });
await fsp.writeFile(path.join(runRoot, 'data/config/host_workspaces.json'), JSON.stringify({ default_workspace_id: 'cross-shell', workspaces: [{ workspace_id: 'cross-shell', label: 'Cross-shell fixture', path: path.join(runRoot, 'workspace') }] }));
await fsp.writeFile(path.join(runRoot, 'data/config/custom_models.json'), JSON.stringify({ models: [{ model_name: 'cross-shell-local', model_id: 'local', url: 'http://127.0.0.1:19878/v1', apikey: 'synthetic-local-only', reasoning_capability: 'fast,thinking', context_window: 100000, max_output_tokens: 2048 }] }));
await fsp.writeFile(path.join(runRoot, 'quick-settings.json'), JSON.stringify({ enabled: false, modifier: 'alt', workspace: '', model: '' }));
const sentinel = path.join(runRoot, 'data/preserve-cross-shell.txt');
await fsp.writeFile(sentinel, 'preserve-cross-shell-runtime-data');
const env = { ...process.env, ASTRION_DESKTOP_DATA_ROOT: path.join(runRoot, 'data'), ASTRION_DESKTOP_SETTINGS_FILE: path.join(runRoot, 'rundata-settings.json'), ASTRION_QUICK_SETTINGS_FILE: path.join(runRoot, 'quick-settings.json'), ASTRION_QUICK_DISCOVERY_FILE: path.join(runRoot, 'gateway.json'), ASTRION_DESKTOP_INITIAL_WORKSPACE: path.join(runRoot, 'workspace'), WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: '--remote-debugging-address=127.0.0.1 --remote-debugging-port=19877', PYTHONUTF8: '1' };
delete env.ELECTRON_RUN_AS_NODE;
const feed = http.createServer((req, res) => {
  const pathname = new URL(req.url, 'http://127.0.0.1').pathname;
  requests.push({ path: pathname, badSignature });
  if (pathname === '/latest-windows-x86_64.json') {
    const manifest = { version: '0.10.1', notes: 'Isolated Tauri to Electron migration', pub_date: '2026-11-13T00:00:00Z', platforms: { 'windows-x86_64': { url: 'http://127.0.0.1:19878/electron-0.10.1.exe', signature: badSignature ? corruptSignature(signature) : signature } } };
    res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(manifest)); return;
  }
  if (pathname === '/electron-0.10.1.exe') {
    res.writeHead(200, { 'Content-Type': 'application/octet-stream', 'Content-Length': fs.statSync(installer).size });
    fs.createReadStream(installer).pipe(res); return;
  }
  res.writeHead(404); res.end();
});
async function api(page, route, data) {
  return page.evaluate(async ({ route, data }) => {
    const headers = { 'Content-Type': 'application/json' };
    if (data) headers['X-CSRF-Token'] = (await (await fetch('/api/csrf-token')).json()).token;
    return (await fetch(route, { method: data ? 'POST' : 'GET', headers, ...(data ? { body: JSON.stringify(data) } : {}) })).json();
  }, { route, data });
}
async function findMain(browser, legacy) {
  for (const page of browser.contexts()[0].pages()) if (await page.evaluate(legacy => !!window.__ASTRION_DESKTOP__ && !window.__ASTRION_CHROME__ && (legacy ? window.__ASTRION_PLATFORM__ === 'windows' : !!window.astrionBrowser), legacy).catch(() => false)) return page;
}
try {
  await new Promise((resolve, reject) => { feed.once('error', reject); feed.listen(19878, '127.0.0.1', resolve); });
  if (fs.existsSync(installDir)) throw new Error('Cross-shell installation already exists; inspect before retrying');
  await run(path.join(output, 'legacy-0.9.3-setup.exe'), ['/S', `/D=${installDir}`]);
  check('real legacy Tauri NSIS installs the unique test executable', fs.existsSync(executable) && !fs.existsSync(path.join(installDir, 'resources/app.asar')));
  const legacyHash = hash(executable);
  oldShell = spawn(executable, [], { env, windowsHide: false, stdio: ['ignore', 'pipe', 'pipe'] });
  oldShell.stdout.on('data', b => logs.push(b.toString())); oldShell.stderr.on('data', b => logs.push(b.toString()));
  oldBrowser = await until(() => chromium.connectOverCDP('http://127.0.0.1:19877', { timeout: 2000 }));
  main = await until(() => findMain(oldBrowser, true));
  await main.waitForFunction(() => document.querySelector('#app')?.__vue_app__?.config.globalProperties.$pinia?._s.get('conversationTabs')?.activeKey, null, { timeout: 90000 });
  const legacyPort = Number(new URL(main.url()).port);
  check('installed Tauri shell starts its real WebView2 and bundled backend', legacyPort > 0, { pid: oldShell.pid, port: legacyPort });
  check('Tauri in-app update accepts the first test request', (await api(main, '/api/desktop/update/install', {})).success);
  const rejected = await until(async () => { const result = await api(main, '/api/desktop/update/progress'); return result.data?.state === 'error' && result.data; });
  check('real Tauri updater rejects the tampered signature before installation', /signature|verify|verified|签名/i.test(rejected.error || '') && oldShell.exitCode === null && hash(executable) === legacyHash, rejected);
  badSignature = false;
  check('Tauri in-app update accepts the correctly signed Electron package', (await api(main, '/api/desktop/update/install', {})).success);
  await until(() => oldShell.exitCode !== null, 180000);
  check('Tauri downloads and verifies the signed package then exits the old shell', oldShell.exitCode === 0 && requests.some(row => row.path === '/electron-0.10.1.exe' && !row.badSignature));
  await until(() => JSON.parse(asar.extractFile(path.join(installDir, 'resources/app.asar'), 'package.json').toString()).version === '0.10.1', 1200000);
  check('cross-shell installer replaces the old installation in place', true);
  newBrowser = await until(() => chromium.connectOverCDP('http://127.0.0.1:19879', { timeout: 2000 }), 600000);
  const electronMain = await until(() => findMain(newBrowser, false));
  await electronMain.waitForFunction(() => document.querySelector('#app')?.__vue_app__?.config.globalProperties.$pinia?._s.get('conversationTabs')?.activeKey, null, { timeout: 90000 });
  gateway = JSON.parse(fs.readFileSync(path.join(runRoot, 'gateway.json'), 'utf8'));
  check('Electron launches automatically after the legacy updater exits', gateway.port > 0 && await electronMain.evaluate(() => window.__ASTRION_PLATFORM__) === 'win32', { port: gateway.port, pid: gateway.pid });
  check('ordinary runtime data and workspace settings survive the migration', fs.readFileSync(sentinel, 'utf8') === 'preserve-cross-shell-runtime-data' && fs.readFileSync(path.join(runRoot, 'data/config/host_workspaces.json'), 'utf8').includes('cross-shell'));
  const chrome = await until(async () => { for (const page of newBrowser.contexts()[0].pages()) if (await page.evaluate(() => !!window.__ASTRION_CHROME__).catch(() => false)) return page; });
  await chrome.locator('.chrome-tab-strip.is-windows').waitFor({ state: 'visible' });
  check('migrated Electron shows Windows settings and three topbar controls', await chrome.locator('.settings-btn').isVisible() && await chrome.locator('.window-controls .wc-btn').count() === 3);
  await electronMain.evaluate(() => window.astrionQuickSettings.configure({ enabled: false }));
  await chrome.locator('.wc-close').click();
  await until(async () => { try { await fetch(`http://127.0.0.1:${gateway.port}/api/health`); return false; } catch { return true; } }, 30000);
  check('migrated Electron exits and releases its bundled backend', true);
  const ownUninstaller = fs.readdirSync(installDir).find(name => /^Uninstall .*\.exe$/i.test(name));
  check('migration retires the old Tauri uninstaller and installs an Electron uninstaller', !fs.existsSync(path.join(installDir, 'uninstall.exe')) && !!ownUninstaller);
  await run(path.join(installDir, ownUninstaller), ['/S']);
  await until(() => !fs.existsSync(installDir), 600000);
  check('new Electron uninstaller removes only the migrated test installation', fs.readFileSync(sentinel, 'utf8') === 'preserve-cross-shell-runtime-data');
} catch (error) { checks.push({ name: 'exception', ok: false, detail: error.stack }); }
finally {
  if (oldShell && oldShell.exitCode === null) oldShell.kill();
  try { await oldBrowser?.close(); } catch {}
  try { await newBrowser?.close(); } catch {}
  feed.closeAllConnections(); await new Promise(resolve => feed.close(resolve));
  const result = { success: checks.length > 0 && checks.every(row => row.ok), checks, requests, buildInputs, installDir, runRoot };
  fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(result, null, 2));
  fs.writeFileSync(path.join(output, 'app.log'), logs.join(''));
  console.log(JSON.stringify(result, null, 2)); process.exitCode = result.success ? 0 : 1;
}
