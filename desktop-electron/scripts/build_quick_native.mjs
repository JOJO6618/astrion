import { execFileSync } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
if (process.platform === 'darwin') {
  mkdirSync(path.join(desktop, '.cache'), { recursive: true });
  execFileSync('/usr/bin/swiftc', [path.join(desktop, 'native/quick-entry.swift'), '-o', path.join(desktop, '.cache/quick-entry-listener')], { stdio: 'inherit' });
  execFileSync('/usr/bin/swiftc', ['-parse-as-library', path.join(desktop, 'native/quick-capture.swift'), '-o', path.join(desktop, '.cache/quick-entry-capture')], { stdio: 'inherit' });
}
