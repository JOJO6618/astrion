import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const output = path.join(root, `output/windows-electron/import-${Date.now()}`);
fs.mkdirSync(output, { recursive: true });
const python = path.join(root, 'desktop/src-tauri/runtime/python/python.exe');
const fixture = spawnSync(python, ['-I', '-B', path.join(root, 'test/2026-11-13_WindowsElectron导入/create_fixture.py'), output], { encoding: 'utf8', windowsHide: true, timeout: 30000 });
if (fixture.status !== 0) throw new Error(`Synthetic fixture failed: ${fixture.stderr}`);
const env = { ...process.env, ASTRION_IMPORT_TEST_ROOT: output };
if (process.argv.includes('--packaged')) env.ASTRION_IMPORT_RESOURCES = path.join(root, 'output/windows-electron/build/win-unpacked/resources');
delete env.ELECTRON_RUN_AS_NODE;
const electron = path.join(root, 'desktop-electron/node_modules/electron/dist/electron.exe');
for (const phase of ['import', 'restart']) {
  const log = fs.openSync(path.join(output, `${phase}.log`), 'w');
  const run = spawnSync(electron, [path.join(root, 'test/2026-11-13_WindowsElectron导入/native.cjs'), ...(phase === 'restart' ? ['--verify'] : [])], { env, windowsHide: true, stdio: ['ignore', log, log], timeout: 180000 });
  fs.closeSync(log);
  if (run.status !== 0) {
    console.log(JSON.stringify({ success: false, output, phase, status: run.status, error: run.error?.message }));
    process.exitCode = 1;
    break;
  }
}
const files = ['import-results.json', 'restart-results.json'];
const results = files.filter(name => fs.existsSync(path.join(output, name))).map(name => JSON.parse(fs.readFileSync(path.join(output, name), 'utf8')));
const result = { success: results.length === 2 && results.every(row => row.success), output, phases: results.map(({ phase, success, checks, error }) => ({ phase, success, checks: checks.length, ...(error ? { error } : {}) })), checks: results.flatMap(row => row.checks), real_user_data_read: false };
fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(result, null, 2));
fs.writeFileSync(path.join(root, 'output/windows-electron/import-latest.json'), JSON.stringify(result, null, 2));
console.log(JSON.stringify({ ...result, checks: result.checks.length }, null, 2));
process.exitCode = result.success ? 0 : 1;
