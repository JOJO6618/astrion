import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const installDir = path.join(root, 'output/windows-electron/installer-test/installed-app');
for (const architecture of ['System32', 'SysWOW64']) {
  const executable = path.join(process.env.SystemRoot, architecture, 'WindowsPowerShell/v1.0/powershell.exe');
  const result = spawnSync(executable, ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', path.join(root, 'desktop-electron/build/stop-installed-backends.ps1'), '-InstallDir', installDir, '-ExecutableName', 'astrion-electron-migration-test.exe'], { encoding: 'utf8', timeout: 15000, windowsHide: true });
  console.log(JSON.stringify({ architecture, status: result.status, error: result.error?.message, stdout: result.stdout, stderr: result.stderr }));
}
const uninstaller = fs.readdirSync(installDir).find(name => /^Uninstall .*\.exe$/i.test(name));
console.log('Own test uninstaller:', uninstaller);
if (uninstaller) {
  const result = spawnSync(path.join(installDir, uninstaller), ['/S'], { encoding: 'utf8', timeout: 20000, windowsHide: false });
  console.log(JSON.stringify({ uninstallerStatus: result.status, error: result.error?.message, stdout: result.stdout, stderr: result.stderr }));
}
