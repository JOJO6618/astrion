import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const buildId = require('../build/build-id.cjs');
execFileSync(process.execPath, [path.join(root, 'node_modules/electron-builder/cli.js'), ...process.argv.slice(2)], {
  cwd: root,
  env: { ...process.env, ASTRION_BUILD_ID: buildId },
  stdio: 'inherit',
  windowsHide: true,
});
