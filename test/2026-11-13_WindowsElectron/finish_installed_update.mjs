// Continue only the already-authorized isolated update after its installation
// exceeded an earlier test wait. Never reinstall or touch the production app.
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';
import asar from '../../desktop-electron/node_modules/@electron/asar/lib/asar.js';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const output = path.join(root, 'output/windows-electron/installer-test');
const resultPath = path.join(output, 'installed-update-results.json');
const previous = JSON.parse(fs.readFileSync(resultPath, 'utf8'));
const checks = previous.checks.filter(row => row.name !== 'exception');
if (checks.length !== 4 || checks.some(row => !row.ok)) throw new Error('Continuation requires four verified preceding update stages');
const installDir = path.resolve(previous.installDir);
if (installDir !== path.join(output, 'installed-app')) throw new Error('Unexpected installer-test directory');
const runRoot = path.join(output, 'run');
const installedExe = path.join(installDir, 'astrion-electron-migration-test.exe');
const powershell = path.join(process.env.SystemRoot, 'System32/WindowsPowerShell/v1.0/powershell.exe');
const metadata = () => JSON.parse(asar.extractFile(path.join(installDir, 'resources/app.asar'), 'package.json').toString());
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(fn, timeout = 600000) {
  const deadline = Date.now() + timeout; let error;
  while (Date.now() < deadline) { try { const value = await fn(); if (value) return value; } catch (e) { error = e; } await delay(400); }
  throw error || new Error('Continuation condition timed out');
}
function check(name, ok, detail) {
  checks.push({ name, ok: !!ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}: ${name}`);
  fs.writeFileSync(path.join(output, 'installed-update-progress.json'), JSON.stringify(checks, null, 2));
  if (!ok) throw new Error(name);
}
let browser, wizard;
const logs = [];
try {
  wizard = spawn(powershell, ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', path.join(root, 'test/2026-11-13_WindowsElectron/installer-wizard.ps1'), '-AllowedRoot', path.join(process.env.LOCALAPPDATA, 'astrion-electron-migration-test-updater')], { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  wizard.stdout.on('data', b => logs.push(b.toString())); wizard.stderr.on('data', b => logs.push(b.toString()));
  await until(() => metadata().version === '0.10.1');
  check('NSIS replaces the installed package with v0.10.1', metadata().name === 'astrion-electron-migration-test');
  const restarted = await until(async () => {
    const gateway = JSON.parse(fs.readFileSync(path.join(runRoot, 'gateway.json'), 'utf8'));
    const response = await fetch(`http://127.0.0.1:${gateway.port}/api/host-mode-enabled`);
    return response.ok && gateway;
  }, 300000);
  check('updated app restarts with the same isolated runtime data', fs.readFileSync(path.join(runRoot, 'data/preserve-sentinel.txt'), 'utf8') === 'preserve-test-runtime-data', { port: restarted.port, pid: restarted.pid });
  browser = await until(() => chromium.connectOverCDP('http://127.0.0.1:19876', { timeout: 2000 }), 30000);
  const main = await until(async () => {
    for (const page of browser.contexts()[0].pages()) if (await page.evaluate(() => !!window.astrionQuickSettings).catch(() => false)) return page;
  }, 30000);
  await main.waitForFunction(() => document.querySelector('#app')?.__vue_app__?.config.globalProperties.$pinia?._s.get('conversationTabs')?.activeKey, null, { timeout: 60000 });
  await main.evaluate(async () => {
    await window.astrionQuickSettings.configure({ enabled: false });
    const { token } = await (await fetch('/api/csrf-token')).json();
    await fetch('/api/desktop/window/control', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token }, body: JSON.stringify({ action: 'close' }) });
  }).catch(() => {});
  await until(async () => { try { await fetch(`http://127.0.0.1:${restarted.port}/api/host-mode-enabled`); return false; } catch { return true; } }, 20000);
  check('updated installed app closes and releases backend resources', true);
  const uninstaller = fs.readdirSync(installDir).find(name => /^Uninstall .*\.exe$/i.test(name));
  check('NSIS creates its own uninstaller', !!uninstaller);
  await new Promise((resolve, reject) => {
    const child = spawn(path.join(installDir, uninstaller), ['/S'], { windowsHide: false, stdio: 'ignore' });
    const timer = setTimeout(() => { child.kill(); reject(new Error('Uninstall timeout')); }, 300000);
    child.once('error', reject); child.once('exit', code => { clearTimeout(timer); code === 0 ? resolve() : reject(new Error(`Uninstall exit=${code}`)); });
  });
  await until(() => !fs.existsSync(installedExe), 300000);
  check('isolated NSIS uninstall removes application files', true);
  check('uninstall preserves runtime data', fs.readFileSync(path.join(runRoot, 'data/preserve-sentinel.txt'), 'utf8') === 'preserve-test-runtime-data');
} catch (error) {
  checks.push({ name: 'exception', ok: false, detail: error.stack });
} finally {
  try { await browser?.close(); } catch {}
  if (wizard && wizard.exitCode === null) wizard.kill();
  const result = { success: checks.every(row => row.ok), checks, installDir, runRoot, requests: previous.requests, continued_from: 'installed-update-resume.log' };
  fs.writeFileSync(resultPath, JSON.stringify(result, null, 2));
  fs.writeFileSync(path.join(output, 'installed-update-finish.log'), logs.join(''));
  console.log(JSON.stringify(result, null, 2));
  process.exitCode = result.success ? 0 : 1;
}
