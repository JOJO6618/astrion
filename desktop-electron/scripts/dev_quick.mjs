import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repo = path.resolve(desktop, '..');
function run(command, args, cwd, env = process.env) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { cwd, env, stdio: 'inherit' });
    child.on('error', reject);
    child.on('exit', (code, signal) => code === 0 ? resolve() : reject(new Error(`${command} exited: ${code ?? signal}`)));
  });
}
try {
  // Build the renderer directly from source. No DMG, sidecar packaging or installation.
  await run('npm', ['run', 'build', '--silent'], repo);
  await run(path.join(desktop, 'node_modules/.bin/electron'), ['.', '--quick-debug'], desktop,
    { ...process.env, ASTRION_QUICK_WORKSPACE: process.env.ASTRION_QUICK_WORKSPACE || repo });
} catch (error) { console.error(error.message); process.exitCode = 1; }
