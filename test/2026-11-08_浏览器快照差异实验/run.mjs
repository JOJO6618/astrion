// 内置浏览器「动作后只返回快照差异」设计验证。
// 用法：node run.mjs [--no-real] [--out <目录>]
// 依赖仓库根 node_modules/playwright-core（1.61.1）与本机已安装的 Playwright Chromium。
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, '../..');
const { chromium } = await import(path.join(repo, 'node_modules/playwright-core/index.mjs'));
const args = process.argv.slice(2);
const outDir = args.includes('--out') ? path.resolve(args[args.indexOf('--out') + 1])
  : path.join(repo, '_experiments/browser-snapshot-diff-20261108', String(Date.now()));
fs.mkdirSync(path.join(outDir, 'samples'), { recursive: true });

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://x');
  if (url.pathname === '/api/delay') {
    setTimeout(() => { res.writeHead(200, { 'content-type': 'application/json' }); res.end('{"ok":true}'); }, Number(url.searchParams.get('ms') || 0));
    return;
  }
  const file = url.pathname === '/page2.html' ? 'page2.html' : 'app.html';
  res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
  res.end(fs.readFileSync(path.join(here, 'fixture', file)));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });

// 页面稳定判定：DOM 静默 quietMs 且（可选）无进行中请求；最长 capMs。
async function installWatch(page) {
  await page.evaluate(() => {
    window.__last = performance.now();
    new MutationObserver(() => { window.__last = performance.now(); })
      .observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
  });
}
function trackRequests(page) {
  const state = { inflight: 0 };
  page.on('request', () => state.inflight++);
  page.on('requestfinished', () => state.inflight--);
  page.on('requestfailed', () => state.inflight--);
  return state;
}
async function settle(page, net, { quietMs = 300, capMs = 3000, useNetwork = true } = {}) {
  const start = Date.now();
  await page.waitForTimeout(50);
  while (Date.now() - start < capMs) {
    let idle;
    try {
      const state = await page.evaluate(q => typeof window.__last !== 'number' ? 'missing' : performance.now() - window.__last >= q, quietMs);
      if (state === 'missing') { await installWatch(page); idle = false; } else idle = state;
    }
    catch { idle = false; await page.waitForLoadState('domcontentloaded').catch(() => {}); await installWatch(page).catch(() => {}); }
    if (idle && (!useNetwork || net.inflight <= 0)) return { ms: Date.now() - start, capped: false };
    await page.waitForTimeout(50);
  }
  return { ms: Date.now() - start, capped: true };
}

const lines = text => (text ? text.split('\n').filter(Boolean) : []);
function measure(full, inc) {
  return {
    full_chars: full.length, full_lines: lines(full).length,
    inc_chars: inc.length, inc_lines: lines(inc).length,
    unchanged_lines: lines(inc).filter(l => /\[unchanged\]\s*$/.test(l)).length,
    ratio: full.length ? +(inc.length / full.length).toFixed(3) : null
  };
}
// 商品 N → 该 listitem 的 ref（listitem 行下一行是「- text: 商品 N ...」）。
function productRefs(snapshot) {
  const map = {}, rows = lines(snapshot);
  rows.forEach((line, i) => {
    const ref = line.match(/- listitem \[ref=(e\d+)\]:/)?.[1];
    const name = rows[i + 1]?.match(/- text: (商品 \d+)/)?.[1];
    if (ref && name) map[name] = ref;
  });
  return map;
}

// 设计中的后处理：
// 1) 同一层连续 >=3 行 [unchanged] 折叠为一行；
// 2) 对比前后完整快照，列出被删除的最外层节点；
// 3) 去掉 [active] 后前后完整快照一致，则判定「页面无变化」。
function parseTree(snapshot) {
  const nodes = new Map(), stack = [];
  for (const line of lines(snapshot)) {
    const indent = line.match(/^ */)[0].length;
    while (stack.length && stack.at(-1).indent >= indent) stack.pop();
    const ref = line.match(/\[ref=(e\d+)\]/)?.[1];
    const parent = stack.findLast(row => row.ref)?.ref || null;
    if (ref) nodes.set(ref, { parent, text: line.trim() });
    stack.push({ indent, ref });
  }
  return nodes;
}
function postprocess(inc, beforeFull, afterFull) {
  const strip = s => s.replace(/ \[active\]/g, '');
  if (strip(beforeFull) === strip(afterFull)) return { text: '页面无变化', no_change: true };
  const out = [], rows = lines(inc);
  for (let i = 0; i < rows.length;) {
    const indent = rows[i].match(/^ */)[0];
    let j = i;
    while (j < rows.length && rows[j].startsWith(indent) && rows[j].slice(indent.length).startsWith('- ref=') && /\[unchanged\]$/.test(rows[j])) j++;
    if (j - i >= 3) { out.push(`${indent}- …（${j - i} 个未变化节点）`); i = j; }
    else { out.push(rows[i]); i++; }
  }
  const before = parseTree(beforeFull), after = parseTree(afterFull);
  const removed = [...before].filter(([ref, row]) => !after.has(ref) && !(row.parent && !after.has(row.parent)));
  if (removed.length) {
    out.push('已移除：');
    for (const [, row] of removed.slice(0, 30)) out.push(`  - ${row.text.replace(/^- /, '').replace(/:$/, '')}`);
    if (removed.length > 30) out.push(`  - …另有 ${removed.length - 30} 个`);
  }
  return { text: out.join('\n'), no_change: false, removed_top: removed.length };
}

