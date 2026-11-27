import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const repoRoot = fileURLToPath(new URL('../../', import.meta.url));
const catalogSeeds = [
  'providers_catalog.json', 'modelsdev_snapshot.json', 'skill_hints.json',
  'host_sandbox_policy.json.example', 'command_blocking_recommended.json'
];
const staticPayload = [
  'index.html', 'login.html', 'terminal.html', 'chrome.html', 'browser-window.html', 'quick.html',
  'apple-touch-icon.png', 'astrion-avatar.svg', 'favicon-16x16.png', 'favicon-32x32.png', 'security.js',
  'admin_policy', 'icons', 'logo', 'easter-eggs', 'quick-capture', 'vendor', 'dist'
];

function verifyAssets(root) {
  const read = name => JSON.parse(fs.readFileSync(path.join(root, 'dist/.vite', name), 'utf8'));
  const manifest = read('manifest.json');
  const styles = read('entry-styles.json').assemblies;
  const files = [
    ...Object.values(manifest).flatMap(entry => [entry.file, ...(entry.css || []), ...(entry.assets || [])]),
    ...Object.values(styles)
  ];
  if (!Object.keys(manifest).length || !Object.keys(styles).length) throw new Error('The frontend build manifests are empty');
  for (const file of files) {
    if (typeof file !== 'string' || path.isAbsolute(file) || file.split(/[\\/]/).includes('..')) {
      throw new Error('Invalid frontend asset path in the Vite manifests');
    }
    if (!fs.statSync(path.join(root, 'dist', file)).isFile()) throw new Error(`Frontend asset is missing: ${file}`);
  }
}

function copyPublicTree(from, to) {
  fs.cpSync(from, to, { recursive: true, filter(source) {
    const name = path.basename(source);
    if (name === '__pycache__' || name.endsWith('.pyc') || (name.startsWith('.') && name !== '.vite')) return false;
    const stat = fs.lstatSync(source);
    if (!(stat.isFile() || stat.isDirectory())) throw new Error(`Unexpected entry in packaged resources: ${source}`);
    return true;
  } });
}

export function stageRustResources(destination) {
  fs.mkdirSync(destination);
  for (const name of ['workflows', 'agentskills', 'prompts']) {
    copyPublicTree(path.join(repoRoot, name), path.join(destination, name));
  }
  fs.mkdirSync(path.join(destination, 'config'));
  for (const name of catalogSeeds) {
    copyPublicTree(path.join(repoRoot, 'config', name), path.join(destination, 'config', name));
  }
  if (process.platform === 'win32' || process.platform === 'linux') {
    const scripts = path.join(destination, 'scripts');
    fs.mkdirSync(scripts);
    const setup = process.platform === 'win32' ? 'setup-wsl-sandbox.ps1' : 'setup-linux-sandbox.sh';
    copyPublicTree(path.join(repoRoot, 'scripts', setup), path.join(scripts, setup));
  }
  if (process.platform === 'linux') {
    const helper = 'astrion-linux-sandbox';
    copyPublicTree(path.join(repoRoot, 'backend-rust/target/release', helper), path.join(destination, helper));
    fs.chmodSync(path.join(destination, helper), 0o755);
  }
}

// Stage a sidecar and its product resources independently of the Python runtime.
export function stageRustBackend({ binary, staticRoot, destination, executable = 'astrion-headless' }) {
  fs.accessSync(binary, process.platform === 'win32' ? fs.constants.F_OK : fs.constants.X_OK);
  if (!fs.statSync(binary).isFile()) throw new Error(`Rust executable is missing: ${binary}`);
  verifyAssets(staticRoot);
  stageRustResources(destination);
  fs.copyFileSync(binary, path.join(destination, executable));
  fs.chmodSync(path.join(destination, executable), 0o755);
  const stagedStatic = path.join(destination, 'static');
  fs.mkdirSync(stagedStatic);
  for (const name of staticPayload) copyPublicTree(path.join(staticRoot, name), path.join(stagedStatic, name));
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  if (process.argv.length > 3) throw new Error('Usage: node desktop/scripts/prepare_rust_backend.mjs [RESOURCE_DIRECTORY]');
  const destination = process.argv[2] ? path.resolve(process.argv[2]) : path.join(repoRoot, 'desktop-electron/.cache/rust-experiment/resources');
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  stageRustResources(destination);
  console.log(destination);
}
