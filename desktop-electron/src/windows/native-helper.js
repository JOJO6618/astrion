import { app } from 'electron';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
export function windowsHelper() {
  const binary = app.isPackaged
    ? path.join(process.resourcesPath, 'astrion-windows-helper.exe')
    : path.join(desktop, '.cache/astrion-windows-helper.exe');
  if (!existsSync(binary)) throw new Error('Windows 原生辅助程序缺失，请先运行 npm run build:quick-native。');
  return binary;
}
