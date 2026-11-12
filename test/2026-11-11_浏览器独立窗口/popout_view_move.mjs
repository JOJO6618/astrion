// 实验：内置浏览器「单独窗口显示」的前置验证（macOS Electron 35）。
// 问题：把网页 WebContentsView 从主 BaseWindow 挪到另一个 BaseWindow（以及挪回）时，
// 页面状态、CDP 调试连接、输入、焦点、视口、截图、popup 接管、窗口关闭回收是否正常。
// 只用合成页面与本地 HTTP 服务；不触碰真实用户资料、不连后端。
// 运行：cd <仓库根> && ./desktop-electron/node_modules/.bin/electron test/2026-11-11_浏览器独立窗口/popout_view_move.mjs
// 结果另存：_experiments/browser-popout-20261111/results.json

import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { app, BaseWindow, WebContentsView } from 'electron';
import { SECURE_WEB_PREFERENCES } from '../../desktop-electron/src/browser/policy.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const OUT = path.join(ROOT, '_experiments/browser-popout-20261111');
const results = [];
const info = {};
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` ${typeof detail === 'string' ? detail : JSON.stringify(detail)}` : ''}`);
};
const note = (name, value) => { info[name] = value; console.log(`INFO ${name} ${JSON.stringify(value)}`); };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const withTimeout = (promise, ms) => Promise.race([promise, sleep(ms).then(() => 'timeout')]);

// 隔离 userData，不碰正式 Astrion 资料。
app.setPath('userData', path.join(OUT, 'userData'));
setTimeout(() => { console.log('FAIL 全局超时'); finish(); }, 90000).unref();

const PAGE = `<!doctype html><html><body style="margin:0;background:#3a7;height:100vh">
<input id="t" style="position:absolute;left:10px;top:10px;width:200px">
<button id="b" style="position:absolute;left:100px;top:100px;width:200px;height:100px">btn</button>
<script>
window.__marker = Math.random().toString(36).slice(2);
window.__clicks = 0; window.__ticks = 0; window.__focus = [];
document.getElementById('b').addEventListener('click', () => window.__clicks++);
setInterval(() => window.__ticks++, 100);
addEventListener('focus', () => __focus.push('focus')); addEventListener('blur', () => __focus.push('blur'));
</script></body></html>`;
const POPUP = '<!doctype html><html><body style="background:#a37"><p>popup</p></body></html>';
let pageHits = 0;
const server = http.createServer((req, res) => {
  if (req.url === '/') pageHits++;
  res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
  res.end(req.url === '/popup' ? POPUP : PAGE);
});

function makeWindow(options) {
  const win = new BaseWindow({ width: 900, height: 640, backgroundColor: '#202020', ...options });
  win.contentView.setBackgroundColor('#202020');
  return win;
}
function contentRect(win, top = 0) {
  const { width, height } = win.getContentBounds();
  return { x: 0, y: top, width, height: height - top };
}
function move(view, from, to, bounds) {
  from.contentView.removeChildView(view);
  to.contentView.addChildView(view);
  view.setBounds(bounds);
}
const js = (wc, code, gesture = false) => wc.executeJavaScript(code, gesture);

async function probe(label, wc, view, win, dbg) {
  const bounds = view.getBounds();
  await sleep(400);
  const state = await js(wc, '({ marker: __marker, value: document.getElementById("t").value, w: innerWidth, h: innerHeight, dpr: devicePixelRatio })');
  check(`${label}: webContents 存活且未重载`, !wc.isDestroyed() && pageHits === 1, { pageHits });
  check(`${label}: 页面 JS/表单状态保留`, state.marker === info.marker && state.value === info.typed, state);
  const zoom = wc.getZoomFactor();
  check(`${label}: 视口跟随新 bounds`, Math.abs(state.w * zoom - bounds.width) <= 2 && Math.abs(state.h * zoom - bounds.height) <= 2, { bounds, inner: [state.w, state.h], zoom });
  check(`${label}: 缩放保留`, Math.abs(zoom - 1.25) < 0.01, { zoom });
  check(`${label}: CDP 仍连接`, dbg.isAttached());
  const evaluated = await withTimeout(dbg.sendCommand('Runtime.evaluate', { expression: '__marker', returnByValue: true }), 3000);
  check(`${label}: CDP Runtime.evaluate 可用`, evaluated !== 'timeout' && evaluated.result?.value === info.marker);

  // 原生输入（Electron sendInputEvent，与 input.js 的转发相同）
  const before = await js(wc, '__clicks');
  // 按钮在 CSS (100..300, 100..200)，缩放 1.25 后的视图坐标需乘 zoom。
  const px = Math.round(200 * zoom), py = Math.round(150 * zoom);
  wc.sendInputEvent({ type: 'mouseDown', x: px, y: py, button: 'left', clickCount: 1 });
  wc.sendInputEvent({ type: 'mouseUp', x: px, y: py, button: 'left', clickCount: 1 });
  await sleep(200);
  const afterNative = await js(wc, '__clicks');
  check(`${label}: sendInputEvent 点击送达`, afterNative === before + 1, { before, afterNative });
  // CDP 输入（AI 驱动的路径）
  await dbg.sendCommand('Input.dispatchMouseEvent', { type: 'mousePressed', x: 200, y: 150, button: 'left', clickCount: 1 });
  await dbg.sendCommand('Input.dispatchMouseEvent', { type: 'mouseReleased', x: 200, y: 150, button: 'left', clickCount: 1 });
  await sleep(200);
  const afterCdp = await js(wc, '__clicks');
  check(`${label}: CDP 点击送达`, afterCdp === afterNative + 1, { afterNative, afterCdp });

  // 焦点与键盘
  win.focus(); wc.focus();
  await js(wc, 'document.getElementById("t").focus()');
  wc.sendInputEvent({ type: 'char', keyCode: 'x' });
  await sleep(200);
  info.typed += 'x';
  const focusState = await js(wc, '({ value: document.getElementById("t").value, hasFocus: document.hasFocus() })');
  check(`${label}: 键盘输入送达`, focusState.value === info.typed, focusState);
  note(`${label}: 焦点`, { wcFocused: wc.isFocused(), docHasFocus: focusState.hasFocus, windowFocused: win.isFocused() });

  // 可见截图
  const shot = await withTimeout(wc.capturePage(), 5000);
  const size = shot === 'timeout' ? null : shot.getSize();
  check(`${label}: 可见 capturePage`, size && size.width > 0 && !shot.isEmpty(), size || 'timeout');
}

async function hiddenProbe(label, wc, view, dbg) {
  view.setVisible(false);
  const t0 = await js(wc, '__ticks');
  await sleep(1500);
  const t1 = await js(wc, '__ticks');
  note(`${label}: 隐藏 1.5s 定时器 tick 数`, t1 - t0);
  const shot = await withTimeout(wc.capturePage(), 5000);
  note(`${label}: 隐藏 capturePage`, shot === 'timeout' ? 'timeout' : { ...shot.getSize(), empty: shot.isEmpty() });
  const cdpShot = await withTimeout(dbg.sendCommand('Page.captureScreenshot', { format: 'png' }), 3000);
  note(`${label}: 隐藏 CDP 截图`, cdpShot === 'timeout' ? 'timeout' : 'ok');
  const evaluated = await withTimeout(dbg.sendCommand('Runtime.evaluate', { expression: '__marker', returnByValue: true }), 3000);
  check(`${label}: 隐藏时 CDP evaluate 可用`, evaluated !== 'timeout' && evaluated.result?.value === info.marker);
  view.setVisible(true);
  await sleep(200);
}

async function run() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  if (process.platform === 'darwin') app.focus({ steal: true });

  // 主窗口替身：标题栏隐藏 + 顶部 46px 留给 chrome 条，与 window.js 一致。
  const main = makeWindow({ x: 60, y: 60, titleBarStyle: 'hidden', trafficLightPosition: { x: 15, y: 15 } });
  const hostView = new WebContentsView({ webPreferences: { sandbox: true, contextIsolation: true } });
  hostView.setBounds(contentRect(main, 46));
  main.contentView.addChildView(hostView);
  void hostView.webContents.loadURL('data:text/html,<body style="background:#246">host</body>');

  const view = new WebContentsView({ webPreferences: { ...SECURE_WEB_PREFERENCES } });
  main.contentView.addChildView(view);
  view.setBounds({ x: 400, y: 120, width: 480, height: 480 });
  const wc = view.webContents;
  await wc.loadURL(base + '/');
  wc.setZoomFactor(1.25);
  info.marker = await js(wc, '__marker');
  await js(wc, 'document.getElementById("t").value = "keep"');
  info.typed = 'keep';
  const dbg = wc.debugger; dbg.attach('1.3');
  await dbg.sendCommand('Runtime.enable');
  const wcId = wc.id;
  await probe('主窗口(基线)', wc, view, main, dbg);

  // 弹出窗口：普通窗口，隐藏标题栏但保留红绿灯，网页铺满（顶部 40px 留给标签条/工具栏替身）。
  const pop = makeWindow({ x: 1000, y: 120, width: 820, height: 600, titleBarStyle: 'hidden', trafficLightPosition: { x: 14, y: 14 }, title: '测试对话标题' });
  pop.setTitle('测试对话标题');
  note('弹出窗口 getTitle', pop.getTitle());
  move(view, main, pop, contentRect(pop, 80));
  check('移动后 webContents id 不变', wc.id === wcId);
  check('主窗口已不含该视图', !main.contentView.children.includes(view));
  check('弹出窗口含该视图', pop.contentView.children.includes(view));
  await probe('弹出窗口', wc, view, pop, dbg);

  // 弹出窗口里改尺寸：视口跟随
  pop.setContentSize(1000, 700);
  view.setBounds(contentRect(pop, 80));
  await sleep(400);
  const resized = await js(wc, '({ w: innerWidth, h: innerHeight })');
  const rb = view.getBounds();
  check('弹出窗口缩放后视口跟随', Math.abs(resized.w * 1.25 - rb.width) <= 2 && Math.abs(resized.h * 1.25 - rb.height) <= 2, { rb, resized });

  await hiddenProbe('弹出窗口', wc, view, dbg);

  // 弹出窗口最小化：AI 在后台操作的场景
  pop.minimize();
  await sleep(800);
  const minClicks = await js(wc, '__clicks');
  await dbg.sendCommand('Input.dispatchMouseEvent', { type: 'mousePressed', x: 200, y: 150, button: 'left', clickCount: 1 });
  await dbg.sendCommand('Input.dispatchMouseEvent', { type: 'mouseReleased', x: 200, y: 150, button: 'left', clickCount: 1 });
  await sleep(300);
  check('弹出窗口最小化时 CDP 点击送达', await js(wc, '__clicks') === minClicks + 1);
  const minShot = await withTimeout(wc.capturePage(), 5000);
  note('弹出窗口最小化 capturePage', minShot === 'timeout' ? 'timeout' : { ...minShot.getSize(), empty: minShot.isEmpty() });
  const mt0 = await js(wc, '__ticks'); await sleep(1500);
  note('弹出窗口最小化 1.5s 定时器 tick 数', (await js(wc, '__ticks')) - mt0);
  pop.restore();
  await sleep(500);

  // popup 接管：与 tabs.js 相同的 createWindow 写法，新视图挂到弹出窗口
  let popupView = null;
  wc.setWindowOpenHandler(() => ({
    action: 'allow', outlivesOpener: true,
    overrideBrowserWindowOptions: { webPreferences: { ...SECURE_WEB_PREFERENCES } },
    createWindow: nativeOptions => {
      popupView = new WebContentsView({ ...(nativeOptions?.webContents ? { webContents: nativeOptions.webContents } : {}), webPreferences: { ...SECURE_WEB_PREFERENCES } });
      pop.contentView.addChildView(popupView);
      popupView.setBounds({ x: 40, y: 120, width: 300, height: 200 });
      return popupView.webContents;
    }
  }));
  await js(wc, `window.__popup = window.open('${base}/popup'); !!window.__popup`, true);
  await sleep(1200);
  check('弹出窗口内 popup 被接管为视图', !!popupView && popupView.webContents.getURL() === base + '/popup', popupView?.webContents.getURL());
  if (popupView) {
    const opener = await js(popupView.webContents, 'window.opener ? window.opener.__marker : null');
    check('popup 保留 opener', opener === info.marker, { opener });
  }

  // 挪回主窗口（收回）：两个视图一起挪
  move(view, pop, main, { x: 400, y: 120, width: 480, height: 480 });
  if (popupView) move(popupView, pop, main, { x: 40, y: 120, width: 300, height: 200 });
  await probe('收回主窗口', wc, view, main, dbg);

  // 再弹出，然后直接关闭弹出窗口：在 close 事件里把视图挪回主窗口
  const pop2 = makeWindow({ x: 1000, y: 160, width: 700, height: 520, titleBarStyle: 'hidden', trafficLightPosition: { x: 14, y: 14 } });
  move(view, main, pop2, contentRect(pop2, 80));
  if (popupView) move(popupView, main, pop2, { x: 40, y: 120, width: 300, height: 200 });
  await sleep(300);
  pop2.on('close', () => {
    move(view, pop2, main, { x: 400, y: 120, width: 480, height: 480 });
    if (popupView) move(popupView, pop2, main, { x: 40, y: 120, width: 300, height: 200 });
  });
  const closed = new Promise(resolve => pop2.once('closed', resolve));
  pop2.close();
  await closed;
  await sleep(300);
  check('关闭弹出窗口(先挪出视图)后网页存活', !wc.isDestroyed() && !(popupView?.webContents.isDestroyed()));
  check('关闭后视图回到主窗口', main.contentView.children.includes(view));
  await probe('关闭弹窗后', wc, view, main, dbg);

  // 对照：不挪出就关窗口，子视图 webContents 会怎样（决定生命周期实现）
  const ctrl = makeWindow({ x: 1100, y: 200, width: 400, height: 300 });
  const orphan = new WebContentsView({ webPreferences: { ...SECURE_WEB_PREFERENCES } });
  ctrl.contentView.addChildView(orphan);
  orphan.setBounds({ x: 0, y: 0, width: 400, height: 300 });
  await orphan.webContents.loadURL(base + '/popup');
  const ctrlClosed = new Promise(resolve => ctrl.once('closed', resolve));
  ctrl.close(); await ctrlClosed; await sleep(500);
  note('对照：未挪出直接关窗后 webContents.isDestroyed()', orphan.webContents.isDestroyed());
  if (!orphan.webContents.isDestroyed()) {
    const value = await withTimeout(orphan.webContents.executeJavaScript('document.body.innerText'), 2000);
    note('对照：孤立视图仍可执行脚本', value);
    // 孤立视图能否再挂到别的窗口
    main.contentView.addChildView(orphan);
    orphan.setBounds({ x: 10, y: 60, width: 200, height: 120 });
    await sleep(300);
    const shot = await withTimeout(orphan.webContents.capturePage(), 3000);
    note('对照：孤立视图重新挂载后截图', shot === 'timeout' ? 'timeout' : { ...shot.getSize(), empty: shot.isEmpty() });
    orphan.webContents.close();
  }

  // 两个弹出窗口同时存在（多对话各自弹出）
  const a = makeWindow({ x: 980, y: 80, width: 500, height: 400, titleBarStyle: 'hidden', trafficLightPosition: { x: 14, y: 14 } });
  const b = makeWindow({ x: 1020, y: 120, width: 500, height: 400, titleBarStyle: 'hidden', trafficLightPosition: { x: 14, y: 14 } });
  move(view, main, a, contentRect(a, 80));
  const second = new WebContentsView({ webPreferences: { ...SECURE_WEB_PREFERENCES } });
  b.contentView.addChildView(second); second.setBounds(contentRect(b, 80));
  await second.webContents.loadURL(base + '/popup');
  await sleep(300);
  const both = await Promise.all([js(wc, '__marker'), js(second.webContents, 'document.body.innerText')]);
  check('两个弹出窗口并存且各自网页正常', both[0] === info.marker && both[1].includes('popup'), both);
  move(view, a, main, { x: 400, y: 120, width: 480, height: 480 });
  b.contentView.removeChildView(second); second.webContents.close();
  a.close(); b.close();
  await sleep(300);
  check('多窗口来回后原网页仍存活', !wc.isDestroyed() && (await js(wc, '__marker')) === info.marker);

  dbg.detach();
  if (popupView && !popupView.webContents.isDestroyed()) popupView.webContents.close();
  wc.close();
  hostView.webContents.close();
  main.close();
}

let finished = false;
function finish() {
  if (finished) return; finished = true;
  fs.mkdirSync(OUT, { recursive: true });
  const failed = results.filter(row => !row.ok).length;
  fs.writeFileSync(path.join(OUT, 'results.json'), JSON.stringify({ at: new Date().toISOString(), electron: process.versions.electron, platform: process.platform, passed: results.length - failed, failed, results, info }, null, 2));
  console.log(`\n${results.length - failed}/${results.length} passed; evidence ${path.join(OUT, 'results.json')}`);
  server.close();
  app.exit(failed ? 1 : 0);
}

app.on('window-all-closed', () => { /* 由 finish 统一退出 */ });
app.whenReady().then(run).catch(error => { console.log('FAIL 异常', error?.stack || error); results.push({ name: '异常', ok: false, detail: String(error?.stack || error) }); }).finally(finish);
