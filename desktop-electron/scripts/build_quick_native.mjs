import { execFileSync } from 'node:child_process';
import { mkdirSync, existsSync, copyFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
if (process.platform === 'darwin') {
  mkdirSync(path.join(desktop, '.cache'), { recursive: true });
  execFileSync('/usr/bin/swiftc', [path.join(desktop, 'native/quick-entry.swift'), '-o', path.join(desktop, '.cache/quick-entry-listener')], { stdio: 'inherit' });
  execFileSync('/usr/bin/swiftc', ['-parse-as-library', path.join(desktop, 'native/quick-capture.swift'), path.join(desktop, 'native/window-discovery-policy.swift'), '-o', path.join(desktop, '.cache/quick-entry-capture')], { stdio: 'inherit' });
} else if (process.platform === 'win32') {
  const installedCargo = path.join(process.env.USERPROFILE || '', '.cargo/bin/cargo.exe');
  const cargo = existsSync(installedCargo) ? installedCargo : 'cargo';
  const target = path.join(desktop, '.cache/windows-native-target');
  execFileSync(cargo, ['build', '--locked', '--release', '--manifest-path', path.join(desktop, 'native/windows-helper/Cargo.toml'), '--target-dir', target], { stdio: 'inherit', windowsHide: true });
  copyFileSync(path.join(target, 'release/astrion-windows-helper.exe'), path.join(desktop, '.cache/astrion-windows-helper.exe'));
} else {
  throw new Error(`Desktop native build is unsupported on ${process.platform}; Windows artifacts must be built on Windows.`);
}
