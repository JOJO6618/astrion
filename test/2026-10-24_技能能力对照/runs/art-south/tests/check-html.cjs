/* 静态资源、路径与界面连接自检（不使用浏览器）。 */
'use strict';
const fs = require('fs');
const path = require('path');

const rootDir = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(rootDir, 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(rootDir, 'css/style.css'), 'utf8');
const jsFiles = ['js/sketch.js', 'js/archive.js', 'js/painter.js'];
const sketch = fs.readFileSync(path.join(rootDir, 'js/sketch.js'), 'utf8');
const allJs = jsFiles.map((f) => fs.readFileSync(path.join(rootDir, f), 'utf8')).join('\n');

const result = {};

/* 1. 本地资源存在且为相对路径 */
const refs = [];
for (const m of html.matchAll(/(?:src|href)="([^"]+)"/g)) refs.push(m[1]);
result.assetRefs = refs.map((r) => ({ ref: r, exists: r.startsWith('http') ? 'external' : fs.existsSync(path.join(rootDir, r)) }));

const external = [];
for (const f of ['index.html', 'css/style.css', ...jsFiles]) {
  const text = fs.readFileSync(path.join(rootDir, f), 'utf8');
  const hits = text.match(/https?:\/\/[^\s"'<>)]+/g) || [];
  for (const h of hits) external.push(f + ' -> ' + h);
}
result.externalUrls = external;

/* 2. id 连接：JS 查询的 id 必须存在 */
const htmlIds = new Set([...html.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1]));
const jsIds = [...new Set([...sketch.matchAll(/\$\('([^']+)'\)/g)].map((m) => m[1]))];
result.ids = {
  htmlCount: htmlIds.size,
  missingInHtml: jsIds.filter((id) => !htmlIds.has(id)),
  unusedHtmlIds: [...htmlIds].filter((id) => !jsIds.includes(id))
};

/* 3. 按钮都有 id，且都在 sketch.js 中绑定监听 */
const buttons = [...html.matchAll(/<button\b[^>]*>/g)].map((m) => m[0]);
const buttonIds = buttons.map((b) => {
  const m = /\sid="([^"]+)"/.exec(b);
  return m ? m[1] : null;
});
const uiMap = {};
for (const m of sketch.matchAll(/ui\.(\w+)\s*=\s*\$\('([^']+)'\)/g)) uiMap[m[2]] = m[1];
const buttonBinding = buttonIds.map((id) => {
  if (!id) return { id: null, bound: false };
  const varName = uiMap[id];
  const bound = !!(varName && new RegExp('ui\\.' + varName + '\\.addEventListener').test(sketch));
  return { id, bound };
});
result.buttons = {
  count: buttons.length,
  allHaveId: buttonIds.every(Boolean),
  bindings: buttonBinding,
  allBound: buttonBinding.every((b) => b.bound)
};

/* 4. range 控件的 label 指向有效 id */
const ranges = [...html.matchAll(/<input\b[^>]*type="range"[^>]*>/g)].map((m) => m[0]);
const labels = [...html.matchAll(/<label\b[^>]*for="([^"]+)"/g)].map((m) => m[1]);
result.ranges = ranges.map((r) => {
  const id = /\sid="([^"]+)"/.exec(r);
  const hasListener = !!(id && new RegExp("ui\\.[\\w]+\\s*=\\s*\\$\\('" + id[1] + "'\\)").test(sketch));
  return { id: id ? id[1] : null, labelled: id ? labels.includes(id[1]) : false, boundInJs: hasListener };
});

/* 5. 结构检查 */
result.structure = {
  doctype: /^<!doctype html>/i.test(html.trim()),
  lang: /<html lang="zh-CN">/.test(html),
  viewport: /name="viewport"/.test(html),
  hasNoscript: /<noscript>/.test(html),
  canvasMountPresent: htmlIds.has('canvas-mount'),
  p5BeforeSketch: html.indexOf('js/p5.min.js') < html.indexOf('js/sketch.js')
};

/* 6. CSS 基本检查 */
result.css = {
  hasMobileQuery: /@media \(max-width: 560px\)/.test(css),
  hasTabletQuery: /@media \(max-width: 1020px\)/.test(css),
  customFocusVisible: /:focus-visible/.test(css),
  noViewportUnits: !/\b100vh\b|\b100dvh\b/.test(css)
};

/* 7. 确定性：生成器不得使用 Math.random / 时间 */
result.generatorPurity = {
  archiveUsesMathRandom: /Math\.random/.test(fs.readFileSync(path.join(rootDir, 'js/archive.js'), 'utf8')),
  archiveUsesDate: /Date\.now|new Date/.test(fs.readFileSync(path.join(rootDir, 'js/archive.js'), 'utf8')),
  painterUsesMathRandom: /Math\.random/.test(fs.readFileSync(path.join(rootDir, 'js/painter.js'), 'utf8')),
  sketchMathRandomCount: (sketch.match(/Math\.random/g) || []).length
};

console.log(JSON.stringify(result, null, 2));
const fail =
  result.assetRefs.some((a) => a.exists !== true) ||
  result.externalUrls.length > 0 ||
  result.ids.missingInHtml.length > 0 ||
  !result.buttons.allHaveId || !result.buttons.allBound ||
  result.ranges.some((r) => !r.labelled || !r.boundInJs) ||
  !result.structure.doctype || !result.structure.lang || !result.structure.viewport || !result.structure.canvasMountPresent ||
  result.generatorPurity.archiveUsesMathRandom || result.generatorPurity.archiveUsesDate || result.generatorPurity.painterUsesMathRandom;
process.exitCode = fail ? 1 : 0;
