import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

// 专门诊断 /chrome 页面：先挂监听再 reload，抓脚本加载与 Vue 挂载错误。
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const executable = process.env.ASTRION_SMOKE_EXE || path.join(root, 'output/windows-electron/build/win-unpacked/astrion-desktop.exe');
const output = path.join(root, `output/windows-electron/probe3-${Date.now()}`);
await fs.mkdir(path.join(output, 'workspace'), { recursive: true });
await fs.mkdir(path.join(output, 'data/config'), { recursive: true });
await fs.writeFile(path.join(output, 'data/config/host_workspaces.json'), JSON.stringify({
  default_workspace_id: 'electron-probe',
  workspaces: [{ workspace_id: 'electron-probe', label: 'Electron probe', path: path.join(output, 'workspace') }]
}));
const gatewayFile = path.join(output, 'gateway.json');
const env = {
  ...process.env,
  ASTRION_ELECTRON_USER_DATA: path.join(output, 'profile'),
  ASTRION_DESKTOP_DATA_ROOT: path.join(output, 'data'),
  ASTRION_DESKTOP_SETTINGS_FILE: path.join(output, 'rundata-settings.json'),
  ASTRION_QUICK_SETTINGS_FILE: path.join(output, 'quick-settings.json'),
  ASTRION_QUICK_DISCOVERY_FILE: gatewayFile,
  ASTRION_DESKTOP_INITIAL_WORKSPACE: path.join(output, 'workspace'),
  PYTHONUTF8: '1'
};
delete env.ELECTRON_RUN_AS_NODE;

const reserve = net.createServer();
await new Promise((resolve) => reserve.listen(0, '127.0.0.1', resolve));
const debugPort = reserve.address().port;
await new Promise((resolve) => reserve.close(resolve));

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const child = spawn(executable, [`--remote-debugging-port=${debugPort}`, '--remote-debugging-address=127.0.0.1'], {
  env, windowsHide: false, stdio: ['ignore', 'pipe', 'pipe']
});
const shellLog = [];
child.stdout.on('data', (b) => shellLog.push(String(b)));
child.stderr.on('data', (b) => shellLog.push(String(b)));

for (let i = 0; i < 160; i += 1) {
  try {
    const raw = await fs.readFile(gatewayFile, 'utf8');
    if (raw.trim()) break;
  } catch {
    // 等待后端
  }
  await delay(250);
}
await delay(2000);

const browser = await chromium.connectOverCDP(`http://127.0.0.1:${debugPort}`, { timeout: 8000 });
const context = browser.contexts()[0];
const events = [];

let chromePage = null;
for (let i = 0; i < 60 && !chromePage; i += 1) {
  for (const page of context.pages()) {
    try {
      if (page.url().includes('/chrome')) { chromePage = page; break; }
    } catch {
      // 忽略正在销毁的页面
    }
  }
  if (!chromePage) await delay(300);
}

const report = { shellLog: shellLog.join(''), events: [], state: null, reloaded: false };
if (chromePage) {
  chromePage.on('console', (m) => events.push({ type: 'console:' + m.type(), text: m.text().slice(0, 500) }));
  chromePage.on('pageerror', (e) => events.push({ type: 'pageerror', text: String(e).slice(0, 800) }));
  chromePage.on('requestfailed', (r) => events.push({ type: 'requestfailed', text: `${r.url()} :: ${r.failure()?.errorText}` }));
  chromePage.on('response', (r) => {
    if (r.status() >= 400) events.push({ type: 'http' + r.status(), text: r.url() });
  });
  try {
    await chromePage.reload({ waitUntil: 'domcontentloaded' });
    report.reloaded = true;
  } catch (error) {
    events.push({ type: 'reload-failed', text: String(error).slice(0, 300) });
  }
  await delay(6000);
  try {
    report.state = await chromePage.evaluate(() => ({
      url: location.href,
      scripts: [...document.scripts].map((s) => s.src),
      links: [...document.querySelectorAll('link[rel=stylesheet]')].map((l) => l.href),
      chromeAppHtmlLen: document.querySelector('#chrome-app')?.innerHTML.length ?? -1,
      bodyText: document.body.innerText.slice(0, 200),
      hasVue: !!document.querySelector('#chrome-app')?.__vue_app__,
      tabs: document.querySelector('#chrome-app')?.__vue_app__?.config.globalProperties.$pinia?._s.get('conversationTabs')?.tabs?.length ?? null
    }));
  } catch (error) {
    events.push({ type: 'state-failed', text: String(error).slice(0, 300) });
  }
}

report.events = events;
await fs.writeFile(path.join(root, 'output/windows-electron/probe-chrome.json'), JSON.stringify(report, null, 2));
child.kill();
process.exit(0);
