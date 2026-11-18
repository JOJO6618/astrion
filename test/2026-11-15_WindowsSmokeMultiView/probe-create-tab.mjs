import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

// 实验：空标签启动后，由 chrome 页面手动创建首个标签，观察内容视图是否正常出现。
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const executable = process.env.ASTRION_SMOKE_EXE || path.join(root, 'output/windows-electron/build/win-unpacked/astrion-desktop.exe');
const output = path.join(root, `output/windows-electron/probe4-${Date.now()}`);
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
for (let i = 0; i < 160; i += 1) {
  try {
    if ((await fs.readFile(gatewayFile, 'utf8')).trim()) break;
  } catch {
    // 等待后端
  }
  await delay(250);
}
await delay(3000);

const browser = await chromium.connectOverCDP(`http://127.0.0.1:${debugPort}`, { timeout: 8000 });
const context = browser.contexts()[0];
const findChrome = () => context.pages().find((p) => { try { return p.url().includes('/chrome'); } catch { return false; } });

let chromePage = null;
for (let i = 0; i < 40 && !chromePage; i += 1) { chromePage = findChrome(); if (!chromePage) await delay(300); }

const result = { beforeTabs: null, created: null, afterTabs: null, pages: [], error: null };
if (chromePage) {
  result.beforeTabs = await chromePage.evaluate(() => document.querySelector('#chrome-app')?.__vue_app__?.config.globalProperties.$pinia?._s.get('conversationTabs')?.tabs?.length ?? null).catch(() => null);
  result.created = await chromePage.evaluate(async () => {
    const pinia = document.querySelector('#chrome-app')?.__vue_app__?.config.globalProperties.$pinia;
    const store = pinia?._s.get('conversationTabs');
    if (!store) return 'no-store';
    try {
      await store.addNewTab();
      return 'ok';
    } catch (error) {
      return String(error).slice(0, 300);
    }
  }).catch((e) => 'eval-failed: ' + String(e).slice(0, 200));
  await delay(6000);
  result.afterTabs = await chromePage.evaluate(() => document.querySelector('#chrome-app')?.__vue_app__?.config.globalProperties.$pinia?._s.get('conversationTabs')?.tabs?.length ?? null).catch(() => null);
  for (const page of context.pages()) {
    try {
      const info = await page.evaluate(() => ({
        hasApp: !!document.querySelector('#app'),
        vue: !!document.querySelector('#app')?.__vue_app__,
        textLen: document.body.innerText.length
      }));
      result.pages.push({ url: page.url(), ...info });
    } catch {
      result.pages.push({ url: page.url(), error: 'closed' });
    }
  }
} else {
  result.error = 'chrome page not found';
}

await fs.writeFile(path.join(root, 'output/windows-electron/probe-create-tab.json'), JSON.stringify(result, null, 2));
child.kill();
process.exit(0);
