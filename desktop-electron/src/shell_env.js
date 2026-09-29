import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const LOGIN_SHELL_PATH_TIMEOUT_MS = 10_000;
const LOGIN_SHELL_PATH_MAX_OUTPUT_BYTES = 256 * 1024;
const PATH_MARKER = '__ASTRION_LOGIN_PATH__';

function loginShellCommand(shellName) {
  const capture = `printf '\\n${PATH_MARKER}%s\\n' "$PATH"`;
  if (shellName === 'zsh' || shellName === 'bash') {
    return { args: ['-ilc', capture] };
  }
  if (shellName === 'fish') {
    return {
      args: [
        '--login',
        '--interactive',
        '--command',
        `printf '\\n${PATH_MARKER}%s\\n' (string join ':' $PATH)`
      ]
    };
  }
  return null;
}

/**
 * 只从用户登录 shell 读取 PATH，不把其他 shell 环境变量带入 Astrion。
 * @returns {Promise<{path: string | null, status: string}>}
 */
export function loadLoginShellPath() {
  if (process.platform !== 'darwin') {
    return Promise.resolve({ path: null, status: 'unsupported-platform' });
  }

  const requestedShell = process.env.SHELL || '/bin/zsh';
  const shell = fs.existsSync(requestedShell) ? requestedShell : '/bin/zsh';
  const command = loginShellCommand(path.basename(shell));
  if (!command) {
    return Promise.resolve({ path: null, status: 'unsupported-shell' });
  }

  return new Promise((resolve) => {
    let child;
    let output = Buffer.alloc(0);
    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(result);
    };
    const stopChild = () => {
      if (!child?.pid) return;
      try {
        process.kill(-child.pid, 'SIGTERM');
      } catch {
        child.kill('SIGTERM');
      }
    };
    const timer = setTimeout(() => {
      stopChild();
      finish({ path: null, status: 'timeout' });
    }, LOGIN_SHELL_PATH_TIMEOUT_MS);

    try {
      child = spawn(shell, command.args, {
        cwd: os.homedir(),
        env: process.env,
        stdio: ['ignore', 'pipe', 'ignore'],
        detached: true
      });
    } catch {
      finish({ path: null, status: 'spawn-error' });
      return;
    }

    child.once('error', () => finish({ path: null, status: 'spawn-error' }));
    child.stdout.on('data', (chunk) => {
      if (settled) return;
      output = Buffer.concat([output, chunk]);
      if (output.length > LOGIN_SHELL_PATH_MAX_OUTPUT_BYTES) {
        stopChild();
        finish({ path: null, status: 'output-limit' });
      }
    });
    child.once('close', (code) => {
      if (settled) return;
      if (code !== 0) {
        finish({ path: null, status: 'shell-error' });
        return;
      }

      const text = output.toString('utf8');
      const markerIndex = text.lastIndexOf(PATH_MARKER);
      if (markerIndex < 0) {
        finish({ path: null, status: 'path-missing' });
        return;
      }
      const pathValue = text
        .slice(markerIndex + PATH_MARKER.length)
        .split(/\r?\n/, 1)[0]
        .trim();
      finish(pathValue ? { path: pathValue, status: 'loaded' } : { path: null, status: 'path-empty' });
    });
  });
}
