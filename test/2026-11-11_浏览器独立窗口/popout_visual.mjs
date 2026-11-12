// 视觉检查：用真实构建的前端（static/browser-window.html + dist）打开对话浏览器独立窗口，
// 截取整窗（含红绿灯）到 _experiments/browser-popout-20261111/visual-<ts>/。不连真实后端。
// 需先 npm run build。运行：./desktop-electron/node_modules/.bin/electron test/2026-11-11_浏览器独立窗口/popout_visual.mjs [dark|light|classic]
// 整窗截图用 macOS screencapture -l，需要终端有屏幕录制权限；没有时退化为仅 renderer 截图。

import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { app, BaseWindow, WebContentsView } from 'electron';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const output = path.join(root, '_experiments/browser-popout-20261111', `visual-${Date.now()}`);
fs.mkdirSync(output, { recursive: true });
app.setPath('userData', path.join(output, 'userData'));
const theme = ['dark', 'light', 'classic'].includes(process.argv.at(-1)) ? process.argv.at(-1) : 'classic';
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const dist = path.join(root, 'static/dist');
const manifest = JSON.parse(fs.readFileSync(path.join(dist, '.vite/manifest.json'), 'utf8'));
const assemblies = JSON.parse(fs.readFileSync(path.join(dist, '.vite/entry-styles.json'), 'utf8')).assemblies;
const TYPES = { '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2', '.html': 'text/html' };
const CONVERSATION = 'conv_20261111_100000_000';
const PAGE = '<!doctype html><title>Example Domain</title><body style="margin:0;font:16px -apple-system;padding:40px;background:#fff"><h1>Example Domain</h1><p>网页内容夹具。</p></body>';

function browserWindowHtml() {
  return fs.readFileSync(path.join(root, 'static/browser-window.html'), 'utf8')
    .replace('/static/dist/assets/browserWindow.js', `/static/dist/${manifest['static/src/browserWindow.ts'].file}`)
    .replace('/static/browser-window.css', `/static/dist/${assemblies['/static/browser-window.css']}`)
    .replace('<script>', `<script>try{localStorage.setItem('agents_ui_theme','${theme}')}catch(e){}`);
}
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://x');
  const send = (body, type = 'text/html') => { res.setHeader('content-type', type); res.end(body); };
  if (url.pathname === '/api/session-status') send(JSON.stringify({ success: true, session: { logged_in: true, username: 'visual-fixture' } }), 'application/json');
  else if (url.pathname === '/api/conversation-tabs') send(JSON.stringify({ success: true, tabs: [{ key: 'k1', conversation_id: CONVERSATION, title: '调研浏览器独立窗口' }] }), 'application/json');
  else if (url.pathname === '/browser-window') send(browserWindowHtml());
  else if (url.pathname === '/new') send('<!doctype html><body></body>');
  else if (url.pathname.startsWith('/page')) send(PAGE);
  else if (url.pathname.startsWith('/static/')) {
    const file = path.join(root, url.pathname);
    if (!file.startsWith(path.join(root, 'static')) || !fs.existsSync(file)) { res.statusCode = 404; res.end(); return; }
    send(fs.readFileSync(file), TYPES[path.extname(file)] || 'application/octet-stream');
  } else { res.statusCode = 404; res.end(); }
});

async function run() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const { NativeBrowser, setBrowserDataRoot } = await import(path.join(root, 'desktop-electron/src/browser/index.js'));
  setBrowserDataRoot(path.join(output, 'data'));
  const window = new BaseWindow({ x: 40, y: 40, width: 900, height: 600, show: false });
  const main = new WebContentsView({ webPreferences: { sandbox: true, contextIsolation: true, preload: path.join(root, 'desktop-electron/src/preload-main.cjs') } });
  window.contentView.addChildView(main); main.setBounds({ x: 0, y: 0, width: 900, height: 600 });
  const manager = new NativeBrowser(window, main, origin);
  await main.webContents.loadURL(`${origin}/new`);
  const mainRun = code => main.webContents.executeJavaScript(code);
  const snap = await mainRun(`window.astrionBrowser.connect(${JSON.stringify({ mode: 'agent', workspace: 'ws', conversation: CONVERSATION })})`);
  await mainRun(`window.astrionBrowser.command(${JSON.stringify({ lease: snap.lease, action: 'navigate', value: `${origin}/page` })})`);
  await mainRun(`window.astrionBrowser.command(${JSON.stringify({ lease: snap.lease, action: 'new', value: `${origin}/page-2` })})`);
  await mainRun(`window.astrionBrowser.command(${JSON.stringify({ lease: snap.lease, action: 'popout' })})`);
  const popout = [...manager.popouts.values()][0];
  popout.window.setBounds({ x: 120, y: 120, width: 1000, height: 640 });
  await sleep(3500);
  const title = popout.window.getTitle();
  console.log(`INFO window title ${JSON.stringify(title)}`);
  const renderer = await popout.view.webContents.capturePage();
  fs.writeFileSync(path.join(output, `renderer-${theme}.png`), renderer.toPNG());
  const id = popout.window.getMediaSourceId().split(':')[1];
  const full = path.join(output, `window-${theme}.png`);
  try { execFileSync('screencapture', ['-x', '-o', `-l${id}`, full]); console.log(`INFO full window ${full}`); }
  catch (error) { console.log(`INFO screencapture unavailable: ${error.message}`); }
  console.log(`INFO renderer ${path.join(output, `renderer-${theme}.png`)}`);
  fs.writeFileSync(path.join(output, 'info.json'), JSON.stringify({ title, theme, bounds: popout.window.getBounds() }, null, 2));
  manager.dispose(); main.webContents.close(); window.destroy();
}
app.on('window-all-closed', () => {});
app.whenReady().then(run).catch(error => console.log('FAIL', error.stack || error)).finally(() => { server.close(); app.exit(0); });
