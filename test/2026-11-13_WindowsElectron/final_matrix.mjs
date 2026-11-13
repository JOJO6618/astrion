import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const desktop = path.join(root, 'desktop-electron');
const output = path.join(root, 'output/windows-electron');
const buildTemp = path.join(output, 'nsis-build-temp');
fs.mkdirSync(buildTemp, { recursive: true });
const builder = path.join(desktop, 'node_modules/electron-builder/cli.js');
function run(args, logName, { build = false, version } = {}) {
  const log = fs.openSync(path.join(output, logName), 'w');
  const env = { ...process.env, ...(build ? { TEMP: buildTemp, TMP: buildTemp } : {}), ...(version ? { ASTRION_INSTALL_TEST_VERSION: version } : {}) };
  console.log('START', logName);
  const result = spawnSync(process.execPath, args, { cwd: build ? desktop : root, env, windowsHide: true, stdio: ['ignore', log, log], timeout: 1200000 });
  fs.closeSync(log);
  if (result.status !== 0) throw new Error(`${logName}: exit=${result.status} ${result.error?.message || ''}`);
  console.log('PASS', logName);
}
// The v0.10.0 App was already repackaged with the silent updater before the
// compiler ran out of C: temporary disk space. Rebuild v0.10.1 from source too.
run([builder, '--config', '../test/2026-11-13_WindowsElectron/installer-test.cjs', '--win', '--x64', '--dir', '--publish', 'never'], 'installer-final-v2-dir.log', { build: true, version: '0.10.1' });
for (const version of ['0.10.0', '0.10.1']) {
  run([builder, '--config', '../test/2026-11-13_WindowsElectron/installer-test.cjs', '--prepackaged', `../output/windows-electron/installer-test/${version}/win-unpacked`, '--win', '--x64', '--publish', 'never'], `installer-final-${version}.log`, { build: true, version });
}
// Restore the caller's normal TEMP for the test: C: temp -> E: installation.
run(['test/2026-11-13_WindowsElectron/installed_update.mjs'], 'installed-update-final.log');
run([builder, '--config', 'electron-builder.win.cjs', '--win', '--x64', '--publish', 'never'], 'final-installer-zip.log', { build: true });
run(['test/2026-11-13_WindowsElectron/packaged_smoke.mjs'], 'packaged-smoke-final.log');
run(['test/2026-11-13_WindowsElectron/verify_package.mjs'], 'package-verify-final.log');
console.log('Final build and verification matrix passed');
