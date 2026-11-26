import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repo = fileURLToPath(new URL('../../', import.meta.url));

function explicitDirectory(env, key) {
  const value = env[key];
  if (!value || !path.isAbsolute(value)) throw new Error(`${key} must be an absolute directory path`);
  const directory = fs.realpathSync(value);
  if (!fs.statSync(directory).isDirectory()) throw new Error(`${key} must point to a directory`);
  return directory;
}

function dedicated(directory, kind) {
  const marker = path.join(directory, `.astrion-rust-${kind}`);
  if (fs.existsSync(marker)) {
    if (!fs.lstatSync(marker).isFile()) throw new Error(`Invalid experiment marker: ${marker}`);
    return;
  }
  if (fs.readdirSync(directory).length) throw new Error(`Use a dedicated, initially empty ${kind} directory: ${directory}`);
  fs.writeFileSync(marker, '', { flag: 'wx', mode: 0o600 });
}

function inside(root, target) {
  const relative = path.relative(root, target);
  return relative === '' || (!path.isAbsolute(relative) && relative !== '..' && !relative.startsWith(`..${path.sep}`));
}

function experimentPaths(env) {
  const dataRoot = explicitDirectory(env, 'ASTRION_DESKTOP_DATA_ROOT');
  const profile = explicitDirectory(env, 'ASTRION_ELECTRON_USER_DATA');
  const workspace = explicitDirectory(env, 'ASTRION_DESKTOP_INITIAL_WORKSPACE');
  const production = ['.astrion/astrion', '.astrion/astrion-desktop', 'Library/Application Support/Astrion', 'Library/Application Support/Electron'];
  if (production.some(relative => [dataRoot, profile].some(directory => inside(path.join(os.homedir(), relative), directory)))) {
    throw new Error('Choose experiment directories outside the production Astrion and Electron profiles');
  }
  if (inside(dataRoot, profile) || inside(profile, dataRoot) || inside(dataRoot, workspace) || inside(profile, workspace)) {
    throw new Error('Use separate directories for the experiment data, Electron profile, and workspace');
  }
  dedicated(dataRoot, 'data');
  dedicated(profile, 'profile');
  const dataDir = path.join(dataRoot, 'host/data');
  const deployDir = path.join(dataRoot, 'config');
  const logsDir = path.join(dataRoot, 'host/logs');
  for (const directory of [path.join(dataRoot, 'host'), dataDir, deployDir, logsDir]) {
    if (fs.existsSync(directory) && !fs.lstatSync(directory).isDirectory()) throw new Error(`Invalid experiment directory: ${directory}`);
  }
  return { dataDir, dataRoot, deployDir, logsDir, profile, workspace, registry: path.join(deployDir, 'host_workspaces.json') };
}

// Select the backend before Electron acquires its profile lock. Only the
// experiment creates dedicated directory markers.
export function resolveDesktopBackend({ env = process.env, platform = process.platform, resourcesPath } = {}) {
  const embedded = resourcesPath && path.join(resourcesPath, 'runtime/rust');
  const stagedRoot = embedded && fs.existsSync(embedded) ? embedded : null;
  const pythonPayload = resourcesPath && path.join(resourcesPath, 'runtime/backend');
  const hasPythonRuntime = resourcesPath && fs.existsSync(path.join(resourcesPath, 'runtime/python'));
  // 包内只有 Rust 运行时（没有内嵌 Python）时默认就用包内 Rust，免去手工指定后端；
  // 开发形态没有 resourcesPath，仍按原默认 Python。显式 ASTRION_DESKTOP_BACKEND 始终优先。
  const bundledRustOnly = Boolean(stagedRoot) && !hasPythonRuntime;
  const mode = env.ASTRION_DESKTOP_BACKEND || (bundledRustOnly ? 'rust' : 'python');
  if (mode === 'python') {
    if (stagedRoot && !hasPythonRuntime) {
      throw new Error('Set ASTRION_DESKTOP_BACKEND=rust to launch this Rust directory bundle');
    }
    return null;
  }
  if (mode !== 'rust') throw new Error('ASTRION_DESKTOP_BACKEND must be python or rust');
  if (!['darwin', 'win32'].includes(platform)) throw new Error(`Rust Electron startup is unavailable on ${platform}`);
  const experimental = env.ASTRION_RUST_EXPERIMENT === '1';
  const executable = platform === 'win32' ? 'astrion-headless.exe' : 'astrion-headless';
  const binary = env.ASTRION_RUST_BACKEND_BIN || path.join(stagedRoot || path.join(repo, 'backend-rust/target/debug'), executable);
  const staticRoot = env.ASTRION_RUST_STATIC_ROOT || path.join(stagedRoot || pythonPayload || repo, 'static');
  const resources = env.ASTRION_RUST_RESOURCE_ROOT || stagedRoot || pythonPayload || path.join(repo, 'desktop-electron/.cache/rust-experiment/resources');
  if (![binary, staticRoot, resources].every(path.isAbsolute)) throw new Error('Rust binary, static root, and resource root must be absolute');
  fs.accessSync(binary, platform === 'win32' ? fs.constants.F_OK : fs.constants.X_OK);
  fs.accessSync(path.join(staticRoot, 'dist/.vite/manifest.json'), fs.constants.R_OK);
  fs.accessSync(path.join(staticRoot, 'dist/.vite/entry-styles.json'), fs.constants.R_OK);
  const resourceRoot = fs.realpathSync(resources);
  fs.accessSync(path.join(resourceRoot, 'config/providers_catalog.json'), fs.constants.R_OK);
  if (Boolean(env.ASTRION_RUST_MODEL_ENDPOINT) !== Boolean(env.ASTRION_RUST_MODEL)) {
    throw new Error('Set ASTRION_RUST_MODEL_ENDPOINT and ASTRION_RUST_MODEL together');
  }
  if (!experimental) return { binary, staticRoot, resourceRoot, experimental };
  const locations = experimentPaths(env);
  if (inside(locations.dataRoot, resourceRoot) || inside(locations.profile, resourceRoot) || fs.existsSync(path.join(resourceRoot, 'config/custom_models.json'))) {
    throw new Error('Use staged Rust product resources, separate from experiment data');
  }
  return { binary, staticRoot, resourceRoot, experimental, ...locations };
}

