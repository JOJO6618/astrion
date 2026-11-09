// 用项目已有的 esbuild 把 design-library 的 TS 源码打成临时 ESM，
// 全部产物落在本测试目录 .build/ 内，不写 design-library 的任何文件。
import { build } from 'esbuild';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const library = path.resolve(here, '../../design-library');
const outdir = path.join(here, '.build');

const targets = [
  { entry: 'room/reviewRules.ts', outfile: 'reviewRules.mjs' },
  { entry: 'tooling/review-server.ts', outfile: 'review-server.mjs' },
  { entry: 'room/useReviews.ts', outfile: 'useReviews.mjs' }
];

export async function buildAll() {
  fs.rmSync(outdir, { recursive: true, force: true });
  fs.mkdirSync(outdir, { recursive: true });
  for (const target of targets) {
    const entry = path.join(library, target.entry);
    if (!fs.existsSync(entry)) throw new Error(`缺少源码：${entry}`);
    await build({
      entryPoints: [entry],
      outfile: path.join(outdir, target.outfile),
      bundle: true,
      format: 'esm',
      platform: 'node',
      target: 'node20',
      // 只打相对路径的相对导入；vue / vite / node 内置全部外置，不安装依赖。
      packages: 'external',
      sourcemap: 'inline',
      logLevel: 'warning'
    });
  }
  return outdir;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const dir = await buildAll();
  process.stdout.write(`built -> ${dir}\n`);
}
