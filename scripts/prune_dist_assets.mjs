// 构建后脚本：按 Vite manifest 的引用闭包清理 static/dist/assets 里的历史产物。
//
// 背景（2026-11-22）：产物名带内容哈希（见 vite.config.ts），而 emptyOutDir 为 false，
// 于是每次构建都只新增文件、从不删除旧文件。实测累积到 107 MB / 1171 个文件，
// 其中当前 manifest 只引用 152 个（7.4 MB），其余 92.9% 是历史残留；
// desktop/scripts/prepare_backend.py 又整目录 copytree，导致安装包里多塞近百 MB。
//
// 本脚本只做“按引用裁剪”，不参与产物生成：
//   1. 读 static/dist/.vite/manifest.json，递归收集所有可达文件
//      （file / css / assets / imports / dynamicImports）；
//   2. 删除 static/dist/assets 下不在集合内的文件（不改动 .vite 与 dist 根散文件）；
//   3. manifest 缺失或引用集合异常小时跳过并告警，避免误删。
//
// 由 scripts/generate_entry_styles.mjs 在每次 writeBundle 后调用，
// 因此正式构建和 `vite build --watch` 都能保持干净。
//
// 手动调用：
//   node scripts/prune_dist_assets.mjs            # 实际清理
//   node scripts/prune_dist_assets.mjs --dry-run  # 只看会删多少
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const distDir = path.join(repoRoot, 'static', 'dist');
const assetsDir = path.join(distDir, 'assets');
const manifestPath = path.join(distDir, '.vite', 'manifest.json');

// 引用集合低于该值时视为 manifest 异常，宁可跳过清理也不误删产物。
const MIN_EXPECTED_REFERENCES = 10;

// 从 manifest 递归收集所有可达产物（相对 distDir 的路径）。
export function collectReferencedFiles(manifest) {
  const referenced = new Set();
  const keys = Object.keys(manifest);
  if (keys.length === 0) return referenced;

  // 从所有入口开始遍历；manifest 里每个 key 都是源码模块，
  // 递归 imports / dynamicImports 可覆盖全部 chunk。
  const stack = keys.slice();
  const visited = new Set();

  while (stack.length > 0) {
    const key = stack.pop();
    if (visited.has(key)) continue;
    visited.add(key);

    const item = manifest[key];
    if (!item || typeof item !== 'object') continue;

    if (typeof item.file === 'string' && item.file) referenced.add(item.file);
    for (const css of item.css || []) referenced.add(css);
    for (const asset of item.assets || []) referenced.add(asset);

    for (const next of [...(item.imports || []), ...(item.dynamicImports || [])]) {
      if (manifest[next]) stack.push(next);
    }
  }

  return referenced;
}

function collectExistingFiles(dir, prefix = '') {
  const out = [];
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return out;
  }
  for (const entry of entries) {
    const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
    const abs = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...collectExistingFiles(abs, rel));
    } else if (entry.isFile()) {
      out.push({ rel, abs });
    }
  }
  return out;
}

function formatMB(bytes) {
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

// 清理 dist/assets 中未被 manifest 引用的文件。
// 返回 { skipped, reason?, removed, freedBytes, kept }。
export function pruneStaleAssets({ dryRun = false, quiet = false } = {}) {
  const log = (...args) => {
    if (!quiet) console.log('[prune_dist_assets]', ...args);
  };

  if (!fs.existsSync(manifestPath)) {
    log('未找到 manifest，跳过清理（下次构建会重新生成）。');
    return { skipped: true, reason: 'no-manifest', removed: 0, freedBytes: 0, kept: 0 };
  }

  let manifest;
  try {
    manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  } catch (error) {
    log(`manifest 解析失败，跳过清理：${error.message}`);
    return { skipped: true, reason: 'bad-manifest', removed: 0, freedBytes: 0, kept: 0 };
  }

  const referenced = collectReferencedFiles(manifest);
  if (referenced.size < MIN_EXPECTED_REFERENCES) {
    log(`引用集合只有 ${referenced.size} 个文件，疑似异常，跳过清理。`);
    return { skipped: true, reason: 'too-few-references', removed: 0, freedBytes: 0, kept: 0 };
  }

  if (!fs.existsSync(assetsDir)) {
    log('assets 目录不存在，跳过清理。');
    return { skipped: true, reason: 'no-assets', removed: 0, freedBytes: 0, kept: 0 };
  }

  const files = collectExistingFiles(assetsDir, 'assets');
  const stale = [];
  let kept = 0;
  let freedBytes = 0;

  for (const file of files) {
    if (referenced.has(file.rel)) {
      kept += 1;
      continue;
    }
    let size = 0;
    try {
      size = fs.statSync(file.abs).size;
    } catch {
      size = 0;
    }
    stale.push(file);
    freedBytes += size;
  }

  if (stale.length === 0) {
    log(`无需清理（保留 ${kept} 个文件）。`);
    return { skipped: false, removed: 0, freedBytes: 0, kept };
  }

  if (dryRun) {
    log(`[dry-run] 将删除 ${stale.length} 个陈旧文件，释放 ${formatMB(freedBytes)}（保留 ${kept} 个）。`);
    return { skipped: false, dryRun: true, removed: stale.length, freedBytes, kept };
  }

  let removed = 0;
  for (const file of stale) {
    // 双保险：只删除 assets 目录内的文件。
    const guard = path.relative(assetsDir, file.abs);
    if (guard.startsWith('..') || path.isAbsolute(guard)) continue;
    try {
      fs.rmSync(file.abs);
      removed += 1;
    } catch (error) {
      log(`删除失败 ${file.rel}：${error.message}`);
    }
  }

  log(`已删除 ${removed} 个陈旧文件，释放 ${formatMB(freedBytes)}（保留 ${kept} 个）。`);
  return { skipped: false, removed, freedBytes, kept };
}

// 直接运行时作为 CLI 使用。
const invokedDirectly = process.argv[1]
  && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (invokedDirectly) {
  pruneStaleAssets({ dryRun: process.argv.includes('--dry-run') });
}
