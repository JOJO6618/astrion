import { app } from 'electron';
import { execFile, execFileSync } from 'node:child_process';
import { existsSync, statSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

const execute = promisify(execFile);
const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

export function resolveListenerBinary() {
  if (app.isPackaged) return path.join(process.resourcesPath, 'quick-entry-listener');
  const binary = path.join(desktop, '.cache/quick-entry-listener');
  const source = path.join(desktop, 'native/quick-entry.swift');
  if (!existsSync(binary) || statSync(source).mtimeMs > statSync(binary).mtimeMs) {
    mkdirSync(path.dirname(binary), { recursive: true });
    execFileSync('/usr/bin/swiftc', [source, '-o', binary], { stdio: 'pipe' });
  }
  return binary;
}

export async function inputPermission(request = false) {
  const { stdout } = await execute(resolveListenerBinary(), ['permission', request ? 'request' : 'check'], {
    encoding: 'utf8', maxBuffer: 8192, timeout: request ? 60000 : 4000
  });
  return JSON.parse(stdout).granted === true ? 'granted' : 'denied';
}
