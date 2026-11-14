import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { createHash } from 'node:crypto';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const run = promisify(execFile);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const output = path.join(root, 'output/windows-update-real-20261114');
const build = path.join(output, 'build-next');
const installerName = 'Astrion-0.10.1-windows-x64-setup.exe';
const installer = path.join(build, installerName);
const launch = JSON.parse(fs.readFileSync(path.join(output, 'electron-launch.json'), 'utf8').replace(/^\uFEFF/, ''));
const logPath = path.join(output, 'electron-ui-actions.jsonl');
const requests = [];
const log = (event, details = {}) => {
  const row = { time: new Date().toISOString(), event, ...details };
  fs.appendFileSync(logPath, JSON.stringify(row) + '\n');
  console.log(JSON.stringify(row));
};
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const server = http.createServer((req, res) => {
  const pathname = new URL(req.url, 'http://127.0.0.1:19976').pathname;
  requests.push({ time: new Date().toISOString(), path: pathname, range: req.headers.range || null });
  if (req.method !== 'GET' && req.method !== 'HEAD') { res.writeHead(405); res.end(); return; }
  if (pathname === '/latest-windows-x86_64.json') {
    res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    res.end(JSON.stringify({ version: '0.10.1', notes: 'Unpublished full Electron 0.10.1 installation; real UI upgrade test.', pub_date: new Date().toISOString(), platforms: { 'windows-x86_64': { url: `http://127.0.0.1:19976/${installerName}` } } }));
    return;
  }
  const file = pathname === '/latest.yml' ? path.join(build, 'latest.yml') : pathname === '/' + installerName ? installer : null;
  if (!file) { res.writeHead(404); res.end(); return; }
  const size = fs.statSync(file).size;
  let start = 0, end = size - 1, status = 200;
  const range = /^bytes=(\d+)-(\d*)$/.exec(req.headers.range || '');
  if (range) {
    start = Number(range[1]); end = range[2] ? Number(range[2]) : end;
    if (start < 0 || end >= size || start > end) { res.writeHead(416, { 'Content-Range': `bytes */${size}` }); res.end(); return; }
    status = 206;
  }
  res.writeHead(status, { 'Content-Type': pathname.endsWith('.yml') ? 'text/yaml' : 'application/octet-stream', 'Content-Length': end - start + 1, 'Accept-Ranges': 'bytes', 'Cache-Control': 'no-store', ...(status === 206 ? { 'Content-Range': `bytes ${start}-${end}/${size}` } : {}) });
  if (req.method === 'HEAD') res.end(); else fs.createReadStream(file, { start, end }).pipe(res);
});
let browser;
let accepted = false;
try {
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(19976, '127.0.0.1', resolve); });
  log('real-test-feed-ready', { version: '0.10.1', installerBytes: fs.statSync(installer).size });
  browser = await chromium.connectOverCDP(launch.cdp, { timeout: 15000 });
  const page = browser.contexts().flatMap(context => context.pages()).find(candidate => /^http:\/\/127\.0\.0\.1:\d+\/(?:new|conversation|$)/.test(candidate.url()));
  if (!page) throw new Error('Real installed Electron application page not found');
  let lastProgress;
  page.on('response', async response => {
    const route = new URL(response.url()).pathname;
    if (!route.startsWith('/api/desktop/update/')) return;
    const body = await response.json().catch(() => null);
    if (route.endsWith('/progress')) {
      const p = body?.data;
      const state = JSON.stringify([p?.state, p?.error, p?.total ? Math.floor(p.downloaded / p.total * 10) : 0]);
      if (state === lastProgress) return;
      lastProgress = state;
    }
    log('real-update-response', { route, status: response.status(), body });
  });
  const skip = page.getByRole('button', { name: '暂不安装', exact: true });
  if (await skip.isVisible().catch(() => false)) await skip.click();
  const expand = page.getByRole('button', { name: '展开侧边栏', exact: true });
  if (await expand.isVisible().catch(() => false)) {
    await expand.click();
    await page.waitForFunction(() => (document.querySelector('.personal-page-btn')?.getBoundingClientRect().left ?? -1) >= 0);
    await pause(300);
  }
  if (await page.locator('.personal-page-btn').getAttribute('aria-expanded') !== 'true') await page.locator('.personal-page-btn').click();
  await page.locator('button.personal-menu-item').filter({ hasText: /^软件更新$/ }).click();
  const dialog = page.locator('.update-dialog');
  const button = dialog.getByRole('button', { name: '立即更新并重启', exact: true });
  await button.waitFor({ state: 'visible', timeout: 60000 });
  const text = await dialog.innerText();
  if (!text.includes('0.10.0') || !text.includes('0.10.1')) throw new Error('Wrong actual update offer: ' + text);
  fs.writeFileSync(path.join(output, 'electron-update-dialog.txt'), text);
  await dialog.screenshot({ path: path.join(output, 'electron-update-dialog.png') });
  const clickedAt = new Date().toISOString();
  await button.click();
  log('clicked-real-electron-update-and-restart', { current: '0.10.0', target: '0.10.1', clickedAt });
  const oldExitDeadline = Date.now() + 120000;
  while (browser.isConnected() && !page.isClosed() && Date.now() < oldExitDeadline) {
    await pause(500);
    const error = await page.locator('.update-error-detail').innerText({ timeout: 250 }).catch(() => '');
    if (error) throw new Error('Actual updater error: ' + error);
  }
  if (browser.isConnected() && !page.isClosed()) throw new Error('Old Electron did not quit');
  log('old-electron-exited');
  const deadline = Date.now() + 1000000;
  let observation;
  let attempt = 0;
  while (Date.now() < deadline) {
    attempt++;
    try {
      await run('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(root, 'test/2026-11-14_Windows更新实机/invoke-isolated.ps1'), '-Mode', 'Observe'], { cwd: root, timeout: 90000, windowsHide: true, maxBuffer: 1024 * 1024 });
      const current = JSON.parse(fs.readFileSync(path.join(output, 'migration-observation.json'), 'utf8').replace(/^\uFEFF/, ''));
      if (/^0\.10\.1(?:\.|$)/.test(current.executableVersion || '') && current.health?.success && current.appPid !== launch.pid && new Date(current.started) > new Date(clickedAt)) { observation = current; break; }
    } catch { /* The real installer may still be replacing the old directory. */ }
    if (attempt === 1 || attempt % 4 === 0) log('waiting-for-automatic-new-version', { attempt });
    await pause(5000);
  }
  if (!observation) throw new Error('New Electron did not automatically become healthy before the deadline');
  if (observation.payload.files < 15000 || observation.payload.missing.length || observation.payload.wrongSize.length || !observation.criticalHashes.every(row => row.matches) || !observation.sentinelPreserved) throw new Error('Installed payload or preserved runtime data did not pass verification');
  const result = { success: true, current: '0.10.0', target: '0.10.1', realUiClick: true, testFeedOnly: true, productionIdentityPreserved: true, clickedAt, installerSha256: createHash('sha256').update(fs.readFileSync(installer)).digest('hex'), observation, requests };
  fs.writeFileSync(path.join(output, 'electron-update-result.json'), JSON.stringify(result, null, 2));
  accepted = true;
  log('real-electron-update-accepted', { appPid: observation.appPid, version: observation.executableVersion, files: observation.payload.files, sentinelPreserved: true });
} catch (error) {
  fs.writeFileSync(path.join(output, 'electron-update-result.json'), JSON.stringify({ success: false, error: error.stack, requests }, null, 2));
  log('real-electron-update-failed', { error: error.message });
} finally {
  fs.writeFileSync(path.join(output, 'electron-feed-requests.json'), JSON.stringify(requests, null, 2));
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
  // Never send Browser.close to the application, successful or failed.
  process.exit(accepted ? 0 : 1);
}
