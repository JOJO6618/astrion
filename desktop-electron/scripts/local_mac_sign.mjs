import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { signAsync } = require('@electron/osx-sign');
const machO = new Set([0xfeedface, 0xfeedfacf, 0xcefaedfe, 0xcffaedfe,
  0xcafebabe, 0xcafebabf, 0xbebafeca, 0xbfbafeca]);

function signable(file) {
  if (fs.statSync(file).isDirectory()) return /\.(app|framework)$/.test(file);
  const magic = Buffer.alloc(4);
  const fd = fs.openSync(file, 'r');
  try {
    return fs.readSync(fd, magic) === magic.length && machO.has(magic.readUInt32BE());
  } finally {
    fs.closeSync(fd);
  }
}

// Use the Electron signing walk so binaries are handled before their parent
// bundles. Resources such as app.asar are sealed by the containing bundle.
export async function signLocalMacApp(app) {
  await signAsync({
    app, identity: '-', identityValidation: false, preAutoEntitlements: false,
    ignore: file => !signable(file),
    optionsForFile: () => ({ hardenedRuntime: false, timestamp: 'none' }),
  });
  execFileSync('/usr/bin/codesign', ['--verify', '--deep', '--strict', '--verbose=2', app], { stdio: 'inherit' });
}
