import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

// 增强诊断：保存主进程日志、页面 console/pageerror，并采样页面内容与视图增删。
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const executable = process.env.ASTRION_SMOKE_EXE || path.join(root, 'output/windows-electron/build/win-unpacked/astrion-desktop.exe');
const output = path.join(root, `output/windows-electron/probe2-${Date.now()}`);
const logFile = path.join(root, 'output/windows-electron/probe-0.10.3-main.log');
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
await fs.writeFile(logFile, '');
const appendLog = async (chunk) => { await fs.appendFile(logFile, chunk).catch(() => {}); };
child.stdout.on('data', (b) => appendLog(`[out] ${b}`));
child.stderr.on('data', (b) => appendLog(`[err] ${b}`));

for (let i = 0; i < 160; i += 1) {
  try {
    const raw = await fs.readFile(gatewayFile, 'utf8');
    if (raw.trim()) break;
  } catch {
    // 等待后端就绪
  }
  await delay(250);
}

await delay(2500);
const browser = await chromium.connectOverCDP(`http://127.0.0.1:${debugPort}`, { timeout: 8000 });
const context = browser.contexts()[0];
const events = [];
const attached = new Set();

const attach = (page) => {
  const key = page.url() + '|' + Math.random();
  if (attached.has(page)) return;
  attached.add(page);
  page.on('console', (msg) => events.push({ t: Date.now(), url: safeUrl(page), type: 'console:' + msg.type(), text: msg.text().slice(0, 400) }));
  page.on('pageerror', (error) => events.push({ t: Date.now(), url: safeUrl(page), type: 'pageerror', text: String(error).slice(0, 600) }));
  page.on('framenavigated', (frame) => { if (frame === page.mainFrame()) events.push({ t: Date.now(), url: safeUrl(page), type: 'navigate', text: frame.url() }); });
  page.on('close', () => events.push({ t: Date.now(), url: safeUrl(page), type: 'pageclosed', text: '' }));
  void key;
};
const safeUrl = (page) => { try { return page.url(); } catch { return '?'; } };

const samples = [];
for (let i = 0; i < 40; i += 1) {
  const rows = [];
  const pages = context.pages();
  for (const page of pages) {
    attach(page);
    try {
      const url = page.url();
      const info = await page.evaluate(() => {
        const app = document.querySelector('#app');
        return {
          title: document.title,
          htmlLen: document.documentElement.outerHTML.length,
          appHtmlLen: app ? app.innerHTML.length : -1,
          bodyText: document.body.innerText.slice(0, 120),
          hasApp: !!app,
          vue: !!app?.__vue_app__,
          chrome: window.__ASTRION_CHROME__ === true,
          platform: window.__ASTRION_PLATFORM__ || '',
          tabs: app?.__vue_app__?.config.globalProperties.$pinia?._s.get('conversationTabs')?.tabs?.length ?? null,
          activeKey: app?.__vue_app__?.config.globalProperties.$pinia?._s.get('conversationTabs')?.activeKey ?? null
        };
      });
      rows.push({ url, ...info });
    } catch (error) {
      rows.push({ url: '?', error: String(error).slice(0, 160) });
    }
  }
  samples.push({ t: i * 500, count: pages.length, rows });
  await delay(500);
}

await fs.writeFile(path.join(root, 'output/windows-electron/probe-multiview-2.json'), JSON.stringify({ samples, events }, null, 2));
child.kill();
process.exit(0);
