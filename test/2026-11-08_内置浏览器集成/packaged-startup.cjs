// Run with Node; the preflight verifies Electron's CLI user-data isolation first.
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const { spawn, execFileSync } = require('node:child_process');
const { once } = require('node:events');

const root = path.resolve(__dirname, '../..');
const directory = path.join(root, '_experiments/browser-native-integration', `packaged-${Date.now()}`);
const bundle = path.resolve(process.env.BROWSER_STARTUP_BUNDLE || path.join(root, '_experiments/macos-browser-20261108/mac-arm64/Astrion.app'));
const relativeBundle = path.relative(path.join(root, '_experiments'), bundle);
if (relativeBundle.startsWith('..') || path.isAbsolute(relativeBundle) || !bundle.endsWith('.app')) throw new Error('local_validation_bundle_required');
const executable = path.join(bundle, 'Contents/MacOS/Astrion');
fs.mkdirSync(directory, { recursive: true });
const env = { ...process.env, ASTRION_DESKTOP_DATA_ROOT: path.join(directory, 'data') };
for (const key of Object.keys(env)) {
  if (/^(DATA_DIR|LOGS_DIR|USER_SPACE_DIR|API_USER_SPACE_DIR|DEPLOY_CONFIG_DIR|ELECTRON_RUN_AS_NODE|NODE_OPTIONS)$/.test(key)) delete env[key];
}
const processes = [];
const knownChildren = new Map();
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function start(binary, args, name) {
  const child = spawn(binary, args, { cwd: directory, env, stdio: ['ignore', 'pipe', 'pipe'] });
  const log = fs.createWriteStream(path.join(directory, `${name}.log`));
  child.stdout.pipe(log, { end: false });
  child.stderr.pipe(log, { end: false });
  child.on('close', () => log.end());
  processes.push(child);
  return child;
}

function childrenOf(pid) {
  const entries = execFileSync('/bin/ps', ['-axo', 'pid=,ppid=,args='], { encoding: 'utf8' })
    .split('\n').map(line => line.match(/^\s*(\d+)\s+(\d+)\s+(.*)$/)).filter(Boolean);
  const pids = new Set([pid]);
  for (let depth = 0; depth < 5; depth++) {
    for (const entry of entries) if (pids.has(Number(entry[2]))) {
      pids.add(Number(entry[1]));
      knownChildren.set(Number(entry[1]), entry[3]);
    }
  }
  return entries.filter(entry => Number(entry[2]) === pid);
}

function get(port, route, redirects = 0) {
  return new Promise((resolve, reject) => {
    const req = http.get({ host: '127.0.0.1', port, path: route, timeout: 1500 }, res => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        res.resume();
        const next = new URL(res.headers.location, `http://127.0.0.1:${port}${route}`);
        if (redirects >= 4 || next.origin !== `http://127.0.0.1:${port}`) return reject(new Error('unexpected_redirect'));
        return get(port, next.pathname + next.search, redirects + 1).then(resolve, reject);
      }
      const chunks = [];
      res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => resolve({ status: res.statusCode, body: Buffer.concat(chunks).toString() }));
    });
    req.on('error', reject);
    req.on('timeout', () => req.destroy(new Error('request_timeout')));
  });
}

async function main() {
  // This uses the same Electron version, before opening the packaged application.
  const probe = path.join(directory, 'probe.cjs');
  const userData = path.join(directory, 'electron');
  fs.writeFileSync(probe, `const { app } = require('electron');
    require('node:assert/strict').equal(app.getPath('userData'), ${JSON.stringify(userData)});
    require('node:assert/strict').equal(app.getPath('sessionData'), ${JSON.stringify(userData)});
    app.whenReady().then(() => app.exit(0));`);
  const preflight = start(path.join(root, 'desktop-electron/node_modules/.bin/electron'),
    [probe, `--user-data-dir=${userData}`], 'preflight');
  const [code] = await once(preflight, 'exit');
  assert.equal(code, 0, 'Electron CLI user-data isolation must pass before packaged startup');

  const child = start(executable, [`--user-data-dir=${userData}`], 'app');
  let port;
  let backendPid;
  const deadline = Date.now() + 45000;
  fs.writeFileSync(path.join(directory, 'progress.json'), JSON.stringify({ appPid: child.pid, phase: 'waiting-backend' }));
  while (Date.now() < deadline) {
    if (child.exitCode !== null || child.signalCode !== null) throw new Error('packaged_app_exited_early');
    const backend = childrenOf(child.pid).find(entry => entry[3].includes(bundle) && entry[3].includes('-m server.app'));
    if (backend) {
      backendPid = Number(backend[1]);
      const match = backend[3].match(/--port\s+(\d+)/);
      if (match) port = Number(match[1]);
    }
    if (port) {
      try {
        const health = await get(port, '/api/host-mode-enabled');
        if (health.status === 200) break;
      } catch { /* The backend is still starting. */ }
    }
    await sleep(350);
  }
  assert.ok(port && backendPid, 'packaged embedded backend must start');
  const health = await get(port, '/api/host-mode-enabled');
  assert.equal(health.status, 200);
  const html = await get(port, '/');
  assert.equal(html.status, 200);
  const manifest = JSON.parse(fs.readFileSync(path.join(bundle, 'Contents/Resources/runtime/backend/static/dist/.vite/manifest.json')));
  const asset = Object.values(manifest).find(entry => entry.isEntry && html.body.includes(entry.file));
  assert.ok(asset, 'served HTML must reference a packaged manifest entry');
  assert.equal((await get(port, `/static/dist/${asset.file}`)).status, 200);
  assert.ok(fs.existsSync(path.join(directory, 'data/host')), 'isolated runtime data must be used');
  childrenOf(child.pid);
  const result = { passed: true, appPid: child.pid, backendPid, port,
    checks: ['Electron CLI userData/sessionData isolation', 'packaged embedded Python starts',
      'isolated backend health returns 200', 'packaged HTML and hashed entry asset return 200', 'isolated data root created'] };
  fs.writeFileSync(path.join(directory, 'results.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ ...result, directory: path.relative(root, directory) }, null, 2));
}

main().catch(error => {
  fs.writeFileSync(path.join(directory, 'failure.json'), JSON.stringify({ passed: false, error: error.stack }, null, 2));
  console.error(error.stack);
  process.exitCode = 1;
}).finally(async () => {
  for (const child of processes) if (child.exitCode === null && child.signalCode === null) child.kill('SIGTERM');
  await sleep(1000);
  for (const child of processes) if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
  for (const [pid, command] of knownChildren) {
    try {
      const current = execFileSync('/bin/ps', ['-p', String(pid), '-o', 'args='], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
      if (current === command.trim() && current.includes(bundle)) process.kill(pid, 'SIGKILL');
    } catch { /* Already exited; no unrelated process is targeted. */ }
  }
  for (const child of processes) { child.stdout.destroy(); child.stderr.destroy(); }
  fs.writeFileSync(path.join(directory, 'cleanup.json'), JSON.stringify({ completed: true, ownedAppPids: processes.map(child => child.pid) }));
});
