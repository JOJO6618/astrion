import { spawn, execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const base = path.join(root, 'output/windows-electron/installer-test');
const powershell = path.join(process.env.SystemRoot, 'System32/WindowsPowerShell/v1.0/powershell.exe');
const installer = spawn(path.join(base, '0.10.0/Astrion-Electron-Test-0.10.0.exe'), [...(process.argv.includes('--silent') ? ['/S'] : []), `/D=${path.join(base, 'installed-app')}`], { stdio: 'ignore', windowsHide: false });
const driver = spawn(powershell, ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', path.join(root, 'test/2026-11-13_WindowsElectron/installer-wizard.ps1'), '-AllowedRoot', path.join(base, '0.10.0')], { stdio: 'inherit', windowsHide: true });
try {
  for (const delay of [15000, 20000]) {
    await new Promise(resolve => setTimeout(resolve, delay));
    execFileSync(powershell, ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', path.join(root, 'test/2026-11-13_WindowsElectron/installer-diagnostic.ps1')], { stdio: 'inherit', windowsHide: true, timeout: 10000 });
  }
} finally {
  if (installer.exitCode === null) installer.kill();
  if (driver.exitCode === null) driver.kill();
}
