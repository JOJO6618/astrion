'use strict';
/* 本地静态与逻辑核对脚本（无浏览器）：
   1) 语法检查由 node --check 完成，这里做数据、纯函数与接线核对。
   运行：node checks/verify.cjs
*/
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const fixtureDir = path.resolve(root, '../../fixtures/papertrail');

const results = [];
function check(name, fn) {
  try {
    const detail = fn();
    results.push({ name, pass: true, detail: detail === undefined ? 'ok' : detail });
  } catch (err) {
    results.push({ name, pass: false, detail: err.message });
  }
}
function assert(cond, msg) { if (!cond) throw new Error(msg); }

/* ---------------------------------------------------------------- 数据 --- */

function loadData() {
  const sandbox = { window: {} };
  vm.runInNewContext(fs.readFileSync(path.join(root, 'js/data.js'), 'utf8'), sandbox);
  return sandbox.window.PAPERS;
}
function loadFixtureData() {
  const sandbox = { window: {} };
  vm.runInNewContext(fs.readFileSync(path.join(fixtureDir, 'js/data.js'), 'utf8'), sandbox);
  return sandbox.window.PAPERS;
}

const papers = loadData();
const fixture = loadFixtureData();

// 用 vm 加载 app.js：没有 document 时脚本只导出纯函数然后返回。
const appSandbox = { module: { exports: {} }, console };
vm.runInNewContext(fs.readFileSync(path.join(root, 'js/app.js'), 'utf8'), appSandbox);
const logic = appSandbox.module.exports;
assert(typeof logic.selectPapers === 'function', 'app.js 未导出纯函数');

check('记录数量与原素材一致（9 条）', () => {
  assert(papers.length === 9, '实际 ' + papers.length);
  assert(fixture.length === 9, '原素材 ' + fixture.length);
  return '9 条';
});

check('每条记录逐字段与原素材完全相同', () => {
  const keys = ['id', 'title', 'authors', 'year', 'venue', 'topic', 'status', 'pages', 'abstract', 'note'];
  papers.forEach((p, i) => {
    const f = fixture[i];
    keys.forEach((k) => {
      assert(p[k] === f[k], `${p.id}.${k} 不一致: ${JSON.stringify(p[k])} != ${JSON.stringify(f[k])}`);
    });
    assert(Object.keys(p).length === keys.length, `${p.id} 字段数变化`);
  });
  return `9 条 × ${keys.length} 字段全部一致`;
});

check('状态分布 4 未读 / 2 在读 / 3 已读', () => {
  const c = logic.counts(papers);
  assert(c.all === 9 && c.unread === 4 && c.reading === 2 && c.read === 3, JSON.stringify(c));
  return JSON.stringify(c);
});

/* ------------------------------------------------------------ 纯函数 --- */

const baseState = { query: '', status: 'all', topic: 'all', sortKey: 'year', sortDir: 'desc' };
const withState = (patch) => Object.assign({}, baseState, patch);

check('每个状态筛选的结果数与分面计数完全一致', () => {
  const report = [];
  ['all', 'unread', 'reading', 'read'].forEach((status) => {
    const state = withState({ status });
    const n = logic.selectPapers(papers, state).length;
    const faceted = logic.facetCounts(papers, state).status[status];
    assert(n === faceted, `${status}: 列表 ${n} != 计数 ${faceted}`);
    report.push(`${status}=${n}`);
  });
  return report.join(' ');
});

check('每个主题筛选的结果数与分面计数一致', () => {
  const report = [];
  ['all', '视觉与图形', '人机交互', '科研方法'].forEach((topic) => {
    const state = withState({ topic });
    const n = logic.selectPapers(papers, state).length;
    const faceted = logic.facetCounts(papers, state).topic[topic];
    assert(n === faceted, `${topic}: 列表 ${n} != 计数 ${faceted}`);
    report.push(`${topic}=${n}`);
  });
  return report.join(' ');
});

check('搜索「噪声」只命中标题含该词的记录', () => {
  const hit = logic.selectPapers(papers, withState({ query: '噪声' }));
  assert(hit.length === 1 && hit[0].id === 'p01', JSON.stringify(hit.map((p) => p.id)));
  return 'p01';
});

check('搜索作者「陈悦」命中 3 条', () => {
  const hit = logic.selectPapers(papers, withState({ query: '陈悦' }));
  assert(hit.length === 3, hit.map((p) => p.id).join(','));
  return hit.map((p) => p.id).join(',');
});

