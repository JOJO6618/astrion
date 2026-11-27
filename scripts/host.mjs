// Source distribution launcher. Desktop and CLI supply their own scoped paths.
import fs from 'node:fs';
import { root, absolute, dataRoot, settingsEnvironment } from './host-config.mjs';
import path from 'node:path';
import { parseArgs } from 'node:util';
import { isIP } from 'node:net';
import { spawn } from 'node:child_process';
import { configure } from './setup-host.mjs';

async function main() {
  const { values } = parseArgs({
    options: {
      path: { type: 'string' },
      port: { type: 'string' },
      bind: { type: 'string' },
      backend: { type: 'string' },
      'thinking-mode': { type: 'boolean' },
      debug: { type: 'boolean' },
      help: { type: 'boolean', short: 'h' }
    }
  });
  if (values.help) {
    console.log(
      'Astrion Host Web: start [--path DIR] [--port PORT] [--bind IP] [--thinking-mode] [--debug]'
    );
    return;
  }
  if (values.backend && values.backend !== 'rust')
    throw Error('Astrion Host requires the Rust backend');
  if (
    !fs.existsSync(path.join(root, '.env')) &&
    !fs.existsSync(path.join(dataRoot(process.env), 'settings.json')) &&
    !process.env.DATA_DIR &&
    !process.env.ASTRION_DATA_ROOT &&
    !/^(1|true|on|yes)$/i.test(process.env.ASTRION_IGNORE_DOTENV || '')
  ) {
    await configure(root, false);
  }
  const env = settingsEnvironment({ ...process.env });
  env.ASTRION_DATA_ROOT = dataRoot(env);
  env.ASTRION_RESOURCE_ROOT = absolute(
    env.ASTRION_RUST_RESOURCE_ROOT || env.ASTRION_RESOURCE_ROOT || root
  );
  for (const key of ['DEPLOY_CONFIG_DIR', 'LOGS_DIR', 'HOST_WORKSPACES_FILE']) {
    if (env[key]) env[key] = absolute(env[key]);
  }
  if (values.debug) env.RUST_BACKTRACE ||= '1';
  if (values['thinking-mode']) env.ASTRION_HOST_THINKING_DEFAULT = '1';
  const data = absolute(env.DATA_DIR || path.join(env.ASTRION_DATA_ROOT, 'host/data'));
  const workspace = absolute(
    values.path ||
      env.DEFAULT_PROJECT_PATH ||
      (fs.existsSync(path.join(root, 'project')) ? 'project' : root)
  );
  const binaryName = process.platform === 'win32' ? 'astrion-headless.exe' : 'astrion-headless';
  const candidates = env.ASTRION_RUST_BACKEND_BIN
    ? [absolute(env.ASTRION_RUST_BACKEND_BIN)]
    : ['release', 'debug'].map((profile) =>
        path.join(absolute(env.CARGO_TARGET_DIR || 'backend-rust/target'), profile, binaryName)
      );
  const binary = candidates.find((file) => fs.existsSync(file));
  if (!binary)
    throw Error('请先运行 cargo build --locked --release --manifest-path backend-rust/Cargo.toml');
  const staticRoot = path.join(env.ASTRION_RESOURCE_ROOT, 'static');
  if (!fs.existsSync(path.join(staticRoot, 'dist/.vite/manifest.json')))
    throw Error('请先运行 npm run build');
  let bind = values.bind ?? env.WEB_SERVER_HOST ?? '127.0.0.1';
  if (bind === 'localhost') bind = '127.0.0.1';
  if (!isIP(bind)) throw Error('监听地址必须是 IPv4 或 IPv6 地址');
  const args = [
    '--web',
    '--workspace',
    workspace,
    '--data-dir',
    data,
    '--static-root',
    staticRoot,
    '--port',
    values.port ?? env.WEB_SERVER_PORT ?? '8091',
    '--bind',
    bind
  ];
  if (process.platform !== 'win32' && process.execve)
    process.execve(binary, [binary, ...args], env);
  const child = spawn(binary, args, { env, cwd: root, stdio: 'inherit' });
  for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => child.kill(signal));
  child.once('error', (error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
  child.once('exit', (code, signal) => {
    process.exitCode = code ?? (signal === 'SIGINT' ? 130 : 1);
  });
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
