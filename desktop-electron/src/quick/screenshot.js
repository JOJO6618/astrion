import { app } from 'electron';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdir, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const execute = promisify(execFile);
const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

export async function prepareNativeCapture() {
  const binary = app.isPackaged ? path.join(process.resourcesPath, 'quick-entry-capture') : path.join(desktop, '.cache/quick-entry-capture');
  if (!app.isPackaged) {
    const source = path.join(desktop, 'native/quick-capture.swift');
    const compiled = await stat(binary).catch(() => null);
    if (!compiled || (await stat(source)).mtimeMs > compiled.mtimeMs) {
      await mkdir(path.dirname(binary), { recursive: true });
      await execute('/usr/bin/swiftc', ['-parse-as-library', source, '-o', binary]);
    }
  }
  return binary;
}

export async function captureRegion(binary, display, rect) {
  const { stdout } = await execute(binary, [display.id, process.pid, rect.x, rect.y, rect.width, rect.height].map(String),
    { encoding: 'buffer', maxBuffer: 50 * 1024 * 1024, timeout: 15000 });
  return `data:image/png;base64,${stdout.toString('base64')}`;
}

export async function listCaptureWindows(binary) {
  const { stdout } = await execute(binary, ['windows', String(process.pid)],
    { encoding: 'utf8', maxBuffer: 4 * 1024 * 1024, timeout: 4000 });
  const windows = JSON.parse(stdout);
  if (!Array.isArray(windows)) throw new Error('Invalid native window list');
  return windows;
}

export async function capturePermission(binary, request = false) {
  const { stdout } = await execute(binary, ['permission', request ? 'request' : 'check'],
    { encoding: 'utf8', maxBuffer: 8192, timeout: request ? 60000 : 4000 });
  return JSON.parse(stdout).granted === true ? 'granted' : 'denied';
}

export async function captureWindow(binary, windowID) {
  const { stdout } = await execute(binary, ['window', String(windowID), String(process.pid)],
    { encoding: 'buffer', maxBuffer: 50 * 1024 * 1024, timeout: 15000 });
  return `data:image/png;base64,${stdout.toString('base64')}`;
}
