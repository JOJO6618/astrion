import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, writeFileSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';

const buildId = createRequire(import.meta.url)('../build/build-id.cjs');

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.dirname(desktop);
const options = new Set(process.argv.slice(2));
if (process.platform !== 'win32') throw new Error('Windows application builds require the Windows native toolchain.');
const output = path.join(root, 'output/windows-electron');
mkdirSync(output, { recursive: true });
const env = { ...process.env, ASTRION_BUILD_ID: buildId, PYTHONUTF8: '1', ELECTRON_MIRROR: 'https://npmmirror.com/mirrors/electron/' };
const node = process.execPath;
function run(command, args, cwd = desktop) {
  console.info(`[windows-build] ${path.basename(command)} ${args.join(' ')}`);
  execFileSync(command, args, { cwd, env, stdio: 'inherit', windowsHide: true });
}
if (!options.has('--skip-frontend')) {
  // npm.cmd requires cmd.exe; do not interpolate paths or user content into a shell string.
  run(process.env.ComSpec || 'C:\\Windows\\System32\\cmd.exe', ['/d', '/s', '/c', 'npm run build --silent'], root);
}
if (!options.has('--skip-native')) run(node, [path.join(desktop, 'scripts/build_quick_native.mjs')]);
const python = path.join(root, 'desktop/src-tauri/runtime/python/python.exe');
if (!existsSync(python)) throw new Error('Bundled Windows Python is missing.');
run(python, [path.join(root, 'desktop/scripts/prepare_backend.py')], root);
const builder = path.join(desktop, 'node_modules/electron-builder/cli.js');
run(node, [builder, '--config', 'electron-builder.win.cjs', '--win', '--x64', '--publish', 'never',
  ...(options.has('--dir') ? ['--dir'] : [])]);
const executable = path.join(output, 'build/win-unpacked/astrion-desktop.exe');
if (!existsSync(executable)) throw new Error('Windows executable was not produced.');
const release = JSON.parse(readFileSync(path.join(desktop, 'windows-release.json'), 'utf8'));
const receipt = {
  version: release.version,
  build_id: buildId,
  platform: process.platform,
  executable: path.relative(root, executable),
  executable_bytes: statSync(executable).size,
  executable_sha256: createHash('sha256').update(readFileSync(executable)).digest('hex'),
  built_at: new Date().toISOString(),
  runtime_verified: false
};
writeFileSync(path.join(output, 'build-receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
console.info('[windows-build] Build produced; run packaged smoke tests before claiming it is verified.');
