// Production NativeBrowser + preload; synthetic localhost pages and isolated data only.
const { app, BaseWindow, WebContentsView } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const root = path.resolve(__dirname, '../..');
const observe = process.argv.includes('--observe');
const output = path.join(root, '_experiments/browser-native-integration', `tab-lifetime-${Date.now()}`);
fs.mkdirSync(output, { recursive: true });
app.setPath('userData', path.join(output, 'electron'));
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const checks = [], observations = [];
let server, window, main, manager;
function check(label, value) { assert.ok(value, label); checks.push(label); }
async function until(test, label) {
  const deadline = Date.now() + 6000;
  while (Date.now() < deadline) { if (await test()) return; await delay(20); }
  throw new Error(`Timeout: ${label}`);
}
app.whenReady().then(async () => {
  const deadline = setTimeout(() => app.exit(1), 45000);
  try {
    server = http.createServer((request, response) => {
      if (request.url === '/api/session-status') {
        response.setHeader('Content-Type', 'application/json');
        response.end(JSON.stringify({ success: true, session: { logged_in: true, username: 'tab-lifetime-fixture' } }));
        return;
      }
      response.setHeader('Content-Type', 'text/html; charset=utf-8');
      response.end(`<!doctype html><title>${request.url}</title><p>Isolated document ${request.url}</p>
        <a id="blank" href="/b" target="_blank">Default new tab</a>
        <a id="opener" href="/b" target="_blank" rel="opener">Explicit opener</a>
        <a id="noopener" href="/b" target="_blank" rel="noopener">No opener</a>`);
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = `http://127.0.0.1:${server.address().port}`;
    const { NativeBrowser, setBrowserDataRoot } = await import(pathToFileURL(path.join(root, 'desktop-electron/src/browser/index.js')));
    setBrowserDataRoot(path.join(output, 'data'));
    window = new BaseWindow({ width: 1100, height: 750, show: false });
    main = new WebContentsView({ webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, preload: path.join(root, 'desktop-electron/src/preload-main.cjs') } });
    window.contentView.addChildView(main); main.setBounds({ x: 0, y: 46, width: 1100, height: 704 });
    manager = new NativeBrowser(window, main, origin);
    await main.webContents.loadURL(`${origin}/new`);
    const run = code => main.webContents.executeJavaScript(code);
    const snapshot = await run(`window.astrionBrowser.connect({mode:'agent',workspace:'fixture',conversation:'lifetime'})`);
    const command = (action, value, id) => run(`window.astrionBrowser.command(${JSON.stringify({ lease: snapshot.lease, action, value, id })})`);
    for (const mode of ['blank', 'opener', 'noopener', 'script']) {
      if (!manager.main.active()) await command('new');
      const a = manager.main.active(), awc = a.view.webContents;
      await command('navigate', `${origin}/a`, a.id);
      await until(() => awc.getTitle() === '/a' && !awc.isLoading(), 'A loaded');
      await awc.executeJavaScript('window.messages=[];addEventListener("message",event=>window.messages.push(event.data))');
      const open = mode === 'script' ? "window.open('/b','_blank');void 0" : `document.getElementById(${JSON.stringify(mode)}).click()`;
      await awc.executeJavaScript(open, true);
      await until(() => manager.tabs.size === 2 && manager.main.active() !== a && manager.main.active().view.webContents.getTitle() === '/b', 'B opened as a real tab');
      const b = manager.main.active(), bwc = b.view.webContents;
      const expectsOpener = mode === 'opener' || mode === 'script';
      check(`${mode}: normal opener/noopener semantics remain intact`, await bwc.executeJavaScript('window.opener !== null') === expectsOpener);
      if (expectsOpener) {
        await bwc.executeJavaScript("window.opener.postMessage('b-ready','*')");
        await until(() => awc.executeJavaScript("window.messages.includes('b-ready')"), 'B communicates with A');
        check(`${mode}: B can message its opener before A closes`, true);
      }
      await bwc.executeJavaScript("window.liveToken='B-survives';window.ticks=0;setInterval(()=>window.ticks++,20);window.open('/c','_blank');void 0", true);
      await until(() => manager.tabs.size === 3 && manager.main.active() !== b && manager.main.active().view.webContents.getTitle() === '/c', 'B opens C');
      const c = manager.main.active(), cwc = c.view.webContents;
      await cwc.executeJavaScript("window.liveToken='C-survives'");
      check(`${mode}: website child keeps sandbox and no trusted bridge`, await bwc.executeJavaScript("typeof require==='undefined'&&typeof window.astrionBrowser==='undefined'") && bwc.getLastWebPreferences().sandbox);
      // Close A through the exact trusted command used by the tab close button.
      await command('close', undefined, a.id);
      await until(() => awc.isDestroyed(), 'A closed');
      await delay(250);
      const bAlive = !bwc.isDestroyed() && manager.tabs.has(b.id);
      const cAlive = !cwc.isDestroyed() && manager.tabs.has(c.id);
      observations.push({ mode, bAlive, cAlive, remaining: manager.tabs.size });
      if (!observe) {
        check(`${mode}: closing A leaves both B and C alive`, bAlive && cAlive && manager.tabs.size === 2);
        const previousTicks = await bwc.executeJavaScript('window.ticks');
        await delay(70);
        check(`${mode}: B retains the same running document`, await bwc.executeJavaScript("window.liveToken==='B-survives'") && await bwc.executeJavaScript('window.ticks') > previousTicks);
        await command('select', undefined, b.id);
        check(`${mode}: surviving B can be selected normally`, manager.main.active() === b);
        await command('close', undefined, b.id);
        await until(() => bwc.isDestroyed(), 'B closed');
        await delay(100);
        check(`${mode}: closing B also leaves C alive`, !cwc.isDestroyed() && manager.tabs.has(c.id) && await cwc.executeJavaScript("window.liveToken==='C-survives'"));
        await command('navigate', `${origin}/c-next`, c.id);
        await until(() => cwc.getTitle() === '/c-next', 'surviving C navigates');
        check(`${mode}: C remains usable after both ancestor tabs close`, true);
      }
      for (const tab of [...manager.tabs.values()]) {
        if (!tab.view.webContents.isDestroyed()) await command('close', undefined, tab.id);
      }
      await until(() => manager.tabs.size === 0, 'fixture tabs cleaned');
    }
    const result = { passed: true, observe, checks, observations, output };
    fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
  } catch (error) {
    fs.writeFileSync(path.join(output, 'failure.json'), JSON.stringify({ error: error.stack, observations }, null, 2));
    console.error(error.stack); process.exitCode = 1;
  } finally {
    clearTimeout(deadline);
    manager?.dispose(); main?.webContents.close(); window?.destroy(); server?.close();
    app.exit(process.exitCode || 0);
  }
});
