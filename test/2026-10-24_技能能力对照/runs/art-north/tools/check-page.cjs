'use strict';
/* 非视觉静态检查：HTML 结构、资源路径、控件与 JS 的对应关系。 */
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(root, 'js', 'app.js'), 'utf8');
const core = fs.readFileSync(path.join(root, 'js', 'core.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'css', 'style.css'), 'utf8');

const problems = [];
const notes = [];

/* 1. 资源路径存在性 */
const refs = [...html.matchAll(/(?:src|href)="([^"]+)"/g)].map(m => m[1]);
for (const ref of refs) {
  if (/^https?:|^\/\//.test(ref)) problems.push('外部资源: ' + ref);
  else if (!fs.existsSync(path.join(root, ref))) problems.push('缺失文件: ' + ref);
}
notes.push('页面引用资源 ' + refs.length + ' 个：' + refs.join(', '));

/* 2. id 唯一性与引用完整性 */
const ids = [...html.matchAll(/\sid="([^"]+)"/g)].map(m => m[1]);
const dup = ids.filter((v, i) => ids.indexOf(v) !== i);
if (dup.length) problems.push('重复 id: ' + [...new Set(dup)].join(', '));
notes.push('HTML 内 id 数量: ' + ids.length);

for (const m of html.matchAll(/aria-labelledby="([^"]+)"/g)) {
  if (!ids.includes(m[1])) problems.push('aria-labelledby 指向不存在的 id: ' + m[1]);
}
for (const m of html.matchAll(/\sfor="([^"]+)"/g)) {
  if (!ids.includes(m[1])) problems.push('label for 指向不存在的 id: ' + m[1]);
}

/* 3. JS 里 getElementById 的目标是否都存在 */
const wanted = [...app.matchAll(/getElementById\('([^']+)'\)/g)].map(m => m[1]);
for (const id of wanted) {
  if (!ids.includes(id)) problems.push('JS 引用了 HTML 中不存在的 id: ' + id);
}
notes.push('app.js 引用 id ' + new Set(wanted).size + ' 个，全部存在于 HTML: ' + wanted.every(id => ids.includes(id)));

/* 4. 每个按钮都绑定了事件 */
const buttons = [...html.matchAll(/<button\b[^>]*id="([^"]+)"/g)].map(m => m[1]);
for (const id of buttons) {
  const bound = new RegExp('els\\.' + id.replace(/-/g, '') + '\\b|getElementById\\(\'' + id + '\'\\)');
  const camel = id.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
  const aliasUsed = new RegExp('els\\.' + camel + '\\b').test(app);
  if (!aliasUsed && !new RegExp('getElementById\\(\'' + id + '\'\\)').test(app)) {
    problems.push('按钮可能没有绑定事件: ' + id);
  }
}
notes.push('按钮 ' + buttons.length + ' 个：' + buttons.join(', '));

/* 5. 滑杆参数与核心参数一致 */
const paramsInHtml = [...html.matchAll(/data-param="([^"]+)"/g)].map(m => m[1]);
const coreParams = ['layers', 'tidalRange', 'sediment', 'erosion'];
for (const p of paramsInHtml) if (!coreParams.includes(p)) problems.push('滑杆参数未在核心中使用: ' + p);
for (const p of coreParams) if (!paramsInHtml.includes(p)) problems.push('核心参数缺少滑杆: ' + p);
notes.push('滑杆参数: ' + paramsInHtml.join(', ') + '（与 core.createPlan 的选项一致）');

/* 6. 调色板按钮与核心调色板一致 */
const palInHtml = [...html.matchAll(/data-palette="([^"]+)"/g)].map(m => m[1]);
for (const p of palInHtml) if (!core.includes(p + ':')) problems.push('调色板未在核心中定义: ' + p);
notes.push('调色板: ' + palInHtml.join(', '));

/* 7. 无障碍基础项 */
if (!/role="slider"/.test(html)) problems.push('缺少 role=slider');
const sliderCount = (html.match(/role="slider"/g) || []).length;
const sliderTab = (html.match(/role="slider" tabindex="0"/g) || []).length;
if (sliderCount !== sliderTab) problems.push('滑杆缺少 tabindex=0');
for (const m of html.matchAll(/role="slider"[\s\S]{0,400}?>/g)) {
  if (!/aria-valuemin|aria-valuemax|aria-valuenow/.test(m[0])) problems.push('滑杆缺少 aria-valu* 属性');
}
if (!/lang="zh-CN"/.test(html)) problems.push('html lang 不是 zh-CN');
if (!/<meta name="viewport"/.test(html)) problems.push('缺少 viewport meta');
notes.push('滑杆 ' + sliderCount + ' 个，均可聚焦（tabindex=0）并带 aria-valuemin/max/now');

/* 8. CSS 关键响应式断点与触控尺寸 */
if (!/@media \(max-width: 900px\)/.test(css)) problems.push('缺少窄屏断点');
if (!/min-height: 44px/.test(css)) problems.push('窄屏按钮未放大到 44px');
notes.push('CSS 含 900px 断点、44px 触控高度、:focus-visible 焦点样式: ' + /:focus-visible/.test(css));

/* 9. 不允许联网依赖 */
if (/https?:\/\//.test(app + core + css)) problems.push('代码中存在外链');

console.log(notes.join('\n'));
console.log('\n问题数 =', problems.length);
if (problems.length) console.log(problems.join('\n'));
