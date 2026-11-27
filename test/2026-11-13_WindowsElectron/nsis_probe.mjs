import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import asar from '../../desktop-electron/node_modules/@electron/asar/lib/asar.js';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const output = path.join(root, 'output/windows-electron/nsis-probe');
const installed = path.join(output, 'installed');
const checks = [];
const hash = file => createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const check = (name, ok, detail) => { checks.push({ name, ok: !!ok, detail }); console.log(`${ok ? 'PASS' : 'FAIL'} ${name}`, detail || ''); if (!ok) throw new Error(name); };
function run(executable, args) {
  const result = spawnSync(executable, args, { windowsHide: false, timeout: 120000, encoding: 'utf8' });
  console.log(path.basename(executable), { status: result.status, error: result.error?.message });
  return result;
}
if (process.argv.includes('--prepare')) {
  for (const version of ['0.10.0', '0.10.1']) {
    const payload = path.join(output, version, 'payload');
    const app = path.join(output, version, 'app');
    fs.mkdirSync(path.join(payload, 'resources'), { recursive: true });
    fs.mkdirSync(app, { recursive: true });
    fs.copyFileSync(path.join(root, 'desktop-electron/.cache/astrion-windows-helper.exe'), path.join(payload, 'astrion-nsis-probe.exe'));
    fs.writeFileSync(path.join(app, 'package.json'), JSON.stringify({ name: 'astrion-nsis-probe', version, main: 'index.js' }));
    fs.writeFileSync(path.join(app, 'index.js'), '// Probe binary is never executed.\n');
    await asar.createPackage(app, path.join(payload, 'resources/app.asar'));
    fs.mkdirSync(path.join(payload, 'nested/deeper'), { recursive: true });
    fs.writeFileSync(path.join(payload, 'nested/deeper/version.txt'), version);
  }
  console.log('Prepared isolated small payloads');
  process.exit(0);
}
fs.mkdirSync(output, { recursive: true });
try {
  let result = run(path.join(output, '0.10.0/Astrion-NSIS-Probe-0.10.0.exe'), ['/S', `/D=${installed}`]);
  check('initial small NSIS installation', result.status === 0 && fs.existsSync(path.join(installed, 'astrion-nsis-probe.exe')), result.status);
  const longFile = path.join(installed, 'long'.repeat(12), 'nested'.repeat(10), 'references'.repeat(7), 'long-path-marker.txt');
  fs.mkdirSync(path.dirname(longFile), { recursive: true });
  fs.writeFileSync(longFile, 'synthetic path longer than MAX_PATH');
  check('fixture exceeds Windows MAX_PATH', longFile.length > 260, longFile.length);
  const uninstaller = fs.readdirSync(installed).find(name => /^Uninstall .*\.exe$/i.test(name));
  const outside = path.join(output, 'probe-uninstaller.exe');
  fs.copyFileSync(path.join(installed, uninstaller), outside);
  const marker = path.join(installed, 'zz-locked.txt');
  const moved = path.join(installed, 'AA-preserved.txt');
  fs.writeFileSync(marker, 'locked'); fs.writeFileSync(moved, 'preserved');
  const oldHash = hash(path.join(installed, 'resources/app.asar'));
  // Use a separate lock process with a readiness file; spawnSync blocks Node events.
  const ready = path.join(output, 'locker-ready');
  const stop = path.join(output, 'locker-stop');
  for (const file of [ready, stop]) if (fs.existsSync(file)) fs.unlinkSync(file);
  const { spawn } = await import('node:child_process');
  const locker = spawn(process.env.ASTRION_TEST_PYTHON || 'python', ['-u', '-B', path.join(root, 'test/2026-11-13_WindowsElectron/rollback_locker.py'), marker], { stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
  try {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Locker readiness timeout')), 10000);
      locker.stdout.on('data', b => { if (b.toString().includes('READY')) { clearTimeout(timer); resolve(); } });
      locker.once('error', reject);
    });
    result = run(outside, ['/S', '--updated', `_?=${installed}`]);
    check('busy update fails and restores moved files', result.status !== null && result.status !== 0 && hash(path.join(installed, 'resources/app.asar')) === oldHash && fs.readFileSync(moved, 'utf8') === 'preserved' && fs.existsSync(longFile), result.status);
  } finally {
    const exit = new Promise(resolve => locker.once('exit', resolve));
    locker.stdin.end();
    await Promise.race([exit, new Promise(resolve => setTimeout(resolve, 3000))]);
    if (locker.exitCode === null) locker.kill();
  }
  fs.unlinkSync(marker); fs.unlinkSync(moved);
  result = run(path.join(output, '0.10.1/Astrion-NSIS-Probe-0.10.1.exe'), ['/S', `/D=${installed}`]);
  check('cross-volume temp update installs replacement', result.status === 0 && fs.readFileSync(path.join(installed, 'nested/deeper/version.txt'), 'utf8') === '0.10.1' && !fs.existsSync(longFile), result.status);
  fs.copyFileSync(path.join(installed, uninstaller), outside);
  result = run(outside, ['/S', `_?=${installed}`]);
  check('ordinary uninstall removes application', result.status === 0 && !fs.existsSync(path.join(installed, 'astrion-nsis-probe.exe')), result.status);
} catch (error) { checks.push({ name: 'exception', ok: false, detail: error.stack }); }
const result = { success: checks.length > 0 && checks.every(item => item.ok), checks };
fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(result, null, 2));
console.log(JSON.stringify(result, null, 2));
process.exitCode = result.success ? 0 : 1;
