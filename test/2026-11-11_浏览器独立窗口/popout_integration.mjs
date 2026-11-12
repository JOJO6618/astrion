// 回归：内置浏览器「单独窗口显示」（macOS Electron）。
// 使用生产 NativeBrowser、生产 preload（主窗口 + 独立窗口）、真实 BaseWindow/WebContentsView 与本机 HTTP 夹具；
// 页面是最小合成 HTML，直接调用 preload 暴露的 astrionBrowser 桥，不加载 Vue、不连真实后端。
// 运行：cd <仓库根> && ./desktop-electron/node_modules/.bin/electron test/2026-11-11_浏览器独立窗口/popout_integration.mjs
// 证据：_experiments/browser-popout-20261111/integration-<ts>/results.json

import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { app, BaseWindow, WebContentsView } from 'electron';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const output = path.join(root, '_experiments/browser-popout-20261111', `integration-${Date.now()}`);
fs.mkdirSync(output, { recursive: true });
app.setPath('userData', path.join(output, 'userData'));
const results = [];
const check = (name, ok, detail = '') => { results.push({ name, ok: !!ok, detail }); console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` ${JSON.stringify(detail)}` : ''}`); };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(fn, label, timeout = 8000) {
  const start = Date.now();
  while (Date.now() - start < timeout) { try { if (await fn()) return; } catch { /* retry */ } await sleep(50); }
  throw new Error(`timeout: ${label}`);
}
const rejects = async (promise, pattern) => { try { await promise; return false; } catch (error) { return pattern.test(String(error?.message || error)); } };

const PAGE = '<!doctype html><title>Fixture Page</title><body style="margin:0;background:#3a7"><p>page</p><script>window.__marker=Math.random().toString(36).slice(2)</script></body>';
const HOST = '<!doctype html><title></title><body></body>';
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://x');
  if (url.pathname === '/api/session-status') {
    res.setHeader('content-type', 'application/json');
    res.end(JSON.stringify({ success: true, session: { logged_in: true, username: 'popout-fixture-user' } }));
  } else if (url.pathname === '/api/browser-retention') {
    res.statusCode = 503; res.end('{}');
  } else if (['/new', '/browser-window'].includes(url.pathname) || /^\/conv_\d{8}_\d{6}_\d{3}$/.test(url.pathname)) {
    res.setHeader('content-type', 'text/html'); res.end(HOST);
  } else if (url.pathname.startsWith('/page')) {
    res.setHeader('content-type', 'text/html'); res.end(PAGE);
  } else { res.statusCode = 404; res.end('missing'); }
});

let manager;
async function run() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const { NativeBrowser, setBrowserDataRoot } = await import(path.join(root, 'desktop-electron/src/browser/index.js'));
  setBrowserDataRoot(path.join(output, 'data'));
  const window = new BaseWindow({ x: 40, y: 40, width: 1200, height: 800, show: false });
  const main = new WebContentsView({ webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, preload: path.join(root, 'desktop-electron/src/preload-main.cjs') } });
  window.contentView.addChildView(main); main.setBounds({ x: 0, y: 46, width: 1200, height: 754 });
  manager = new NativeBrowser(window, main, origin);
  await main.webContents.loadURL(`${origin}/new`);
  window.showInactive();
  const mainRun = code => main.webContents.executeJavaScript(code);
  const scopeA = { mode: 'agent', workspace: 'ws', conversation: 'conv_20261111_100000_000' };
  const scopeB = { mode: 'agent', workspace: 'ws', conversation: 'conv_20261111_100000_001' };
  let mainSnap = await mainRun(`window.astrionBrowser.connect(${JSON.stringify(scopeA)})`);
  let mainLease = mainSnap.lease;
  check('主窗口桥声明可弹出', await mainRun('window.astrionBrowser.popout === true'));
  const mainCommand = (action, value, id, lease = mainLease) => mainRun(`window.astrionBrowser.command(${JSON.stringify({ lease, action, value, id })})`);
  let seq = 0;
  const mainLayout = visible => mainRun(`window.astrionBrowser.layout(${JSON.stringify({ lease: mainLease, sequence: ++seq, visible, rect: { x: 600, y: 92, width: 600, height: 600 } })})`);
  await mainCommand('navigate', `${origin}/page-a`);
  await mainLayout(true);
  const tabA = manager.main.active();
  await until(() => tabA.view.webContents.getURL().endsWith('/page-a') && !tabA.view.webContents.isLoading(), 'page A');
  const ownerA = manager.main.owner;
  const markerA = await tabA.view.webContents.executeJavaScript('__marker');
  check('弹出前网页挂在主窗口并可见', tabA.window === window && tabA.view.getVisible());

  // 弹出
  mainSnap = await mainCommand('popout');
  const popoutA = manager.popouts.get(ownerA);
  check('弹出后主窗口快照为占位态', mainSnap.detached === true && mainSnap.tabs.length === 0 && mainSnap.window === 'main');
  check('独立窗口已创建且为普通窗口（无 parent）', !!popoutA && BaseWindow.getAllWindows().length === 2 && !popoutA.window.getParentWindow?.());
  check('网页视图已挪到独立窗口', tabA.window === popoutA.window && popoutA.window.contentView.children.includes(tabA.view) && !window.contentView.children.includes(tabA.view));
  check('主窗口占位态拒绝标签命令', await rejects(mainCommand('navigate', `${origin}/page-x`), /browser_detached/));
  const popRun = code => popoutA.view.webContents.executeJavaScript(code);
  await until(() => !popoutA.view.webContents.isLoading() && popoutA.view.webContents.getURL().includes('/browser-window'), 'popout page loaded');
  check('独立窗口页加载可信路由并携带对话参数', new URL(popoutA.view.webContents.getURL()).searchParams.get('conversation') === scopeA.conversation);
  check('独立窗口桥声明 window=popout', await popRun('window.astrionBrowser.window === "popout"'));
  // renderer 自报其他对话也只能连到主进程固定的对话
  const popSnap = await popRun(`window.astrionBrowser.connect(${JSON.stringify(scopeB)})`);
  check('独立窗口连接固定为弹出时的对话', popSnap.window === 'popout' && popSnap.tabs.length === 1 && popSnap.tabs[0].id === tabA.id && popoutA.owner === ownerA);
  let popSeq = 0;
  const popLease = popSnap.lease;
  const popLayout = (visible, rect = { x: 0, y: 92, width: 900, height: 600 }) => {
    const content = popoutA.window.getContentBounds();
    return popRun(`window.astrionBrowser.layout(${JSON.stringify({ lease: popLease, sequence: ++popSeq, visible, rect, viewport: { width: content.width, height: content.height, panelWidth: 600, surfaceInset: 0 } })})`);
  };
  await popoutA.window.setContentSize(900, 692);
  await sleep(200);
  await popLayout(true, { x: 0, y: 92, width: 900, height: 600 });
  await until(() => tabA.view.getVisible(), 'popout page visible');
  const b = tabA.view.getBounds();
  check('网页在独立窗口内按上报矩形显示', b.x === 0 && b.y === 92 && b.width === 900 && b.height === 600, b);
  check('网页未重载、状态保留', await tabA.view.webContents.executeJavaScript('__marker') === markerA);
  const popCommand = (action, value, id, lease = popLease) => popRun(`window.astrionBrowser.command(${JSON.stringify({ lease, action, value, id })})`);
  await popCommand('navigate', `${origin}/page-a2`);
  await until(() => tabA.view.webContents.getURL().endsWith('/page-a2'), 'popout navigate');
  check('独立窗口可操作本对话标签', true);
  check('主窗口 lease 不能操作独立窗口', await rejects(popCommand('zoom', 110, undefined, mainLease), /stale_browser_view/));
  // 独立窗口拉大：网页跟随（renderer 追上之前的主进程投影）
  popoutA.window.setContentSize(1000, 742);
  await sleep(300);
  const grown = tabA.view.getBounds();
  check('独立窗口改尺寸时网页按投影伸缩', grown.width === 1000 && grown.height === 650, grown);
  check('AI 面板尺寸取独立窗口宿主', JSON.stringify(manager.boundsFor(ownerA)) === JSON.stringify(grown));

  // 窗口标题 = 页面 document.title（前端写入对话标题）
  await popRun('document.title = "对话标题夹具"');
  await until(() => popoutA.window.getTitle() === '对话标题夹具', 'window title');
  check('窗口标题跟随独立窗口页 document.title', popoutA.window.getTitle() === '对话标题夹具');

  // 主窗口切到另一个对话：独立窗口不受影响
  mainSnap = await mainRun(`window.astrionBrowser.connect(${JSON.stringify(scopeB)})`); mainLease = mainSnap.lease; seq = 0;
  check('主窗口切到未弹出的对话恢复正常面板', mainSnap.detached === false);
  await mainCommand('navigate', `${origin}/page-b`); await mainLayout(true);
  const tabB = manager.main.active();
  await until(() => tabB.view.webContents.getURL().endsWith('/page-b'), 'page B');
  await until(() => tabB.view.getVisible(), 'B visible');
  check('两个对话同时各自显示', tabA.view.getVisible() && tabB.view.getVisible() && tabB.window === window && tabA.window === popoutA.window);

  // 第二个对话也弹出：两个独立窗口，位置错开
  await mainCommand('popout');
  const ownerB = manager.main.owner, popoutB = manager.popouts.get(ownerB);
  const ba = popoutA.window.getBounds(), bb = popoutB.window.getBounds();
  check('第二个对话另开独立窗口且与已开窗口错开', manager.popouts.size === 2 && (ba.x !== bb.x || ba.y !== bb.y), { ba, bb });
  check('再次弹出同一对话只前置已有窗口', (await mainCommand('popout')).detached === true && manager.popouts.size === 2);

  // 位置记忆
  popoutB.window.setBounds({ x: 300, y: 200, width: 820, height: 640 });
  popoutB.window.emit('moved');
  const savedPath = path.join(app.getPath('userData'), 'browser-window.json');
  await until(() => fs.existsSync(savedPath), 'bounds saved');
  const saved = JSON.parse(fs.readFileSync(savedPath, 'utf8'));
  check('独立窗口大小位置写入记忆', saved.width === 820 && saved.height === 640, saved);

  // 关闭独立窗口 B（红绿灯）：收回主窗口，网页存活
  const markerB = await tabB.view.webContents.executeJavaScript('__marker');
  popoutB.window.close();
  await until(() => !manager.popouts.has(ownerB), 'undock B');
  await sleep(200);
  check('关窗后标签回到主窗口且网页存活', tabB.window === window && !tabB.view.webContents.isDestroyed() && await tabB.view.webContents.executeJavaScript('__marker') === markerB);
  mainSnap = await mainRun(`window.astrionBrowser.connect(${JSON.stringify(scopeB)})`); mainLease = mainSnap.lease; seq = 0;
  await mainLayout(true);
  await until(() => tabB.view.getVisible(), 'B back in main');
  check('收回后主窗口重新显示该对话网页', mainSnap.detached === false && tabB.view.getVisible());

  // 新开的独立窗口使用记忆的大小
  await mainCommand('popout');
  const popoutB2 = manager.popouts.get(ownerB);
  const b2 = popoutB2.window.getBounds();
  check('再次弹出使用记忆的大小', b2.width === 820 && b2.height === 640, b2);
  // 从主窗口占位「收回」
  await mainCommand('dock');
  await until(() => !manager.popouts.has(ownerB), 'dock from main');
  check('主窗口占位可收回', tabB.window === window);

  // 从独立窗口「收回」按钮
  // 收回会关闭发起命令的窗口本身，回包无人接收；页面内触发后不等待结果（与真实界面一致）。
  await popRun(`void window.astrionBrowser.command(${JSON.stringify({ lease: popLease, action: 'dock' })}).catch(() => {}); true`);
  await until(() => !manager.popouts.has(ownerA), 'dock from popout');
  await sleep(200);
  check('独立窗口收回后标签回主窗口、窗口关闭', tabA.window === window && popoutA.window.isDestroyed() && !tabA.view.webContents.isDestroyed());

  // 主窗口销毁时独立窗口一起关闭
  mainSnap = await mainRun(`window.astrionBrowser.connect(${JSON.stringify(scopeA)})`); mainLease = mainSnap.lease;
  await mainCommand('popout');
  const last = manager.popouts.get(ownerA);
  manager.dispose();
  await sleep(300);
  check('dispose 关闭所有独立窗口', last.window.isDestroyed() && manager.popouts.size === 0);
  main.webContents.close(); window.destroy();
}

let finished = false;
function finish(error) {
  if (finished) return; finished = true;
  if (error) { console.log('FAIL 异常', error.stack || error); results.push({ name: '异常', ok: false, detail: String(error.stack || error) }); }
  const failed = results.filter(row => !row.ok).length;
  fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify({ at: new Date().toISOString(), electron: process.versions.electron, passed: results.length - failed, failed, results }, null, 2));
  console.log(`\n${results.length - failed}/${results.length} passed; evidence ${path.relative(root, output)}/results.json`);
  try { manager?.dispose(); } catch { /* already disposed */ }
  server.close();
  app.exit(failed ? 1 : 0);
}
setTimeout(() => finish(new Error('global timeout')), 90000).unref();
app.on('window-all-closed', () => {});
app.whenReady().then(run).then(() => finish(), finish);