const scenarios = [
  { id: 'noop', label: '点击无效按钮', act: p => p.click('#noop') },
  { id: 'counter', label: '计数按钮文字变化', act: p => p.click('#counter') },
  { id: 'menu_open', label: '打开下拉菜单', act: p => p.click('#menuBtn') },
  { id: 'menu_close', label: '关闭下拉菜单（节点删除）', prepare: p => p.click('#menuBtn'), act: p => p.click('#menuBtn') },
  { id: 'search_suggest', label: '搜索框逐字输入出联想', act: p => p.locator('#search').pressSequentially('配置', { delay: 40 }) },
  { id: 'modal', label: '打开模态框', act: p => p.click('#modalBtn') },
  { id: 'toast_delayed', label: '点击后 800ms 才出提示', act: p => p.click('#toastBtn') },
  { id: 'form_errors', label: '提交表单出校验错误', act: p => p.click('form button[type=submit]') },
  { id: 'load_more_fetch', label: '加载更多（真实请求 500ms 后追加 20 项）', act: p => p.click('#loadBtn') },
  { id: 'tab_switch', label: '切换标签页（面板内容整体替换）', act: p => p.click('#tabB') },
  { id: 'spa_route', label: 'SPA 路由（pushState + 主区替换）', act: p => p.click('#routeBtn') },
  { id: 'rerender_full', label: '整体重渲染商品列表（只改 1 个价格）', act: p => p.click('#rerenderBtn'), refs: /- listitem \[ref=(e\d+)\]:.*?商品 (\d+)/ },
  { id: 'rerender_keyed', label: '局部更新 1 个价格', act: p => p.click('#keyedBtn') },
  { id: 'big_list_item', label: '500 项列表中改第 250 项', act: p => p.click('#bigBtn') },
  { id: 'live_clock', label: '页面上有持续变化的时钟', act: p => p.click('#clockBtn') },
  { id: 'full_navigation', label: '真实整页跳转', act: p => p.click('nav[aria-label="主导航"] >> text=文档') }
];

const results = [];
for (const sc of scenarios) {
  const page = await context.newPage();
  const net = trackRequests(page);
  await page.goto(`${base}/app.html`);
  if (sc.prepare) await sc.prepare(page);
  await installWatch(page);
  const track = `t-${sc.id}`;
  const before = await page.ariaSnapshot({ mode: 'ai', _track: track });
  const urlBefore = page.url();
  const t0 = Date.now();
  await sc.act(page);
  const actMs = Date.now() - t0;
  const domOnly = await settle(page, net, { useNetwork: false, capMs: 3000 });
  const withNet = await settle(page, net, { useNetwork: true, capMs: 3000 });
  const inc = await page.ariaSnapshot({ mode: 'ai', _track: track });
  const full = await page.ariaSnapshot({ mode: 'ai' });
  // 稳定后再等 2.5s，检查是否漏掉迟到的变化。
  await page.ariaSnapshot({ mode: 'ai', _track: `${track}-late` });
  await page.waitForTimeout(2500);
  const late = await page.ariaSnapshot({ mode: 'ai', _track: `${track}-late` });
  const row = {
    id: sc.id, label: sc.label, url_changed: page.url() !== urlBefore, act_ms: actMs,
    settle_dom_only: domOnly, settle_with_network_after_dom: withNet,
    ...measure(full, inc), before_chars: before.length,
    inc_empty: inc.trim() === '', late_change_chars: late.trim().length
  };
  const a = productRefs(before), b = productRefs(full), keys = Object.keys(a);
  row.product_ref_stable = `${keys.filter(k => a[k] === b[k]).length}/${keys.length}`;
  const pp = postprocess(inc, before, full);
  Object.assign(row, { pp_chars: pp.text.length, pp_ratio: +(pp.text.length / full.length).toFixed(3), pp_no_change: pp.no_change, pp_removed_top: pp.removed_top || 0 });
  fs.writeFileSync(path.join(outDir, 'samples', `${sc.id}.processed.txt`), pp.text);
  fs.writeFileSync(path.join(outDir, 'samples', `${sc.id}.incremental.txt`), inc);
  fs.writeFileSync(path.join(outDir, 'samples', `${sc.id}.full.txt`), full);
  if (late.trim()) fs.writeFileSync(path.join(outDir, 'samples', `${sc.id}.late.txt`), late);
  results.push(row);
  await page.close();
}

