// 真实网站上的真实交互：验证快照差异、页面稳定判定与后处理。
// 用法：node real.mjs [--only id1,id2] [--out <目录>]
import fs from 'node:fs';
import path from 'node:path';
import { repo, lines, installWatch, trackRequests, settle, measure, postprocess, stripActive } from './lib.mjs';

const { chromium } = await import(path.join(repo, 'node_modules/playwright-core/index.mjs'));
const args = process.argv.slice(2);
const only = args.includes('--only') ? new Set(args[args.indexOf('--only') + 1].split(',')) : null;
const outDir = args.includes('--out') ? path.resolve(args[args.indexOf('--out') + 1])
  : path.join(repo, '_experiments/browser-snapshot-diff-20261108', `real-${Date.now()}`);
fs.mkdirSync(path.join(outDir, 'samples'), { recursive: true });

const role = (p, r, name) => p.getByRole(r, { name }).first();
const scenarios = [
  { id: 'wiki_search_type', url: 'https://en.wikipedia.org/wiki/Main_Page', label: 'Wikipedia 搜索框逐字输入出联想',
    act: p => p.locator('input[name="search"]:visible').first().pressSequentially('Electron', { delay: 60 }) },
  { id: 'wiki_search_enter', url: 'https://en.wikipedia.org/wiki/Main_Page', label: 'Wikipedia 输入后回车进入词条（整页跳转）',
    prepare: p => p.locator('input[name="search"]:visible').first().fill('Electron'), act: p => p.keyboard.press('Enter') },
  { id: 'wiki_languages', url: 'https://en.wikipedia.org/wiki/Electron', label: 'Wikipedia 词条打开语言列表（上百项）',
    act: p => role(p, 'button', /languages/i).click() },
  { id: 'github_code_menu', url: 'https://github.com/microsoft/playwright', label: 'GitHub 仓库页打开 Code 下拉',
    act: p => role(p, 'button', /^Code$/).click() },
  { id: 'github_open_file', url: 'https://github.com/microsoft/playwright', label: 'GitHub 点文件列表进入文件（SPA）',
    act: p => p.locator('a[title="package.json"]:visible').first().click() },
  { id: 'github_issues_tab', url: 'https://github.com/microsoft/playwright', label: 'GitHub 切到 Issues 标签',
    act: p => p.getByRole('link', { name: /^Issues/ }).first().click() },
  { id: 'mdn_search', url: 'https://developer.mozilla.org/en-US/', label: 'MDN 首页搜索输入',
    act: p => p.locator('input[type=search]:visible, input[name=q]:visible').first().pressSequentially('flexbox', { delay: 60 }) },
  { id: 'react_docsearch', url: 'https://react.dev/', label: 'react.dev 打开搜索弹窗并输入',
    act: async p => { await role(p, 'button', /Search/).click(); await p.getByRole('searchbox', { name: 'Search' }).last().pressSequentially('useState', { delay: 60 }); } },
  { id: 'vue_api_toggle', url: 'https://vuejs.org/guide/essentials/reactivity-fundamentals.html', label: 'Vue 文档切换 Options/Composition API（全文内容切换）',
    act: p => p.getByRole('switch', { name: /Composition|Options|API/i }).first().click() },
  { id: 'npm_search', url: 'https://www.npmjs.com/', label: 'npm 搜索框输入出联想',
    act: p => p.locator('input[type=search]:visible, input[name=q]:visible').first().pressSequentially('playwright', { delay: 60 }) },
  { id: 'baidu_suggest', url: 'https://www.baidu.com/', label: '百度搜索框输入出联想',
    act: p => p.locator('#kw, textarea#chat-textarea').first().pressSequentially('内置浏览器', { delay: 80 }) },
  { id: 'bilibili_search', url: 'https://www.bilibili.com/', label: 'Bilibili 搜索框输入出联想',
    act: p => p.locator('input.nav-search-input').first().pressSequentially('原神', { delay: 80 }) },
  { id: 'hn_more', url: 'https://news.ycombinator.com/', label: 'HN 点 More 翻页（整页跳转）',
    act: p => p.getByRole('link', { name: 'More', exact: true }).click() },
  { id: 'hn_collapse', url: 'https://news.ycombinator.com/', label: 'HN 评论页折叠第一条评论',
    prepare: async p => { await p.getByRole('link', { name: /\d+\s*comments?/ }).first().click(); await p.waitForLoadState('domcontentloaded'); },
    act: p => p.locator('a.togg').first().click() }
];

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, locale: 'zh-CN' });
const results = [];
for (const sc of scenarios) {
  if (only && !only.has(sc.id)) continue;
  const page = await context.newPage();
  page.setDefaultTimeout(12000);
  const net = trackRequests(page);
  const row = { id: sc.id, label: sc.label, url: sc.url };
  try {
    await page.goto(sc.url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await installWatch(page);
    row.load_settle = await settle(page, net, { capMs: 8000 });
    if (sc.prepare) { await sc.prepare(page); await installWatch(page).catch(() => {}); row.prepare_settle = await settle(page, net, { capMs: 8000 }); }
    const before = await page.ariaSnapshot({ mode: 'ai', _track: 'm' });
    const urlBefore = page.url();
    await page.evaluate(() => { window.__sameDoc = true; });
    const t0 = Date.now();
    await sc.act(page);
    row.act_ms = Date.now() - t0;
    row.settle = await settle(page, net, { capMs: 5000 });
    let inc = await page.ariaSnapshot({ mode: 'ai', _track: 'm' });
    let after = await page.ariaSnapshot({ mode: 'ai' });
    row.url_changed = page.url() !== urlBefore;
    row.same_document = await page.evaluate(() => window.__sameDoc === true).catch(() => false);
    // 设计：判定无变化前再多等 1s。
    if (stripActive(before) === stripActive(after)) {
      await page.waitForTimeout(1000);
      row.no_change_extended = true;
      inc = await page.ariaSnapshot({ mode: 'ai', _track: 'm' });
      after = await page.ariaSnapshot({ mode: 'ai' });
    }
    const pp = postprocess(inc, before, after);
    Object.assign(row, { before_chars: before.length, ...measure(after, inc),
      pp_chars: pp.text.length, pp_ratio: +(pp.text.length / after.length).toFixed(3), pp_no_change: pp.no_change, pp_removed: pp.removed_top });
    // 稳定后再等 2.5s，看是否有迟到变化。
    await page.ariaSnapshot({ mode: 'ai', _track: 'late' });
    await page.waitForTimeout(2500);
    const late = await page.ariaSnapshot({ mode: 'ai', _track: 'late' });
    const lateAfter = await page.ariaSnapshot({ mode: 'ai' });
    row.late_change = stripActive(lateAfter) !== stripActive(after) ? postprocess(late, after, lateAfter).text.length : 0;
    for (const [name, text] of [['before', before], ['incremental', inc], ['processed', pp.text], ['after', after]])
      fs.writeFileSync(path.join(outDir, 'samples', `${sc.id}.${name}.txt`), text);
    if (row.late_change) fs.writeFileSync(path.join(outDir, 'samples', `${sc.id}.late.txt`), late);
  } catch (error) { row.error = String(error.message).split('\n')[0].slice(0, 200); }
  results.push(row);
  console.log(JSON.stringify(row));
  await page.close();
}
await browser.close();
fs.writeFileSync(path.join(outDir, 'results.json'), JSON.stringify({ playwright_core: '1.61.1', ran_at: new Date().toISOString(), results }, null, 2));
console.log('输出目录:', outDir);