function registeredWorkspace(registry) {
  let catalog;
  try { catalog = JSON.parse(fs.readFileSync(registry, 'utf8')); }
  catch (error) {
    if (error.code === 'ENOENT') return null;
    throw error;
  }
  if (!Array.isArray(catalog.workspaces)) return null;
  const valid = catalog.workspaces.filter(entry => typeof entry?.path === 'string' && fs.statSync(entry.path, { throwIfNoEntry: false })?.isDirectory());
  return valid.find(entry => entry.workspace_id === catalog.default_workspace_id)?.path || valid[0]?.path || null;
}

function configuredPath(value, fallback) {
  const raw = value?.trim();
  if (!raw) return fallback;
  if (raw === '~') return os.homedir();
  if (/^~[\\/]/.test(raw)) return path.resolve(os.homedir(), raw.slice(2));
  return path.resolve(repo, raw);
}

export function productionRustPaths(dataRoot, env = process.env) {
  const dataDir = configuredPath(env.DATA_DIR, path.join(dataRoot, 'host/data'));
  const deployDir = configuredPath(env.DEPLOY_CONFIG_DIR, path.join(dataRoot, 'config'));
  const logsDir = configuredPath(env.LOGS_DIR, path.join(dataRoot, 'host/logs'));
  const registry = configuredPath(env.HOST_WORKSPACES_FILE, path.join(deployDir, 'host_workspaces.json'));
  const workspace = configuredPath(env.ASTRION_DESKTOP_INITIAL_WORKSPACE, null) || registeredWorkspace(registry) || os.homedir();
  return { dataDir, dataRoot, deployDir, logsDir, registry, workspace };
}

export function rustDesktopLaunch(runtime, { port, version, identity, env = process.env }) {
  const args = ['--web', '--static-root', runtime.staticRoot, '--workspace', runtime.workspace,
    '--data-dir', runtime.dataDir, '--port', String(port)];
  if (env.ASTRION_RUST_MODEL_ENDPOINT) args.push('--endpoint', env.ASTRION_RUST_MODEL_ENDPOINT, '--model', env.ASTRION_RUST_MODEL);
  const childEnv = { ...env };
  if (runtime.experimental) {
    for (const key of Object.keys(childEnv)) if (/^(ASTRION_|AGENT_|WEB_)/i.test(key)) delete childEnv[key];
    Object.assign(childEnv, {
      HOME: runtime.dataRoot, USERPROFILE: runtime.dataRoot, APPDATA: runtime.dataRoot, LOCALAPPDATA: runtime.dataRoot,
      XDG_CONFIG_HOME: runtime.deployDir, XDG_DATA_HOME: runtime.dataRoot, XDG_CACHE_HOME: path.join(runtime.dataRoot, 'cache')
    });
  }
  delete childEnv.ASTRION_DESKTOP_STOP_STDIN;
  delete childEnv.ASTRION_DESKTOP_BRIDGE_PORT;
  if (runtime.bridgePort) childEnv.ASTRION_DESKTOP_BRIDGE_PORT = String(runtime.bridgePort);
  if (runtime.shellPath) {
    const entries = [runtime.shellPath, childEnv.PATH || ''].join(path.delimiter).split(path.delimiter).filter(Boolean);
    childEnv.PATH = [...new Set(entries)].join(path.delimiter);
  }
  Object.assign(childEnv, {
    ASTRION_DESKTOP_VERSION: version,
    ASTRION_DESKTOP_BUILD_ID: identity.build_id,
    ASTRION_DESKTOP_PLATFORM: identity.platform,
    ASTRION_DESKTOP_ARCH: identity.arch,
    ASTRION_DESKTOP_POLICY_PROTOCOL: '1',
    ASTRION_DATA_ROOT: runtime.dataRoot,
    ASTRION_RESOURCE_ROOT: runtime.resourceRoot,
    ASTRION_IGNORE_DOTENV: '1',
    DEPLOY_CONFIG_DIR: runtime.deployDir,
    LOGS_DIR: runtime.logsDir,
    HOST_WORKSPACES_FILE: runtime.registry
  });
  if (env.ASTRION_MODEL_API_KEY) childEnv.ASTRION_MODEL_API_KEY = env.ASTRION_MODEL_API_KEY;
  return { command: runtime.binary, args, cwd: runtime.workspace, env: childEnv };
}

export const RUST_PROBE_PATH = '/api/host-mode-enabled';

export async function isRustBackendReady(port, pid) {
  if (!pid) return false;
  try {
    const response = await fetch(`http://127.0.0.1:${port}${RUST_PROBE_PATH}`, {
      redirect: 'error', signal: AbortSignal.timeout(800)
    });
    const payload = response.ok ? await response.json() : null;
    return payload?.backend === 'rust' && payload.pid === pid;
  } catch {
    return false;
  }
}
