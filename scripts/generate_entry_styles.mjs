// 构建后脚本：按 Vite manifest 的“静态 imports 闭包”递归装配手写 HTML 入口
// 依赖的共享 CSS。
//
// 背景：Vite 多入口构建会把多个入口共享的 SFC 样式抽到共享 chunk 里（例如
// StatusAvatar 的样式被抽到 assets/katex.css），入口自身的 CSS 通过 JS 静态
// import 关联，但手写 HTML 只 <link> 了入口 CSS，共享 chunk CSS 不会被自动加载，
// 于是出现“共享组件样式晚到、图标先叠出再消退”。
//
// 本脚本读取 static/dist/.vite/manifest.json，对每个配置的入口按 imports 递归
// 收集静态 CSS（共享 chunk 在前、入口 CSS 在后），再写出确定的 @import 装配文件。
// 不把 CSS 合并成一包，保持多入口隔离；不硬编码 katex 等会随打包变化的 chunk 名。
//
// 由 vite.config.ts 的 writeBundle 调用，覆盖正式构建和每轮监听构建。
// 构建产物缺失/路径非法时以非零退出码终止，使构建显式失败。
//
// 手动调用：node scripts/generate_entry_styles.mjs
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const distDir = path.join(repoRoot, 'static', 'dist');
const manifestPath = path.join(distDir, '.vite', 'manifest.json');

// 需要由本脚本生成“共享 CSS 装配”的入口：
//   输出文件（相对仓库根） -> Vite manifest 入口 key
// 此处维护主页面的既有样式入口；快捷窗口已有独立 manifest 装配。
// 新增使用此方式加载样式的页面时，在这里登记其入口。
const ENTRIES = [{ output: 'static/style.css', manifestKey: 'static/src/main.ts' }];

function fail(message) {
  console.error(`[generate_entry_styles] ${message}`);
  process.exit(1);
}

// 与 desktop-electron/src/quick/entry-styles.js 一致的递归收集：先访问 imports，
// 再追加本 chunk 的 CSS，得到“共享先、入口后”的顺序；去重但保持顺序。
function collectStyles(manifest, entryKey) {
  const visited = new Set();
  const styles = [];
  const visit = (key) => {
    if (visited.has(key)) return;
    visited.add(key);
    const chunk = manifest[key];
    if (!chunk) throw new Error(`Missing build manifest chunk: ${key}`);
    for (const imported of chunk.imports || []) visit(imported);
    for (const css of chunk.css || []) {
      if (typeof css !== 'string' || !/^[\w./-]+\.css$/.test(css) || css.includes('..')) {
        throw new Error(`Invalid stylesheet path in chunk ${key}: ${css}`);
      }
      if (!styles.includes(css)) styles.push(css);
    }
  };
  visit(entryKey);
  return styles;
}

// 用所有被引用 CSS 的内容哈希作为缓存令牌：内容变化才变，保持装配文件确定。
function contentToken(styles) {
  const hash = createHash('sha256');
  for (const css of styles) {
    const abs = path.join(distDir, css);
    if (!fs.existsSync(abs)) throw new Error(`Missing built stylesheet: ${css}`);
    hash.update(css);
    hash.update(fs.readFileSync(abs));
  }
  return hash.digest('hex').slice(0, 10);
}

function renderStylesheet(styles, token) {
  const lines = [
    '/* 由 scripts/generate_entry_styles.mjs 依据 Vite manifest 自动生成，请勿手改。',
    '   正式构建与监听构建均由 Vite writeBundle 自动更新。',
    '   顺序为共享 chunk CSS 在前、入口 CSS 在后，保证入口局部样式最后生效。 */'
  ];
  for (const css of styles) {
    lines.push(`@import url('/static/dist/${css}?v=${token}');`);
  }
  return `${lines.join('\n')}\n`;
}

function main() {
  if (!fs.existsSync(manifestPath)) {
    fail(`未找到构建 manifest：${path.relative(repoRoot, manifestPath)}。请先执行 vite build。`);
  }
  let manifest;
  try {
    manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  } catch (error) {
    fail(`无法解析构建 manifest：${error.message}`);
    return;
  }

  for (const entry of ENTRIES) {
    let styles;
    try {
      styles = collectStyles(manifest, entry.manifestKey);
    } catch (error) {
      fail(`入口 ${entry.manifestKey} 的 CSS 闭包收集失败：${error.message}`);
      return;
    }
    if (styles.length === 0) {
      fail(`入口 ${entry.manifestKey} 未收集到任何 CSS，疑似 manifest 结构异常。`);
      return;
    }
    let token;
    try {
      token = contentToken(styles);
    } catch (error) {
      fail(`计算缓存令牌失败：${error.message}`);
      return;
    }
    const target = path.join(repoRoot, entry.output);
    const next = renderStylesheet(styles, token);
    let previous = '';
    if (fs.existsSync(target)) previous = fs.readFileSync(target, 'utf8');
    if (previous === next) {
      console.log(`[generate_entry_styles] ${entry.output} 无需更新（${styles.length} 个 CSS）。`);
      continue;
    }
    fs.writeFileSync(target, next);
    console.log(`[generate_entry_styles] 已写出 ${entry.output} -> ${styles.join(', ')}`);
  }
}

main();
