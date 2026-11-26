import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdir, mkdtemp, rm } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { PassThrough } from 'node:stream';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { rustGuardianLaunch, watchGuardedPid } from '../../desktop-electron/src/windows/rust-guardian.js';
import { waitFor } from './gateway_harness.mjs';

const here = fileURLToPath(new URL('.', import.meta.url));
const executable = process.platform === 'win32' ? 'astrion-headless.exe' : 'astrion-headless';
const binary = resolve(process.env.ASTRION_HEADLESS_BIN || join(here, '../../backend-rust/target/debug', executable));

test('Windows Rust guardian preserves the argument vector, signals over stdin, and announces the correct child PID', () => {
  const command = 'C:\\Astrion Test\\astrion-headless.exe';
  const guardian = 'C:\\Astrion Test\\astrion-windows-helper.exe';
  const launch = rustGuardianLaunch({ command, guardian, args: ['--workspace', 'C:\\项目'], env: {}, shellPid: 124 });
  assert.equal(launch.command, guardian);
  assert.deepEqual(launch.args, ['guard-backend', '124', '--', command, '--workspace', 'C:\\项目']);
  assert.deepEqual(launch.stdio, ['pipe', 'pipe', 'pipe']);
  assert.equal(launch.env.ASTRION_DESKTOP_STOP_STDIN, '1');
  assert.throws(() => rustGuardianLaunch({ command, guardian, args: [], shellPid: -1 }), /invalid_rust_backend_guardian/);
  const child = { stdout: new PassThrough(), stdin: new PassThrough() };
  watchGuardedPid(child);
  child.stdout.write('native helper\r\nASTRION_BACK');
  child.stdout.write('END_PID=4294967296\r\nASTRION_BACKEND_PID=');
  child.stdout.write('892\r\n');
  assert.equal(child.backendPid, 892);
  child.stdout.write('ASTRION_BACKEND_PID=893\r\n');
  assert.equal(child.backendPid, 892);
});

for (const [name, requestStop] of [['EOF', child => child.stdin.end()], ['byte', child => child.stdin.write('\n')]]) {
  test(`real backend gracefully handles the desktop stop pipe over ${name}`, { timeout: 10_000 }, async t => {
    const root = await mkdtemp(join(here, 'desktop_guardian-'));
    const workspace = join(root, 'workspace');
    await mkdir(workspace);
    const child = spawn(binary, ['--workspace', workspace, '--data-dir', join(root, 'host/data'), '--port', '0'], {
      cwd: workspace, env: {
        PATH: process.env.PATH, ...(process.platform === 'win32' && { SystemRoot: process.env.SystemRoot }),
        ASTRION_DESKTOP_STOP_STDIN: '1', ASTRION_IGNORE_DOTENV: '1', HOME: root
      }, stdio: ['pipe', 'ignore', 'pipe']
    });
    child.stdin.on('error', () => {});
    let output = '';
    child.stderr.on('data', chunk => { output += chunk; });
    const exited = once(child, 'close');
    t.after(async () => {
      child.stdin.end();
      await rm(root, { recursive: true, force: true });
    });
    const port = await waitFor('desktop stop-pipe fixture to listen', () => {
      if (child.exitCode !== null || child.signalCode !== null) throw new Error(output);
      return /listening on 127\.0\.0\.1:(\d+)/.exec(output)?.[1];
    });
    const health = await fetch(`http://127.0.0.1:${port}/api/host-mode-enabled`);
    assert.equal((await health.json()).pid, child.pid);
    requestStop(child);
    assert.deepEqual(await exited, [0, null]);
  });
}