// 失效 ref 行为：重渲染后用旧 ref 操作会怎样。
const stale = {};
{
  const page = await context.newPage();
  await page.goto(`${base}/app.html`);
  const snap = await page.ariaSnapshot({ mode: 'ai' });
  const oldRef = lines(snap).find(l => l.includes('商品 4'))?.match(/ref=(e\d+)/)?.[1];
  await page.click('#rerenderBtn');
  await page.ariaSnapshot({ mode: 'ai' });
  stale.old_ref = oldRef;
  try { await page.locator(`aria-ref=${oldRef}`).click({ timeout: 1500 }); stale.result = 'clicked'; }
  catch (error) { stale.result = 'error'; stale.message = String(error.message).split('\n')[0]; }
  // 文字变化的同一个按钮：ref 是否换新、旧 ref 还能不能用。
  const s1 = await page.ariaSnapshot({ mode: 'ai' });
  const counterOld = lines(s1).find(l => l.includes('已点击 0 次'))?.match(/ref=(e\d+)/)?.[1];
  await page.click('#counter');
  const s2 = await page.ariaSnapshot({ mode: 'ai' });
  stale.counter_old_ref = counterOld;
  stale.counter_new_ref = lines(s2).find(l => l.includes('已点击 1 次'))?.match(/ref=(e\d+)/)?.[1];
  try { await page.locator(`aria-ref=${counterOld}`).click({ timeout: 1500 }); stale.counter_old_ref_click = 'clicked'; }
  catch (error) { stale.counter_old_ref_click = String(error.message).split('\n')[0]; }
  await page.close();
}

const real = [];
if (!args.includes('--no-real')) {
  const sites = [
    { id: 'wikipedia_search', url: 'https://en.wikipedia.org/wiki/Main_Page', act: p => p.locator('input[name="search"]').first().pressSequentially('Electron', { delay: 60 }) },
    { id: 'hn_front', url: 'https://news.ycombinator.com/' },
    { id: 'github_trending', url: 'https://github.com/trending' },
    { id: 'mdn_array', url: 'https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Array' },
    { id: 'bilibili_search', url: 'https://www.bilibili.com/', act: p => p.locator('input.nav-search-input').first().pressSequentially('原神', { delay: 80 }) }
  ];
  for (const site of sites) {
    const page = await context.newPage();
    const net = trackRequests(page);
    const row = { id: site.id, url: site.url };
    try {
      await page.goto(site.url, { waitUntil: 'domcontentloaded', timeout: 25000 });
      await installWatch(page);
      row.load_settle = await settle(page, net, { capMs: 8000 });
      const full = await page.ariaSnapshot({ mode: 'ai', _track: 'r' });
      row.full_chars = full.length; row.full_lines = lines(full).length;
      fs.writeFileSync(path.join(outDir, 'samples', `real_${site.id}.full.txt`), full);
      if (site.act) {
        await site.act(page);
        row.act_settle = await settle(page, net, { capMs: 5000 });
        const inc = await page.ariaSnapshot({ mode: 'ai', _track: 'r' });
        const after = await page.ariaSnapshot({ mode: 'ai' });
        const pp = postprocess(inc, full, after);
        Object.assign(row, { act: { ...measure(after, inc), pp_chars: pp.text.length, pp_removed_top: pp.removed_top || 0 } });
        fs.writeFileSync(path.join(outDir, 'samples', `real_${site.id}.processed.txt`), pp.text);
        fs.writeFileSync(path.join(outDir, 'samples', `real_${site.id}.incremental.txt`), inc);
      } else {
        await page.waitForTimeout(2000);
        const idle = await page.ariaSnapshot({ mode: 'ai', _track: 'r' });
        row.idle_2s_change_chars = idle.trim().length;
        if (idle.trim()) fs.writeFileSync(path.join(outDir, 'samples', `real_${site.id}.idle.txt`), idle);
      }
    } catch (error) { row.error = String(error.message).split('\n')[0]; }
    real.push(row);
    await page.close();
  }
}

await browser.close();
server.close();
const report = { playwright_core: '1.61.1', ran_at: new Date().toISOString(), fixture: results, stale_ref: stale, real };
fs.writeFileSync(path.join(outDir, 'results.json'), JSON.stringify(report, null, 2));
console.log(JSON.stringify(report, null, 2));
console.log('输出目录:', outDir);
