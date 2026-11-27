import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { stageRustBackend } from '../../desktop/scripts/prepare_rust_backend.mjs';

const buildId = createRequire(import.meta.url)('../build/build-id.cjs');

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.dirname(desktop);
const options = new Set(process.argv.slice(2));
if (process.platform !== 'win32' || process.arch !== 'x64') throw new Error('Windows application builds require native x64 Windows.');
for (const option of options) {
  if (!['--dir', '--skip-frontend', '--skip-native'].includes(option)) throw new Error(`Unknown build option: ${option}`);
}
const output = path.join(root, 'output/windows-electron');
mkdirSync(output, { recursive: true });
const receiptPath = path.join(output, 'build-receipt.json');
rmSync(receiptPath, { force: true });
const env = { ...process.env, ASTRION_BUILD_ID: buildId, ELECTRON_MIRROR: 'https://npmmirror.com/mirrors/electron/' };
const node = process.execPath;
function run(command, args, cwd = desktop) {
  console.info(`[windows-build] ${path.basename(command)} ${args.join(' ')}`);
  execFileSync(command, args, { cwd, env, stdio: 'inherit', windowsHide: true });
}
if (!options.has('--skip-frontend') && !env.ASTRION_RUST_STATIC_ROOT) {
  // npm.cmd requires cmd.exe; do not interpolate paths or user content into a shell string.
  run(process.env.ComSpec || 'C:\\Windows\\System32\\cmd.exe', ['/d', '/s', '/c', 'npm run build --silent'], root);
}
if (!options.has('--skip-native')) run(node, [path.join(desktop, 'scripts/build_quick_native.mjs')]);

const target = 'x86_64-pc-windows-msvc';
const cargoTarget = path.resolve(root, env.ASTRION_RUST_CARGO_TARGET_DIR || 'desktop-electron/.cache/windows-rust-target');
if (!env.ASTRION_RUST_BACKEND_BIN) {
  const installedCargo = path.join(env.USERPROFILE || '', '.cargo/bin/cargo.exe');
  run(existsSync(installedCargo) ? installedCargo : 'cargo', [
    'build', '--locked', '--release', '--manifest-path', 'backend-rust/Cargo.toml',
    '--target', target, '--target-dir', cargoTarget
  ], root);
}
const runtime = path.join(desktop, '.cache/windows-rust-backend');
rmSync(runtime, { recursive: true, force: true });
mkdirSync(path.dirname(runtime), { recursive: true });
stageRustBackend({
  binary: env.ASTRION_RUST_BACKEND_BIN || path.join(cargoTarget, target, 'release/astrion-headless.exe'),
  staticRoot: env.ASTRION_RUST_STATIC_ROOT || path.join(root, 'static'),
  destination: runtime,
  executable: 'astrion-headless.exe'
});
const builder = path.join(desktop, 'node_modules/electron-builder/cli.js');
run(node, [builder, '--config', 'electron-builder.win.cjs', '--win', '--x64', '--publish', 'never',
  ...(options.has('--dir') ? ['--dir'] : [])]);
const executable = path.join(output, 'build/win-unpacked/astrion-desktop.exe');
const resources = path.join(path.dirname(executable), 'resources');
const backend = path.join(resources, 'runtime/rust/astrion-headless.exe');
const helper = path.join(resources, 'astrion-windows-helper.exe');
for (const file of [executable, backend, path.join(resources, 'runtime/rust/scripts/setup-wsl-sandbox.ps1'),
  path.join(resources, 'runtime/rust/static/dist/.vite/manifest.json'), helper,
  path.join(resources, 'browser-migration-extension/manifest.json')]) {
  if (!statSync(file).isFile()) throw new Error(`Required Windows package file is missing: ${file}`);
}
for (const relative of ['runtime/backend', 'runtime/python', 'chrome-import']) {
  if (existsSync(path.join(resources, relative))) throw new Error(`Unexpected legacy resource in the Windows package: ${relative}`);
}
const chromeProbe = JSON.parse(execFileSync(helper, ['chrome-import'], {
  input: JSON.stringify({ action: 'preview-file', value: { format: 'password-csv', content: 'url,username,password\r\n' } }),
  encoding: 'utf8', windowsHide: true, timeout: 10000
}));
if (chromeProbe.ok !== true || chromeProbe.value?.stats.passwords.read !== 0) {
  throw new Error('The Windows package is missing a working Chrome import helper.');
}
const release = JSON.parse(readFileSync(path.join(desktop, 'windows-release.json'), 'utf8'));
const receipt = {
  version: release.version,
  build_id: buildId,
  platform: process.platform,
  executable: path.relative(root, executable),
  executable_bytes: statSync(executable).size,
  executable_sha256: createHash('sha256').update(readFileSync(executable)).digest('hex'),
  backend: 'rust',
  backend_executable: path.relative(root, backend),
  backend_bytes: statSync(backend).size,
  backend_sha256: createHash('sha256').update(readFileSync(backend)).digest('hex'),
  built_at: new Date().toISOString(),
  runtime_verified: false
};
writeFileSync(receiptPath, JSON.stringify(receipt, null, 2) + '\n');
console.info('[windows-build] Build produced; run packaged smoke tests before claiming it is verified.');
