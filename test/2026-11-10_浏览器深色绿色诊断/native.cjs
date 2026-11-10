const { app, View, WebContentsView, desktopCapturer, systemPreferences } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { pathToFileURL } = require('node:url');
const { spawn, execFileSync } = require('node:child_process');
const root = path.resolve(__dirname, '../..');
const output = path.join(root, '_experiments/browser-green', String(Date.now()));
fs.mkdirSync(output, { recursive: true });
for (const name of ['userData', 'sessionData']) app.setPath(name, path.join(output, 'electron'));
app.commandLine.appendSwitch('user-data-dir', path.join(output, 'electron'));
const nativeColors = new WeakMap();
const original = View.prototype.setBackgroundColor;
View.prototype.setBackgroundColor = function (color) { nativeColors.set(this, color); return original.call(this, color); };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const samples = [], events = [];
let server, win, main, manager, screenshotAllowed, backend;
const sourceRoot = process.env.GREEN_SOURCE || path.join(root, 'desktop-electron');
const backendRoot = process.env.GREEN_BACKEND_ROOT || root;
const load = file => import(pathToFileURL(path.join(sourceRoot, file)).href);
const until = async (condition, label) => {
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) { if (await condition()) return; await sleep(20); }
  throw new Error('Timeout: ' + label);
};
function pixels(image) {
  const bitmap = image.toBitmap(), counts = new Map();
  let green = 0, opaque = 0;
  for (let i = 0; i < bitmap.length; i += 4) {
    const b = bitmap[i], g = bitmap[i + 1], r = bitmap[i + 2], a = bitmap[i + 3];
    if (a < 240) continue;
    opaque++;
    if (g > r + 8 && g > b + 8 && g > 20) green++;
    const key = '#' + [r, g, b].map(n => n.toString(16).padStart(2, '0')).join('');
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  return { size: image.getSize(), opaque, green, top: [...counts].sort((a, b) => b[1] - a[1]).slice(0, 5) };
}
async function saveImage(label, kind, image) {
  if (image.isEmpty()) return { empty: true };
  const file = `${label}-${kind}.png`;
  fs.writeFileSync(path.join(output, file), image.toPNG());
  return { file, ...pixels(image) };
}
async function sample(label, includePage = true) {
  const active = manager.active();
  const state = {
    label, time: Date.now(),
    window: win.getBackgroundColor(), root: nativeColors.get(win.contentView),
    main: nativeColors.get(main), page: active?.view && nativeColors.get(active.view),
    nativeThemeColor: manager.surfaceColor,
    visible: active?.view?.getVisible(), bounds: active?.view?.getBounds(),
    raised: manager.stacking.raised,
    sourceRoot,
    children: win.contentView.children.map(v => v === main ? 'main' : v === active?.view ? 'page' : 'chrome'),
    renderer: await main.webContents.executeJavaScript('window.surfaceDiag.inspect()')
  };
  if (label === 'dark-internal-held') {
    state.rootRules = await main.webContents.executeJavaScript(fs.readFileSync(path.join(__dirname, 'root-rules.js'), 'utf8'));
    console.log('ROOT_RULES ' + JSON.stringify(state.rootRules));
  }
  if (screenshotAllowed) {
    const sources = await desktopCapturer.getSources({ types: ['window'], thumbnailSize: { width: 1600, height: 1000 } });
    const source = sources.find(row => row.id === win.getMediaSourceId());
    if (source) state.windowPixels = await saveImage(label, 'window', source.thumbnail);
  }
  state.mainPixels = await saveImage(label, 'main', await main.webContents.capturePage());
  if (includePage && active?.view?.getVisible()) state.pagePixels = await saveImage(label, 'page', await active.view.webContents.capturePage());
  samples.push(state);
  fs.writeFileSync(path.join(output, 'progress.json'), JSON.stringify({ label, samples }, null, 2));
  console.log(JSON.stringify({ label, visible: state.visible, color: state.nativeThemeColor, greenMain: state.mainPixels.green, greenPage: state.pagePixels?.green, greenWindow: state.windowPixels?.green }));
}
app.whenReady().then(async () => {
  const timeout = setTimeout(() => { console.error('diagnostic deadline exceeded'); app.exit(1); }, 115000);
  try {
    screenshotAllowed = systemPreferences.getMediaAccessStatus('screen') === 'granted';
    const assets = path.join(root, '_experiments/browser-green-fixture');
    server = http.createServer((request, response) => {
      const route = new URL(request.url, 'http://fixture').pathname;
      if (route === '/api/session-status') { response.setHeader('Content-Type', 'application/json'); response.end(JSON.stringify({ success: true, session: { logged_in: true, username: 'green-fixture' } })); }
      else if (route === '/api/browser-retention') { response.statusCode = 503; response.end(); }
      else if (route === '/chrome') { response.setHeader('Content-Type', 'text/html'); response.end('<!doctype html><title>Diagnostic chrome</title>'); }
      else if (route === '/slow') {
        setTimeout(() => { if (!response.destroyed) { response.setHeader('Content-Type', 'text/html'); response.end('<!doctype html><meta charset="utf-8"><title>Native background fixture</title><link rel="stylesheet" href="/page.css"><h1>本地网页内容</h1><p>用于检测绿色背景，不包含绿色图形。</p>'); } }, 650);
      } else if (route === '/page.css') {
        setTimeout(() => { response.setHeader('Content-Type', 'text/css'); response.end('body{margin:0;background:#ececec;color:#222;font:24px system-ui;padding:40px}'); }, 250);
      } else {
        const file = route === '/new' ? path.join(assets, 'index.html') : path.join(assets, route.replace(/^\//, ''));
        if (!file.startsWith(assets + '/') || !fs.existsSync(file) || !fs.statSync(file).isFile()) { response.statusCode = 404; response.end(); return; }
        response.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
        response.end(fs.readFileSync(file));
      }
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const port = server.address().port;
    const { NativeBrowser, setBrowserDataRoot } = await load('src/browser/index.js');
    const register = NativeBrowser.prototype.register;
    NativeBrowser.prototype.register = function () { manager = this; return register.call(this); };
    setBrowserDataRoot(path.join(output, 'runtime'));
    const { createMainWindow, getMainView } = await load('src/window.js');
    const full = process.env.GREEN_FULL_APP === '1';
    let applicationPort = port;
    if (full) {
      const { pickFreePort, waitBackendReady } = await load('src/backend.js');
      applicationPort = await pickFreePort();
      const workspace = path.join(output, 'workspace');
      fs.mkdirSync(workspace, { recursive: true });
      const env = { ...process.env };
      for (const key of Object.keys(env)) if (/^(ASTRION_|AGENT_|DATA_DIR$|LOGS_DIR$|USER_SPACE_DIR$|API_USER_SPACE_DIR$|DEPLOY_CONFIG_DIR$)/.test(key)) delete env[key];
      Object.assign(env, { ASTRION_IGNORE_DOTENV: '1', ASTRION_DATA_ROOT: path.join(output, 'runtime'), ASTRION_DESKTOP_VERSION: '0.8.0', TERMINAL_SANDBOX_MODE: 'host', WEB_SERVER_HOST: '127.0.0.1', PYTHONDONTWRITEBYTECODE: '1' });
      const python = path.join(root, '_experiments/macos-browser-20261109-ai/mac-arm64/Astrion.app/Contents/Resources/runtime/python/bin/python3.12');
      execFileSync(python, ['-B', '-c', 'import sys; from modules.host_workspace_manager import create_host_workspace; create_host_workspace(sys.argv[1], "green-fixture", set_default=True)', workspace], { cwd: backendRoot, env, stdio: 'pipe' });
      backend = spawn(python, ['-B', '-m', 'server.app', '--path', workspace, '--port', String(applicationPort)], { cwd: backendRoot, env, stdio: ['ignore', 'pipe', 'pipe'] });
      const log = fs.createWriteStream(path.join(output, 'backend.log'));
      backend.stdout.pipe(log, { end: false }); backend.stderr.pipe(log, { end: false });
      await waitBackendReady(applicationPort);
    }
    win = createMainWindow(applicationPort, full ? '/new' : '/new?theme=dark'); main = getMainView();
    main.webContents.on('console-message', (_event, level, message) => { if (level >= 2) fs.appendFileSync(path.join(output, 'renderer.log'), message + '\n'); });
    if (full) {
      const setup = fs.readFileSync(path.join(__dirname, 'setup-actual.js'), 'utf8');
      await until(() => main.webContents.executeJavaScript(setup).catch(() => false), 'complete application Pinia mounts');
      await until(() => !!manager.lease, 'complete application opens its browser');
      console.log('FULL_APPLICATION ' + JSON.stringify(await main.webContents.executeJavaScript('window.surfaceDiag.inspect()')));
    }
    await until(() => main.webContents.executeJavaScript('!!window.surfaceDiag?.ready').catch(() => false), 'current Vue BrowserPanel connects');
    win.show();
    if (process.env.GREEN_THEME_SWITCH === '1') {
      await require('./theme-switch.cjs')(main, manager, output, until, sleep, nativeColors);
    }
    const internal = process.env.GREEN_INTERNAL === '1';
    const continuous = internal || process.env.GREEN_CONTINUOUS === '1';
    if (continuous) {
      const recorder = fs.readFileSync(path.join(__dirname, 'record.js'), 'utf8');
      const result = await main.webContents.executeJavaScript(`${recorder}\nrecordDiagnosticWindow(${JSON.stringify(win.getMediaSourceId())})`);
      console.log('RECORDER ' + JSON.stringify(result));
    }
    for (const theme of (process.env.GREEN_ONLY_DARK === '1' ? ['dark'] : ['dark', 'light', 'classic'])) {
      await main.webContents.executeJavaScript(`window.surfaceDiag.theme(${JSON.stringify(theme)})`);
      await sleep(150);
      const start = Date.now();
      void main.webContents.executeJavaScript(`window.surfaceDiag.navigate('http://127.0.0.1:${port}/slow?${theme}=${start}')`);
      await sleep(120);
      await sample(`${theme}-before-commit`, false);
      await until(() => !!manager.active()?.view?.getVisible(), 'native page becomes visible');
      const page = manager.active().view.webContents;
      for (const event of ['did-start-loading', 'did-stop-loading', 'did-finish-load', 'did-navigate']) page.on(event, () => events.push({ theme, event, at: Date.now() }));
      for (let i = 0; i < 3; i++) { await sample(`${theme}-loaded-${i}`); await sleep(130); }
      if (internal) {
        const handle = await main.webContents.executeJavaScript("(()=>{const r=document.querySelector('.nb-resizer').getBoundingClientRect();return {x:Math.round(r.x+3),y:200}})()");
        main.webContents.sendInputEvent({ type: 'mouseMove', ...handle });
        main.webContents.sendInputEvent({ type: 'mouseDown', button: 'left', clickCount: 1, ...handle });
        await until(() => manager.stacking.raised && manager.overlayCapturesInput, 'internal divider raises the real main overlay');
        await sample(`${theme}-internal-held`);
        if (full && process.env.GREEN_CSS_AB !== '1') {
          await main.webContents.executeJavaScript('window.surfaceDiag.repaint()');
          await sleep(80);
          await sample(`${theme}-main-repaint`);
        }
        if (process.env.GREEN_CSS_AB === '1' && theme === 'dark') {
          const repair = await main.webContents.executeJavaScript(fs.readFileSync(path.join(__dirname, 'repair-rules.js'), 'utf8'));
          fs.writeFileSync(path.join(output, 'css-ab.json'), JSON.stringify(repair, null, 2));
          console.log('CSS_AB ' + JSON.stringify(repair));
          await sleep(120);
          await sample('dark-internal-held-corrected');
          await main.webContents.executeJavaScript('window.surfaceDiag.repaint()');
          await sleep(80);
          await sample('dark-main-repaint-corrected');
        }
        let tick = 0;
        const timer = setInterval(() => main.webContents.sendInputEvent({ type: 'mouseMove', x: Math.round(handle.x + Math.sin(tick++ / 12) * 150), y: handle.y, modifiers: ['leftButtonDown'] }), 16);
        try { await sleep(5000); await sample(`${theme}-internal-moving`); }
        finally {
          clearInterval(timer);
          main.webContents.sendInputEvent({ type: 'mouseUp', button: 'left', clickCount: 1, ...handle });
          // Explicitly deliver cancellation to the fixture handle if native synthetic mouse-up
          // did not release capture. This is test cleanup, not a production behavior change.
          await main.webContents.executeJavaScript("document.querySelector('.nb-resizer').dispatchEvent(new PointerEvent('pointercancel',{bubbles:true}))");
          main.webContents.sendInputEvent({ type: 'mouseMove', x: 100, y: 200 });
        }
        await until(async () => !(await main.webContents.executeJavaScript('window.surfaceDiag.inspect().resizing')), 'internal divider releases resize state');
        await sleep(80);
        await sample(`${theme}-internal-released`);
      } else if (continuous) {
        let tick = 0;
        const timer = setInterval(() => {
          const wave = Math.sin(tick++ / 12);
          win.setContentSize(Math.round(1200 + wave * 160), Math.round(780 + wave * 70));
        }, 16);
        try { await sleep(5000); } finally { clearInterval(timer); }
        await sample(`${theme}-continuous-resize-end`);
      } else for (let i = 0; i < 8; i++) {
        win.setContentSize(1100 + i * 35, 720 + i * 12);
        await sleep(35);
        await sample(`${theme}-resize-${i}`);
      }
      await main.webContents.executeJavaScript("window.astrionBrowser.command({lease:window.surfaceDiag.inspect().lease,action:'new'})");
      await sleep(70);
    }
    let recording;
    if (continuous) {
      recording = await main.webContents.executeJavaScript("(()=>{const r=window.surfaceRecording;r.stopped=true;r.stream.getTracks().forEach(t=>t.stop());return {frames:r.frames,greenFrames:r.greenFrames}})()");
      for (let i = 0; i < recording.greenFrames.length; i++) {
        const frame = recording.greenFrames[i];
        fs.writeFileSync(path.join(output, `green-frame-${i}.png`), Buffer.from(frame.png.split(',')[1], 'base64'));
        delete frame.png;
      }
      fs.writeFileSync(path.join(output, 'recording.json'), JSON.stringify(recording, null, 2));
      console.log('RECORDING ' + JSON.stringify({ frames: recording.frames.length, greenFrames: recording.greenFrames.length, maxGreen: Math.max(0, ...recording.frames.map(f => f.green)) }));
    }
    fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify({ completed: true, sourceRoot, backendRoot, screenshotAllowed, samples, events, recordingFrames: recording?.frames.length }, null, 2));
    console.log(JSON.stringify({ completed: true, screenshotAllowed, output, samples: samples.length }));
  } catch (error) {
    const renderer = main && !main.webContents.isDestroyed() ? await main.webContents.executeJavaScript('window.surfaceDiag?.inspect()').catch(() => null) : null;
    fs.writeFileSync(path.join(output, 'failure.json'), JSON.stringify({ error: error.stack, samples, events, renderer }, null, 2));
    console.error(error.stack); if (renderer) console.error(JSON.stringify(renderer)); process.exitCode = 1;
  } finally {
    clearTimeout(timeout);
    View.prototype.setBackgroundColor = original;
    win?.destroy(); server?.closeAllConnections(); server?.close();
    backend?.kill('SIGTERM');
    app.exit(process.exitCode || 0);
  }
});
