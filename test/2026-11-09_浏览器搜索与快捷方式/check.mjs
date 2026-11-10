// 搜索引擎与新标签页快捷方式的回归（Electron 主进程环境，无需窗口）。
// 运行：desktop-electron/node_modules/.bin/electron test/2026-11-09_浏览器搜索与快捷方式/check.mjs
import { app } from 'electron';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, '../..');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'astrion-search-'));
app.setPath('userData', path.join(temp, 'electron'));
const results = [];
const check = (name, ok, detail = '') => { results.push({ name, ok: !!ok }); console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${ok ? '' : ` :: ${detail}`}`); };
const throws = fn => { try { fn(); return false; } catch { return true; } };

app.whenReady().then(async () => {
  try {
    const policy = await import(pathToFileURL(path.join(repo, 'desktop-electron/src/browser/policy.js')).href);
    const { BrowserProfile, normalizeShortcuts } = await import(pathToFileURL(path.join(repo, 'desktop-electron/src/browser/profile.js')).href);
    // 搜索地址
    check('关键词走所选引擎（百度）', policy.navigationURL('rust 教程', 'baidu') === 'https://www.baidu.com/s?wd=rust%20%E6%95%99%E7%A8%8B');
    check('必应国内', policy.navigationURL('hello world', 'bing_cn').startsWith('https://cn.bing.com/search?q=hello'));
    check('true 兼容为谷歌', policy.navigationURL('hello world', true).startsWith('https://www.google.com/search?q='));
    check('网址不被当成搜索', policy.navigationURL('github.com/a', 'baidu') === 'https://github.com/a');
    check('localhost:端口不被当成搜索', policy.navigationURL('localhost:5173', 'baidu') === 'http://localhost:5173/');
    check('不允许搜索时关键词报错', throws(() => policy.navigationURL('hello world')));
    // 资料保存与校验
    const profile = new BrowserProfile(path.join(temp, 'data'), 'host');
    check('未设置时回退谷歌', profile.searchEngine() === 'google' && profile.data.settings.searchEngine === '');
    profile.setSearchEngine('bing_cn');
    check('设置引擎', profile.searchEngine() === 'bing_cn');
    check('拒绝未知引擎', throws(() => profile.setSearchEngine('yahoo')));
    profile.setShortcuts({ pinned: [{ url: 'https://github.com/', title: ' GitHub ' }, { url: 'https://github.com/', title: 'dup' }], hidden: ['Example.COM'] });
    check('快捷方式去重与清洗', JSON.stringify(profile.data.shortcuts) === JSON.stringify({ pinned: [{ url: 'https://github.com/', title: 'GitHub' }], hidden: ['example.com'] }), JSON.stringify(profile.data.shortcuts));
    check('拒绝 javascript: 快捷方式', throws(() => profile.setShortcuts({ pinned: [{ url: 'javascript:alert(1)', title: 'x' }], hidden: [] })));
    check('拒绝超过 9 个固定项', throws(() => profile.setShortcuts({ pinned: Array.from({ length: 10 }, (_, i) => ({ url: `https://s${i}.com/`, title: '' })), hidden: [] })));
    check('空标题回退为主机名', normalizeShortcuts({ pinned: [{ url: 'https://www.zhihu.com/', title: '' }] }).pinned[0].title === 'zhihu.com');
    profile.flush(true);
    const reloaded = new BrowserProfile(path.join(temp, 'data'), 'host');
    check('重启后保留设置与快捷方式', reloaded.searchEngine() === 'bing_cn' && reloaded.data.shortcuts.pinned.length === 1);
    // 新标签页最常访问（TS 模块经 esbuild 转译）
    const out = path.join(temp, 'shortcuts.mjs');
    execFileSync(path.join(repo, 'node_modules/.bin/esbuild'), [path.join(repo, 'static/src/components/browser/newTabShortcuts.ts'), '--format=esm', `--outfile=${out}`], { stdio: 'pipe' });
    const nt = await import(pathToFileURL(out).href);
    const now = Date.now();
    const history = [
      { id: '1', url: 'https://www.github.com/a', title: 'a', at: now },
      { id: '2', url: 'https://github.com/b', title: 'b', at: now - 1 },
      { id: '3', url: 'https://www.google.com/search?q=x', title: 's', at: now - 2 },
      { id: '4', url: 'https://www.google.com/search?q=y', title: 's', at: now - 3 },
      { id: '5', url: 'https://news.ycombinator.com/', title: 'hn', at: now - 4 },
      { id: '6', url: 'https://example.com/', title: 'e', at: now - 5 },
      { id: '7', url: 'https://example.com/x', title: 'e', at: now - 6 },
      { id: '8', url: 'https://accounts.google.com/o/oauth2', title: 'a', at: now - 7 },
      { id: '9', url: 'https://accounts.google.com/signin', title: 'a', at: now - 8 },
      { id: '10', url: 'https://auth.openai.com/log-in', title: 'a', at: now - 9 },
      { id: '11', url: 'https://auth.openai.com/log-in', title: 'a', at: now - 10 }
    ];
    let tiles = nt.shortcutTiles(history, { pinned: [], hidden: [] });
    check('按访问次数排序、合并 www、排除搜索结果页与登录跳转页', tiles.map(t => t.key).join() === 'github.com,example.com,news.ycombinator.com', tiles.map(t => t.key).join());
    tiles = nt.shortcutTiles(history, { pinned: [{ url: 'https://zhihu.com/', title: '知乎' }], hidden: ['example.com'] });
    check('固定项在前，隐藏项不出现', tiles.map(t => t.key).join() === 'zhihu.com,github.com,news.ycombinator.com', tiles.map(t => t.key).join());
    tiles = nt.shortcutTiles(history, { pinned: [{ url: 'https://github.com/x', title: 'gh' }], hidden: [] });
    check('已固定站点不在最常访问中重复', tiles.filter(t => t.key === 'github.com').length === 1);
    check('最多 9 个（第 10 格留给添加快捷方式）', nt.shortcutTiles(Array.from({ length: 30 }, (_, i) => ({ id: String(i), url: `https://s${i}.com/`, title: '', at: now - i })), { pinned: [], hidden: [] }).length === nt.MAX_SHORTCUTS);
    check('输入网址补全 https', nt.shortcutURL('github.com') === 'https://github.com/' && nt.shortcutURL('localhost:3000') === 'http://localhost:3000/');
    check('拒绝无效网址', nt.shortcutURL('hello world') === '' && nt.shortcutURL('javascript:alert(1)') === '' && nt.shortcutURL('intranet') === '');
  } catch (error) {
    check('脚本运行', false, error.stack);
  }
  const failed = results.filter(r => !r.ok).length;
  console.log(JSON.stringify({ passed: results.length - failed, failed }));
  fs.rmSync(temp, { recursive: true, force: true });
  app.exit(failed ? 1 : 0);
});
