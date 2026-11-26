// 正常 macOS Rust 目录包：产出可直接双击/命令行启动的 Astrion.app，包内只有 Rust 后端。
//
// 与以下两个既有入口的区别：
//   - npm run build / build:dir：签名配置沿用 electron-builder.yml，复用下方 Rust staging；
//   - build_rust_dir.mjs（npm run build:rust:dir）：隔离实验包，productName
//     "Astrion Rust Experiment"、appId com.astrion.desktop.rust-experiment，要求专用空数据根。
// 本脚本产物沿用正式标识（appId com.astrion.desktop、productName Astrion、版本取仓库版本），
// 但只放 runtime/rust，因此壳层会默认使用包内 Rust（见 src/rust-backend.js 的默认选择）。
// 本地产物不签名、不公证、不发布、不替换已安装应用。
//
// 用法（不接受任何参数）：
//   npm --prefix desktop-electron run build:rust:app
// 可选环境变量：
//   ASTRION_RUST_BACKEND_BIN   复用已构建的 release 二进制（跳过 cargo build）
//   ASTRION_RUST_STATIC_ROOT   复用已构建的前端（跳过根目录前端构建）
//   ASTRION_BUILD_ID           指定包内 build id（默认随机 UUID）
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { stageRustBackend } from '../../desktop/scripts/prepare_rust_backend.mjs';

if (process.platform !== 'darwin') {
  throw new Error('The normal macOS Rust directory build requires native macOS.');
}
const prepareOnly = process.argv[2] === '--prepare';
if (process.argv.length !== (prepareOnly ? 3 : 2))
  throw new Error('Usage: node scripts/build_rust_app.mjs [--prepare]');

const executable = 'astrion-headless';
const desktop = fileURLToPath(new URL('../', import.meta.url));
const repo = path.dirname(path.resolve(desktop));
const cache = path.join(desktop, '.cache/rust-app');
const buildRoot = path.join(cache, 'build');
const runtime = path.join(buildRoot, 'runtime');
const cargoTarget = process.env.ASTRION_RUST_CARGO_TARGET_DIR || path.join(cache, 'cargo-target');
const env = { ...process.env, TMPDIR: path.join(buildRoot, 'tmp') };
fs.rmSync(buildRoot, { recursive: true, force: true });
fs.mkdirSync(env.TMPDIR, { recursive: true });

const suppliedBinary = env.ASTRION_RUST_BACKEND_BIN;
const suppliedStatic = env.ASTRION_RUST_STATIC_ROOT;
if (!suppliedBinary) {
  execFileSync(
    'cargo',
    [
      'build', '--locked', '--release',
      '--manifest-path', 'backend-rust/Cargo.toml',
      '--target-dir', cargoTarget
    ],
    { cwd: repo, env, stdio: 'inherit' }
  );
}
if (!suppliedStatic) {
  execFileSync('npm', ['run', 'build', '--silent', '--', '--emptyOutDir'], {
    cwd: repo, env, stdio: 'inherit'
  });
}

stageRustBackend({
  binary: suppliedBinary || path.join(cargoTarget, 'release', executable),
  staticRoot: suppliedStatic || path.join(repo, 'static'),
  destination: runtime,
  executable
});
if (prepareOnly) process.exit(0);

const require = createRequire(import.meta.url);
const buildId = require('../build/build-id.cjs');
const electronDist = path.join(path.dirname(require.resolve('electron/package.json')), 'dist');
fs.accessSync(path.join(electronDist, 'Electron.app/Contents/MacOS/Electron'));
process.env.TMPDIR = env.TMPDIR;
process.env.CSC_IDENTITY_AUTO_DISCOVERY = 'false';
for (const key of Object.keys(process.env).filter(name => /^(CSC_|APPLE_)/.test(name) && name !== 'CSC_IDENTITY_AUTO_DISCOVERY')) {
  delete process.env[key];
}
const { Arch, Platform, build } = require('electron-builder');
const output = path.join(desktop, 'dist-rust');
fs.rmSync(output, { recursive: true, force: true });
const productName = 'Astrion';
const config = {
  electronDist,
  npmRebuild: false,
  forceCodeSigning: false,
  directories: { output },
  extraMetadata: { astrionBuildId: buildId },
  mac: { identity: null, notarize: false }
};
await build({
  projectDir: desktop,
  targets: Platform.MAC.createTarget('dir', Arch[process.arch]),
  publish: 'never',
  config
});

const app = path.join(output, process.arch === 'arm64' ? 'mac-arm64' : 'mac', `${productName}.app`);
const resources = path.join(app, 'Contents/Resources');
const runtimes = fs.readdirSync(path.join(resources, 'runtime')).sort();
if (runtimes.join(',') !== 'rust') throw new Error(`Unexpected runtime in the Rust bundle: ${runtimes.join(',')}`);
const binary = path.join(resources, 'runtime/rust', executable);
fs.accessSync(binary, fs.constants.X_OK);
for (const file of ['static/dist/.vite/manifest.json', 'static/dist/.vite/entry-styles.json', 'config/providers_catalog.json']) {
  fs.accessSync(path.join(resources, 'runtime/rust', file), fs.constants.R_OK);
}
if (fs.existsSync(path.join(resources, 'runtime/python'))) throw new Error('Python runtime must not be staged in the Rust bundle');
const sha256 = createHash('sha256').update(fs.readFileSync(binary)).digest('hex');
console.info(JSON.stringify({
  app,
  productName,
  buildId,
  runtime: runtimes,
  binary,
  binaryBytes: fs.statSync(binary).size,
  binarySha256: sha256,
  unsigned: true,
  note: 'Local directory build only; not published, not installed.'
}, null, 2));