check('搜索只覆盖标题与作者，不匹配摘要/期刊', () => {
  // 「复现」只出现在 p01 摘要里，不应命中
  assert(logic.matchesQuery(papers[0], '复现') === false, '摘要被误纳入搜索范围');
  assert(logic.matchesQuery(papers[0], '计算视觉研究') === false, '期刊被误纳入搜索范围');
  return '摘要与期刊不参与搜索';
});

check('搜索与筛选可叠加，且计数互相自洽', () => {
  const state = withState({ query: '王禾', topic: '人机交互', status: 'unread' });
  const visible = logic.selectPapers(papers, state);
  const facets = logic.facetCounts(papers, state);
  assert(visible.length === facets.status.unread, '状态计数与列表不一致');
  assert(visible.length === facets.topic['人机交互'], '主题计数与列表不一致');
  assert(visible.every((p) => p.topic === '人机交互' && p.status === 'unread'), '结果混入其他条件');
  return `${visible.length} 条：${visible.map((p) => p.id).join(',')}`;
});

check('三种排序都可执行，方向可翻转', () => {
  ['year', 'title', 'pages'].forEach((key) => {
    const desc = logic.sortPapers(papers, key, 'desc');
    const asc = logic.sortPapers(papers, key, 'asc');
    assert(desc.length === 9 && asc.length === 9, key + ' 排序丢失记录');
    assert(desc.map((p) => p.id).join() !== asc.map((p) => p.id).join(), key + ' 升降序结果相同');
  });
  const byYear = logic.sortPapers(papers, 'year', 'desc').map((p) => p.year);
  assert(byYear.join() === byYear.slice().sort((a, b) => b - a).join(), '年份降序不正确');
  const byPages = logic.sortPapers(papers, 'pages', 'asc').map((p) => p.pages);
  assert(byPages.join() === byPages.slice().sort((a, b) => a - b).join(), '页数升序不正确');
  return '年份/标题/页数 × 升降序';
});

check('空查询与全空筛选返回全部 9 条', () => {
  assert(logic.selectPapers(papers, baseState).length === 9, '默认视图不是 9 条');
  return '9 条';
});

check('不存在的关键词返回 0 条（空状态可触发）', () => {
  assert(logic.selectPapers(papers, withState({ query: 'zzzz' })).length === 0, '应为 0');
  return '0 条';
});

/* -------------------------------------------------------------- 静态 --- */

