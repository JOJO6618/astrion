const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const net = require('node:net');
const { spawn, execFileSync } = require('node:child_process');
const assert = require('node:assert/strict');
const { chromium } = require('../../node_modules/playwright-core');
const root = path.resolve(__dirname, '../..');
const lab = path.join(root, '_experiments/native-browser-feasibility-20261107');
const output = path.join(lab, 'results/cef');
fs.mkdirSync(output, { recursive: true });
const distribution = JSON.parse(fs.readFileSync(path.join(__dirname, 'cef-distribution.json'))).selected;
const clientDirectory = fs.readdirSync(path.join(lab, 'cef-client')).find(name => name.endsWith('_client'));
const executable = path.join(lab, 'cef-client', clientDirectory, 'Release/cefclient.app/Contents/MacOS/cefclient');
const cases = [
  { name: 'chrome-views', args: ['--use-views'] },
  { name: 'chrome-use-native-request', args: ['--use-native'] },
  { name: 'alloy-native', args: ['--use-native', '--use-alloy-style'] },
];
const results = { version: distribution.cef_version, cases: [], note: 'Official sample modes, not an Electron embedding prototype; window metadata does not prove all alternate embedding techniques impossible.' };
const fixture = fs.readFileSync(path.join(__dirname, 'fixture.html'));
const server = http.createServer((req, res) => {
  if (req.url === '/fixture.js') { res.setHeader('Content-Type', 'text/javascript'); return res.end(fs.readFileSync(path.join(__dirname, 'fixture.js'))); }
  if (req.url === '/fixture.css') { res.setHeader('Content-Type', 'text/css'); return res.end(fs.readFileSync(path.join(__dirname, 'fixture.css'))); }
  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  res.end(fixture);
});
const freePort = async () => {
  const temporary = net.createServer();
  await new Promise(resolve => temporary.listen(0, '127.0.0.1', resolve));
  const port = temporary.address().port;
  await new Promise(resolve => temporary.close(resolve));
  return port;
};
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  for (const item of cases) {
    const profile = path.join(output, item.name);
    fs.mkdirSync(profile, { recursive: true });
    const port = await freePort();
    const args = [
      ...item.args, `--url=${origin}/fixture`, `--remote-debugging-port=${port}`,
      `--cache-path=${profile}/cache`, `--root-cache-path=${profile}`,
      `--log-file=${profile}/cef.log`, '--window-position=-2500,-2500', '--window-size=900,700',
    ];
    const log = fs.openSync(path.join(profile, 'process.log'), 'w');
    const child = spawn(executable, args, { cwd: root, detached: true, stdio: ['ignore', log, log] });
    fs.closeSync(log);
    let spawnError;
    child.on('error', error => { spawnError = String(error); });
    let browser;
    const result = { name: item.name, flags: item.args, status: 'started' };
    try {
      let endpoint;
      for (let attempt = 0; attempt < 160; attempt++) {
        if (spawnError) throw new Error(spawnError);
        if (child.exitCode !== null) throw new Error('cefclient exited with ' + child.exitCode);
        try {
          const response = await fetch(`http://127.0.0.1:${port}/json/version`);
          if (response.ok) { endpoint = (await response.json()).webSocketDebuggerUrl; break; }
        } catch {}
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      if (!endpoint) throw new Error('No CEF CDP endpoint within 16 seconds');
      browser = await chromium.connectOverCDP(endpoint, { timeout: 10000 });
      const page = browser.contexts().flatMap(context => context.pages()).find(page => page.url().startsWith(origin));
      assert.ok(page, 'CEF sample page exists');
      await page.getByLabel('Message').fill(`CEF ${item.name} 中文`);
      await page.getByRole('button', { name: 'Apply', exact: true }).click();
      result.pageResult = await page.locator('#result').textContent();
      assert.equal(result.pageResult, `CEF ${item.name} 中文`);
      const browserSession = await browser.newBrowserCDPSession();
      const pageSession = await page.context().newCDPSession(page);
      result.browserVersion = await browserSession.send('Browser.getVersion');
      const { targetInfo } = await pageSession.send('Target.getTargetInfo');
      result.window = await browserSession.send('Browser.getWindowForTarget', { targetId: targetInfo.targetId }).catch(error => ({ error: String(error) }));
      result.pageMetrics = await page.evaluate(() => ({ outerWidth, outerHeight, innerWidth, innerHeight, title: document.title }));
      result.nativeWindows = JSON.parse(execFileSync(path.join(lab, 'window-probe'), [String(child.pid)], { encoding: 'utf8', timeout: 10000 }));
      result.status = 'completed';
      await browserSession.send('Browser.close').catch(() => {});
    } catch (error) {
      result.status = 'failed';
      result.error = String(error.stack || error);
      process.exitCode = 1;
    } finally {
      if (browser) await browser.close().catch(() => {});
      if (child.exitCode === null && child.pid) {
        try { process.kill(-child.pid, 'SIGTERM'); } catch (error) { if (error.code !== 'ESRCH') result.cleanupError = String(error); }
      }
      const processLog = fs.readFileSync(path.join(profile, 'process.log'), 'utf8');
      result.runtimeLog = processLog.split('\n').filter(line => line.includes('Using ') || line.includes('requires Alloy'));
      results.cases.push(result);
      fs.writeFileSync(path.join(output, 'cef-results.json'), JSON.stringify(results, null, 2));
      console.log(JSON.stringify(result));
    }
  }
})().catch(error => { results.error = String(error.stack || error); console.error(results.error); process.exitCode = 1; }).finally(async () => {
  await new Promise(resolve => server.close(resolve));
  fs.writeFileSync(path.join(output, 'cef-results.json'), JSON.stringify(results, null, 2));
});
