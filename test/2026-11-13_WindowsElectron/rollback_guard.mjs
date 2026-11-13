import fs from 'node:fs';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
const hash = file => createHash('sha256').update(fs.readFileSync(file)).digest('hex');
export async function verifyBusyRollback(root, installDir) {
  const output = path.join(root, 'output/windows-electron/installer-test');
  if (installDir !== path.join(output, 'installed-app')) throw new Error('Unexpected rollback test directory');
  const marker = path.join(installDir, 'AA-rollback-guard.txt');
  const moved = path.join(installDir, '00-rollback-before-guard.txt');
  fs.writeFileSync(marker, 'synthetic locked file'); fs.writeFileSync(moved, 'preserve on rollback');
  const executable = path.join(installDir, 'astrion-electron-migration-test.exe');
  const archive = path.join(installDir, 'resources/app.asar');
  const before = { executable: hash(executable), archive: hash(archive) };
  const uninstaller = fs.readdirSync(installDir).find(name => /^Uninstall .*\.exe$/i.test(name));
  const copy = path.join(output, 'rollback-guard-uninstaller.exe');
  fs.copyFileSync(path.join(installDir, uninstaller), copy);
  const locker = spawn(path.join(root, 'desktop/src-tauri/runtime/python/python.exe'), ['-u', '-B', path.join(root, 'test/2026-11-13_WindowsElectron/rollback_locker.py'), marker], { stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
  try {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Rollback locker startup timeout')), 10000);
      locker.stdout.on('data', chunk => { if (chunk.toString().includes('READY')) { clearTimeout(timer); resolve(); } });
      locker.once('error', reject);
      locker.once('exit', code => { clearTimeout(timer); reject(new Error(`Rollback locker exited early: ${code}`)); });
    });
    const result = spawnSync(copy, ['/S', '--updated', `_?=${installDir}`], { timeout: 60000, windowsHide: false, stdio: 'ignore' });
    const restored = result.status !== null && result.status !== 0 && hash(executable) === before.executable && hash(archive) === before.archive
      && fs.readFileSync(moved, 'utf8') === 'preserve on rollback' && fs.existsSync(marker);
    const evidence = { success: restored, uninstallExit: result.status, error: result.error?.message, executableRestored: fs.existsSync(executable), archiveRestored: fs.existsSync(archive) };
    fs.writeFileSync(path.join(output, 'rollback-guard-results.json'), JSON.stringify(evidence, null, 2));
    if (!restored) throw new Error('Locked-file rollback did not preserve the old package');
    return evidence;
  } finally {
    if (locker.exitCode === null) {
      const closed = new Promise(resolve => locker.once('exit', resolve));
      locker.stdin.end();
      await Promise.race([closed, new Promise(resolve => setTimeout(resolve, 3000))]);
      if (locker.exitCode === null) locker.kill();
    }
    for (const file of [marker, moved, copy]) { try { fs.unlinkSync(file); } catch {} }
  }
}