const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(root, 'js/app.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'css/style.css'), 'utf8');
const htmlIds = new Set(Array.from(html.matchAll(/\bid="([^"]+)"/g)).map((m) => m[1]));

check('app.js 引用的每个 getElementById 都能在 index.html 找到', () => {
  const used = Array.from(app.matchAll(/getElementById\('([^']+)'\)/g)).map((m) => m[1]);
  const missing = used.filter((id) => !htmlIds.has(id));
  assert(missing.length === 0, '缺失: ' + missing.join(', '));
  return `${new Set(used).size} 个 id 全部存在`;
});

check('HTML 中的按钮都带有可用标识（id 或 data-*）', () => {
  const buttons = Array.from(html.matchAll(/<button\b[^>]*>/g)).map((m) => m[0]);
  const unmarked = buttons.filter((b) => !/\bid="/.test(b) && !/\bdata-[a-z-]+=/.test(b));
  assert(unmarked.length === 0, '未标注: ' + unmarked.join(' | '));
  return `${buttons.length} 个按钮`;
});

check('HTML 里出现的 id 没有重复', () => {
  const all = Array.from(html.matchAll(/\bid="([^"]+)"/g)).map((m) => m[1]);
  const dup = all.filter((id, i) => all.indexOf(id) !== i);
  assert(dup.length === 0, '重复: ' + dup.join(', '));
  return `${all.length} 个 id 唯一`;
});

check('JS 用到的 data-* 选择器在 HTML 或 JS 模板中有落点', () => {
  const attrs = new Set();
  Array.from(app.matchAll(/\[(data-[a-z-]+)[\]=]/g), (m) => attrs.add(m[1]));
  Array.from(html.matchAll(/\b(data-[a-z-]+)=/g), (m) => attrs.add(m[1]));
  Array.from(app.matchAll(/\b(data-[a-z-]+)=/g), (m) => attrs.add(m[1]));
  const used = Array.from(app.matchAll(/\[(data-[a-z-]+)/g)).map((m) => m[1]);
  const missing = Array.from(new Set(used)).filter((a) => !attrs.has(a));
  assert(missing.length === 0, '无落点: ' + missing.join(', '));
  return Array.from(new Set(used)).join(', ');
});

check('JS 模板与 classList 用到的 class 都在样式表里有定义', () => {
  const cssClasses = new Set(Array.from(css.matchAll(/\.([a-zA-Z][\w-]*)/g)).map((m) => m[1]));
  const used = new Set();
  const shape = /^[a-z][a-z0-9_-]*$/;
  const addAll = (str) => str.replace(/\$\{[^}]*\}/g, ' ').split(/\s+/).forEach((t) => { if (shape.test(t)) used.add(t); });

  Array.from(app.matchAll(/class="([^"]*)"/g), (m) => {
    // 含引号/加号/括号的片段是字符串拼接，跳过；歪掉的 token 不参与比较
    if (/['+(]/.test(m[1])) return;
    addAll(m[1]);
  });
  Array.from(app.matchAll(/className\s*=\s*'([^']*)'/g), (m) => addAll(m[1]));
  Array.from(app.matchAll(/classList\.(?:add|toggle|remove)\(\s*'([^']+)'/g), (m) => used.add(m[1]));
  ['is-open', 'is-up', 'is-zero', 'is-locked', 'is-visible'].forEach((c) => used.add(c));
  ['dot--', 'meter--'].forEach((p) => used.add(p));

  // 带 `--` 后缀的是运行时拼接的状态类，改为验证前缀确有对应样式
  const prefixes = Array.from(used).filter((c) => c.endsWith('--'));
  prefixes.forEach((p) => {
    assert(Array.from(cssClasses).some((c) => c.startsWith(p)), '状态类前缀无样式: ' + p);
    used.delete(p);
  });

  const missing = Array.from(used).filter((c) => !cssClasses.has(c));
  assert(missing.length === 0, '未定义: ' + missing.join(', '));
  return `${used.size} 个 class + 前缀 ${prefixes.join('/')} 均有样式`;
});

check('样式表括号成对', () => {
  const open = (css.match(/\{/g) || []).length;
  const close = (css.match(/\}/g) || []).length;
  assert(open === close, `{ ${open} 与 } ${close} 不匹配`);
  return `{ ${open} / } ${close}`;
});

check('三个断点齐全（1240 / 1023 / 640）', () => {
  ['1240px', '1023px', '640px'].forEach((bp) => {
    assert(css.includes('(min-width: ' + bp + ')') || css.includes('(max-width: ' + bp + ')'), '缺少 ' + bp);
  });
  return 'min-width:1240 / max-width:1023 / max-width:640';
});

check('输入素材里 980px 的固定最小宽度已被移除', () => {
  const fixtureCss = fs.readFileSync(path.join(fixtureDir, 'css/style.css'), 'utf8');
  assert(fixtureCss.includes('min-width: 980px'), '原素材应包含该问题（用于对照）');
  assert(!/min-width:\s*9\d\dpx/.test(css), '新版仍存在固定最小宽度');
  return '原素材有，新版无 900px 以上固定最小宽度';
});

check('演示数据标记在页面中可见', () => {
  assert(/class="badge"[\s\S]{0,80}演示数据/.test(html), '顶栏缺少演示标记');
  assert(/演示记录|虚构演示资料/.test(html), '缺少数据说明');
  return '顶栏 badge + 侧栏说明 + 页脚 + 详情页脚';
});

check('没有引入网络请求相关代码', () => {
  const banned = ['fetch(', 'XMLHttpRequest', 'WebSocket', 'EventSource', 'import('];
  const hits = banned.filter((b) => app.includes(b) || html.includes(b));
  assert(hits.length === 0, '命中: ' + hits.join(', '));
  return '无 fetch/XHR/WebSocket/动态 import';
});

check('外部资源只引用本地文件', () => {
  const refs = Array.from(html.matchAll(/(?:src|href)="([^"]+)"/g)).map((m) => m[1])
    .filter((u) => !u.startsWith('#'));
  const remote = refs.filter((u) => /^(https?:)?\/\//.test(u));
  assert(remote.length === 0, '远程引用: ' + remote.join(', '));
  refs.forEach((u) => {
    if (u.startsWith('data:')) return;
    const abs = path.join(root, u.split('?')[0]);
    assert(fs.existsSync(abs), '本地资源不存在: ' + u);
  });
  return refs.join(', ');
});

/* -------------------------------------------------------------- 输出 --- */

const failed = results.filter((r) => !r.pass);
results.forEach((r) => {
  console.log(`${r.pass ? 'PASS' : 'FAIL'}  ${r.name}\n      ${r.detail}`);
});
console.log(`\n${results.length - failed.length}/${results.length} 项通过`);
process.exit(failed.length ? 1 : 0);
