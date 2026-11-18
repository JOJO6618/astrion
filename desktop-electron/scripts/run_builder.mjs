import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const buildId = require('../build/build-id.cjs');
// electron-builder 不会展开 extraMetadata 中的环境变量，必须用 CLI 配置项写入真实编号；
// 否则包内 astrionBuildId 会落成字面量 ${env.ASTRION_BUILD_ID}（0.10.9 及以前的问题）。
execFileSync(
  process.execPath,
  [
    path.join(root, 'node_modules/electron-builder/cli.js'),
    `-c.extraMetadata.astrionBuildId=${buildId}`,
    ...process.argv.slice(2),
  ],
  {
    cwd: root,
    env: { ...process.env, ASTRION_BUILD_ID: buildId },
    stdio: 'inherit',
    windowsHide: true,
  }
);
