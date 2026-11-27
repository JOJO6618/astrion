import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { stageRustBackend } from '../../desktop/scripts/prepare_rust_backend.mjs';

const windows = process.platform === 'win32';
if (process.platform !== 'darwin' && !(windows && process.arch === 'x64')) {
  throw new Error('The Rust-only directory build requires native macOS or x64 Windows.');
}
if (process.argv.length !== 2) throw new Error('This command only produces a local, unsigned directory build.');

const executable = windows ? 'astrion-headless.exe' : 'astrion-headless';
const desktop = fileURLToPath(new URL('../', import.meta.url));
const repo = path.dirname(path.resolve(desktop));
const cache = path.join(desktop, '.cache/rust-experiment');
fs.mkdirSync(cache, { recursive: true });
const buildRoot = fs.mkdtempSync(path.join(cache, 'build-'));
const tmp = path.join(buildRoot, 'tmp');
fs.mkdirSync(tmp);
const env = { ...process.env, TMPDIR: tmp, ...(windows && { TEMP: tmp, TMP: tmp }) };
const suppliedBinary = env.ASTRION_RUST_BACKEND_BIN;
const suppliedStatic = env.ASTRION_RUST_STATIC_ROOT;
if (!suppliedBinary) {
  execFileSync('cargo', ['build', '--locked', '--release', '--manifest-path', 'backend-rust/Cargo.toml', '--target-dir', path.join(cache, 'cargo-target')], { cwd: repo, env, stdio: 'inherit' });
}
if (!suppliedStatic) {
  const options = { cwd: repo, env, stdio: 'inherit', windowsHide: true };
  if (windows) execFileSync(env.ComSpec || 'C:\\Windows\\System32\\cmd.exe', ['/d', '/s', '/c', 'npm run build --silent -- --emptyOutDir'], options);
  else execFileSync('npm', ['run', 'build', '--silent', '--', '--emptyOutDir'], options);
}
const runtime = path.join(buildRoot, 'runtime');
stageRustBackend({
  binary: suppliedBinary || path.join(cache, 'cargo-target/release', executable),
  staticRoot: suppliedStatic || path.join(repo, 'static'),
  destination: runtime,
  executable
});
const extraResources = [{ from: runtime, to: 'runtime/rust' }];
if (windows) {
  execFileSync(process.execPath, [path.join(desktop, 'scripts/build_quick_native.mjs')], { cwd: desktop, env, stdio: 'inherit', windowsHide: true });
  extraResources.push({ from: '.cache/astrion-windows-helper.exe', to: 'astrion-windows-helper.exe' });
  extraResources.push({ from: '../desktop/browser-migration-extension', to: 'browser-migration-extension' });
}

const require = createRequire(import.meta.url);
const buildId = require('../build/build-id.cjs');
const electronDist = path.join(path.dirname(require.resolve('electron/package.json')), 'dist');
fs.accessSync(path.join(electronDist, windows ? 'electron.exe' : 'Electron.app/Contents/MacOS/Electron'));
process.env.TMPDIR = tmp;
if (windows) Object.assign(process.env, { TEMP: tmp, TMP: tmp });
process.env.CSC_IDENTITY_AUTO_DISCOVERY = 'false';
for (const key of Object.keys(process.env).filter(key => /^(CSC_|APPLE_)/.test(key) && key !== 'CSC_IDENTITY_AUTO_DISCOVERY')) delete process.env[key];
const { Arch, Platform, build } = require('electron-builder');
const config = path.join(buildRoot, 'electron-builder.rust.json');
const output = path.join(buildRoot, 'output');
const productName = 'Astrion Rust Experiment';
// Passing a separate config file prevents electron-builder from merging the
// default release configuration and bringing the Python runtime into this bundle.
fs.writeFileSync(config, JSON.stringify({
  appId: 'com.astrion.desktop.rust-experiment',
  productName,
  electronDist,
  npmRebuild: false,
  forceCodeSigning: false,
  directories: { output, buildResources: 'build' },
  files: ['src/**', '!src/windows/*.py', 'package.json'],
  extraResources,
  extraMetadata: { astrionBuildId: buildId },
  mac: { identity: null, notarize: false, icon: '../desktop/src-tauri/icons/icon.icns' },
  win: { signAndEditExecutable: false, executableName: 'astrion-rust-experiment', icon: '../desktop/src-tauri/icons/icon.ico' }
}));
await build({
  projectDir: desktop,
  targets: (windows ? Platform.WINDOWS : Platform.MAC).createTarget('dir', Arch[process.arch]),
  publish: 'never',
  config
});
const app = windows ? path.join(output, 'win-unpacked') : path.join(output, process.arch === 'arm64' ? 'mac-arm64' : 'mac', `${productName}.app`);
const packagedResources = path.join(app, windows ? 'resources' : 'Contents/Resources');
const packagedRuntime = path.join(packagedResources, 'runtime');
if (fs.readdirSync(packagedRuntime).join(',') !== 'rust') throw new Error('Unexpected runtime in the Rust experiment bundle');
fs.accessSync(path.join(packagedRuntime, 'rust', executable));
if (windows) fs.accessSync(path.join(packagedResources, 'astrion-windows-helper.exe'));
console.info(`Local Rust experiment build: ${app}`);
