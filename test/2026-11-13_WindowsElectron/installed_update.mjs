import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { chromium } from 'playwright-core';
import asar from '../../desktop-electron/node_modules/@electron/asar/lib/asar.js';
import { verifyBusyRollback } from './rollback_guard.mjs';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const output = path.join(root, 'output/windows-electron/installer-test');
const runRoot = path.join(output, 'run');
let installDir = path.join(output, 'installed-app');
let installedExe = path.join(installDir, 'astrion-electron-migration-test.exe');
const feedDir = path.join(output, '0.10.1');
const checks = [], logs = [], requests = [];
const hash = file => createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const buildInputs = {
  nsisHookSha256: hash(path.join(root, 'desktop-electron/build/installer-windows.nsh')),
  installers: ['0.10.0', '0.10.1'].map(version => ({ version, sha256: hash(path.join(output, version, `Astrion-Electron-Test-${version}.exe`)) }))
};
const check = (name, ok, detail) => {
  checks.push({ name, ok: !!ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}: ${name}`);
  fs.writeFileSync(path.join(output, 'installed-update-progress.json'), JSON.stringify(checks, null, 2));
  if (!ok) throw new Error(name);
};
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(fn, timeout = 120000) {
  const deadline = Date.now() + timeout; let error;
  while (Date.now() < deadline) { try { const value = await fn(); if (value) return value; } catch (e) { error = e; } await delay(400); }
  throw error || new Error('Test condition timed out');
}
function run(command, args, timeout = 600000) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { windowsHide: false, stdio: ['ignore', 'pipe', 'pipe'] });
    console.log(`Starting isolated installer command: ${path.basename(command)} pid=${child.pid}`);
    const timer = setTimeout(() => { child.kill(); reject(new Error('Child command timeout')); }, timeout);
    const progress = setInterval(() => {
      const diagnostic = spawn(path.join(process.env.SystemRoot, 'System32/WindowsPowerShell/v1.0/powershell.exe'), ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', path.join(root, 'test/2026-11-13_WindowsElectron/installer-diagnostic.ps1')], { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
      diagnostic.stdout.on('data', b => { logs.push(b.toString()); console.log(b.toString().trim()); });
    }, 90000);
    child.stdout.on('data', b => logs.push(b.toString())); child.stderr.on('data', b => logs.push(b.toString()));
    child.once('error', error => { clearInterval(progress); clearTimeout(timer); reject(error); });
    child.once('exit', code => { clearInterval(progress); clearTimeout(timer); code === 0 ? resolve() : reject(new Error(`Child command exit ${code}`)); });
  });
}
const version = () => JSON.parse(asar.extractFile(path.join(installDir, 'resources/app.asar'), 'package.json').toString()).version;
await fsp.mkdir(path.join(runRoot, 'workspace'), { recursive: true });
await fsp.mkdir(path.join(runRoot, 'data/config'), { recursive: true });
await fsp.writeFile(path.join(runRoot, 'data/config/host_workspaces.json'), JSON.stringify({ default_workspace_id: 'install-test', workspaces: [{ workspace_id: 'install-test', label: 'Installer test', path: path.join(runRoot, 'workspace') }] }));
await fsp.writeFile(path.join(runRoot, 'data/config/custom_models.json'), JSON.stringify({ models: [{ model_name: 'local-fixture', model_id: 'local', url: 'http://127.0.0.1:19875/v1', apikey: 'synthetic-local-only', reasoning_capability: 'fast,thinking', context_window: 100000, max_output_tokens: 2048 }] }));
await fsp.writeFile(path.join(runRoot, 'data', 'preserve-sentinel.txt'), 'preserve-test-runtime-data');
const gatewayFile = path.join(runRoot, 'gateway.json');
const readGateway = () => JSON.parse(fs.readFileSync(gatewayFile, 'utf8'));
const env = { ...process.env, ASTRION_ELECTRON_USER_DATA: path.join(runRoot, 'profile'), ASTRION_DESKTOP_DATA_ROOT: path.join(runRoot, 'data'),
  ASTRION_DESKTOP_SETTINGS_FILE: path.join(runRoot, 'rundata-settings.json'), ASTRION_QUICK_SETTINGS_FILE: path.join(runRoot, 'quick-settings.json'),
  ASTRION_QUICK_DISCOVERY_FILE: gatewayFile, ASTRION_DESKTOP_INITIAL_WORKSPACE: path.join(runRoot, 'workspace') };
delete env.ELECTRON_RUN_AS_NODE;
const feed = http.createServer((req, res) => {
  const name = path.basename(decodeURIComponent(new URL(req.url, 'http://127.0.0.1').pathname));
  requests.push(name);
  const file = path.join(feedDir, name);
  if (!['latest.yml', 'Astrion-Electron-Test-0.10.1.exe', 'Astrion-Electron-Test-0.10.1.exe.blockmap'].includes(name) || !fs.existsSync(file)) { res.writeHead(404); res.end(); return; }
  const size = fs.statSync(file).size;
  const range = /^bytes=(\d+)-(\d*)$/.exec(req.headers.range || '');
  const start = range ? Number(range[1]) : 0, end = range && range[2] ? Math.min(Number(range[2]), size - 1) : size - 1;
  res.writeHead(range ? 206 : 200, { 'Content-Type': name.endsWith('.yml') ? 'text/yaml' : 'application/octet-stream', 'Content-Length': end - start + 1,
    'Accept-Ranges': 'bytes', ...(range ? { 'Content-Range': `bytes ${start}-${end}/${size}` } : {}) });
  fs.createReadStream(file, { start, end }).pipe(res);
});
let shell, browser, main, firstGateway;
try {
  await new Promise((resolve, reject) => { feed.once('error', reject); feed.listen(19875, '127.0.0.1', resolve); });
  const initialUninstaller = fs.existsSync(installDir) && fs.readdirSync(installDir).some(name => /^Uninstall .*\.exe$/i.test(name));
  if (!initialUninstaller || !fs.existsSync(installedExe) || version() !== '0.10.0') {
    await run(path.join(output, '0.10.0/Astrion-Electron-Test-0.10.0.exe'), ['/S', `/D=${installDir}`], 1200000);
  } else { console.log('Reusing the previously verified v0.10.0 test installation'); }
  if (!fs.existsSync(installedExe)) {
    const nested = path.join(installDir, 'Astrion Electron Migration Test');
    if (fs.existsSync(path.join(nested, 'astrion-electron-migration-test.exe'))) { installDir = nested; installedExe = path.join(nested, 'astrion-electron-migration-test.exe'); }
  }
  check('isolated NSIS installs v0.10.0 into the selected directory', fs.existsSync(installedExe) && version() === '0.10.0');
  const rollback = await verifyBusyRollback(root, installDir);
  check('busy-file update restores the old package', rollback.success, rollback);
  shell = spawn(installedExe, ['--remote-debugging-address=127.0.0.1', '--remote-debugging-port=19876'], { env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: false });
  shell.stdout.on('data', b => logs.push(b.toString())); shell.stderr.on('data', b => logs.push(b.toString()));
  firstGateway = await until(() => { const g = readGateway(); return g.pid === shell.pid && g; });
  const connect = () => chromium.connectOverCDP('http://127.0.0.1:19876', { timeout: 2000 });
  browser = await until(connect);
  const findMain = async () => {
    for (const page of browser.contexts()[0].pages()) if (await page.evaluate(() => !!window.astrionQuickSettings).catch(() => false)) return page;
  };
  main = await until(findMain);
  await main.waitForFunction(() => document.querySelector('#app')?.__vue_app__?.config.globalProperties.$pinia?._s.get('conversationTabs')?.activeKey, null, { timeout: 60000 });
  check('installed v0.10.0 launches its bundled backend', firstGateway.port > 0);
  await main.evaluate(() => window.astrionQuickSettings.configure({ enabled: false }));
  // Updates must remain silent; no UI automation advances or dismisses dialogs.
  // Any unexpected dialog therefore cannot be hidden by the test driver.
  const response = await main.evaluate(async () => {
    const { token } = await (await fetch('/api/csrf-token')).json();
    return (await fetch('/api/desktop/update/install', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token }, body: '{}' })).json();
  });
  check('in-app update starts through the real backend and shell bridge', response.success, response);
  await until(() => shell.exitCode !== null, 90000);
  check('real updater downloads the new NSIS package and exits the old shell', requests.includes('latest.yml') && requests.includes('Astrion-Electron-Test-0.10.1.exe'), { requests: [...new Set(requests)] });
  await until(() => version() === '0.10.1', 1200000);
  check('NSIS replaces the installed package with v0.10.1', true);
  const restarted = await until(async () => {
    const g = readGateway();
    if (g.pid === firstGateway.pid) return false;
    const response = await fetch(`http://127.0.0.1:${g.port}/api/host-mode-enabled`);
    return response.ok && g;
  }, 300000);
  check('updated app restarts with the same isolated runtime data', fs.readFileSync(path.join(runRoot, 'data/preserve-sentinel.txt'), 'utf8') === 'preserve-test-runtime-data', { port: restarted.port, pid: restarted.pid });
  browser = await until(connect);
  main = await until(findMain);
  await main.waitForFunction(() => document.querySelector('#app')?.__vue_app__?.config.globalProperties.$pinia?._s.get('conversationTabs')?.activeKey, null, { timeout: 60000 });
  await main.evaluate(async () => {
    await window.astrionQuickSettings.configure({ enabled: false });
    const { token } = await (await fetch('/api/csrf-token')).json();
    await fetch('/api/desktop/window/control', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token }, body: JSON.stringify({ action: 'close' }) });
  }).catch(() => {});
  await until(async () => { try { await fetch(`http://127.0.0.1:${restarted.port}/api/host-mode-enabled`); return false; } catch { return true; } }, 20000);
  check('updated installed app closes and releases backend resources', true);
  const uninstaller = (await fsp.readdir(installDir)).find(name => /^Uninstall .*\.exe$/i.test(name));
  check('NSIS creates its own uninstaller', !!uninstaller);
  await run(path.join(installDir, uninstaller), ['/S']);
  await until(() => !fs.existsSync(installDir), 600000);
  check('isolated NSIS uninstall removes application files', true);
  check('uninstall preserves runtime data', fs.readFileSync(path.join(runRoot, 'data/preserve-sentinel.txt'), 'utf8') === 'preserve-test-runtime-data');
} catch (error) {
  checks.push({ name: 'exception', ok: false, detail: error.stack });
} finally {
  try { await browser?.close(); } catch {}
  if (shell && shell.exitCode === null) shell.kill();
  feed.closeAllConnections();
  await new Promise(resolve => feed.close(resolve));
  const result = { success: checks.length > 0 && checks.every(c => c.ok), checks, installDir, runRoot, buildInputs, requests: [...new Set(requests)] };
  await fsp.writeFile(path.join(output, 'installed-update-results.json'), JSON.stringify(result, null, 2));
  await fsp.writeFile(path.join(output, 'installed-update.log'), logs.join(''));
  console.log(JSON.stringify(result, null, 2));
  process.exitCode = result.success ? 0 : 1;
}
