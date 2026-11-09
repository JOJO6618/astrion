// Real Docker -> polling desktop client -> native browser diagnostic.
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const { _electron } = require('../../node_modules/playwright-core');
const root = path.resolve(__dirname, '../..');
const output = path.join(root, '_experiments/native-browser-feasibility-20261107/results/docker');
fs.mkdirSync(output, { recursive: true });
const token = crypto.randomBytes(32).toString('hex');
const result = { started: new Date().toISOString(), checks: [] };
const docker = args => execFileSync('docker', args, { encoding: 'utf8', timeout: 30000, env: { ...process.env, BROWSER_LAB_TOKEN: token }, stdio: ['ignore', 'pipe', 'pipe'] }).trim();
const record = (name, detail) => { result.checks.push({ name, status: 'pass', detail }); console.log(JSON.stringify({ name, detail })); };
let containerId;
let electronApp;
(async () => {
  containerId = docker(['run', '-d', '--rm', '--read-only', '--memory', '128m', '--cpus', '1', '--pids-limit', '64', '--tmpfs', '/tmp:size=16m', '-e', 'BROWSER_LAB_TOKEN', '-p', '127.0.0.1::8080', '--mount', `type=bind,source=${path.join(__dirname, 'docker-backend.py')},target=/lab/backend.py,readonly`, '--entrypoint', 'python3', 'python:3.11-slim', '-B', '/lab/backend.py']);
  const mapping = docker(['port', containerId, '8080/tcp']);
  const origin = `http://${mapping}`;
  for (let i = 0; i < 100; i++) {
    try { if ((await fetch(`${origin}/health`)).ok) break; } catch {}
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  assert.equal((await fetch(`${origin}/health`)).status, 200);
  assert.equal((await fetch(`${origin}/next`)).status, 401);
  assert.equal((await fetch(`${origin}/next`, { headers: { Authorization: 'Bearer incorrect' } })).status, 401);
  record('real_container_backend_and_basic_authentication', { image: 'python:3.11-slim', mapping, unauthenticated: 401, wrongToken: 401, note: 'Authentication demonstration only, not a production authorization audit' });
  const internalHealth = docker(['exec', containerId, 'python3', '-c', 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8080/health").status)']);
  assert.equal(internalHealth, '200');
  record('container_localhost_and_host_localhost_need_explicit_mapping', { containerPort: 8080, hostMapping: mapping, internalHealth });
  electronApp = await _electron.launch({ executablePath: require(path.join(root, 'desktop-electron/node_modules/electron')), args: [path.join(__dirname, 'electron-harness.cjs')], cwd: root, env: { ...process.env, BROWSER_LAB_OUTPUT: output, BROWSER_LAB_ORIGIN: origin }, timeout: 25000 });
  await electronApp.evaluate(async () => {
    for (let i = 0; i < 100 && !global.browserLab?.ready; i++) await new Promise(r => setTimeout(r, 50));
    if (!global.browserLab?.ready) throw new Error('native harness not ready');
  });
  const page = electronApp.context().pages().find(item => item.url() === `${origin}/fixture`);
  assert.ok(page);
  const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
  const { command } = await (await fetch(`${origin}/next`, { headers })).json();
  assert.equal(command.operation, 'fill');
  await page.locator(command.selector).fill(command.value);
  const actualValue = await page.locator(command.selector).inputValue();
  assert.equal(actualValue, 'Docker to local native view 中文');
  await fetch(`${origin}/result`, { method: 'POST', headers, body: JSON.stringify({ commandId: command.id, value: actualValue, pageUrl: page.url(), ok: true }) });
  const remoteResult = JSON.parse(docker(['exec', containerId, 'python3', '-c', 'import os,json,urllib.request; req=urllib.request.Request("http://127.0.0.1:8080/result",headers={"Authorization":"Bearer "+os.environ["BROWSER_LAB_TOKEN"]}); print(urllib.request.urlopen(req).read().decode())']));
  assert.equal(remoteResult.result.ok, true);
  record('docker_command_local_native_page_result_roundtrip', { commandId: command.id, value: remoteResult.result.value, offscreen: await electronApp.evaluate(() => global.browserLab.views.browser.webContents.isOffscreen()), displayFramesTransferred: 0 });
  const ready = JSON.parse(fs.readFileSync(path.join(output, 'harness-ready.json')));
  record('container_developer_app_loads_as_native_document', { nativeChildBounds: ready.bounds.browser, csp: (await fetch(`${origin}/app`)).headers.get('content-security-policy'), note: 'Explicit loopback port publication; no iframe, no response rewriting. Not an internet tunnel or arbitrary-port solution.' });
  const secondPoll = await (await fetch(`${origin}/next`, { headers })).json();
  assert.equal(secondPoll.command, null);
  record('polling_control_transport_no_inbound_desktop_command_endpoint', { polls: 2, note: 'Only commands/results over HTTP. Disconnect/cancel/ownership/multi-user not implemented by fixture.' });
  result.status = 'completed';
})().catch(error => { result.status = 'failed'; result.error = String(error.stack || error); console.error(result.error); process.exitCode = 1; }).finally(async () => {
  if (electronApp) await electronApp.close().catch(() => {});
  if (containerId) {
    try { docker(['rm', '-f', containerId]); result.containerRemoved = true; } catch (error) { result.cleanupError = String(error); }
  }
  result.finished = new Date().toISOString();
  fs.writeFileSync(path.join(output, 'docker-results.json'), JSON.stringify(result, null, 2));
});
