// Real Vue components + production preload/NativeBrowser; no visual automation or screenshots.
const { app, BaseWindow, WebContentsView } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const root = path.resolve(__dirname, '../..');
const output = path.join(root, '_experiments/browser-native-integration', `no-model-${Date.now()}`);
fs.mkdirSync(output, { recursive: true });
app.setPath('userData', path.join(output, 'electron'));
app.setPath('downloads', output);
const checks = [];
const calls = [];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let server, window, main, manager;
async function until(check, label) {
  const end = Date.now() + 10000;
  while (Date.now() < end) { if (await check()) return; await delay(25); }
  throw new Error(`Timeout: ${label}`);
}
function check(label, value) { assert.ok(value, label); checks.push(label); }
app.whenReady().then(async () => {
  const deadline = setTimeout(() => app.exit(1), 45000);
  try {
    const assets = path.join(root, '_experiments/browser-renderer-regression');
    server = http.createServer((request, response) => {
      calls.push(request.url);
      if (request.url === '/api/session-status') {
        response.setHeader('Content-Type', 'application/json');
        response.end(JSON.stringify({ success: true, session: { logged_in: true, username: 'no-model-fixture' } }));
      } else if (request.url === '/renderer.js') {
        response.setHeader('Content-Type', 'text/javascript'); response.end(fs.readFileSync(path.join(assets, 'renderer.js')));
      } else if (request.url.endsWith('.css') && !request.url.includes('..')) {
        response.setHeader('Content-Type', 'text/css'); response.end(fs.readFileSync(path.join(assets, path.basename(request.url))));
      } else if (require('../2026-11-08_浏览器原生菜单与叠层/check.cjs').route(request, response) || require('../2026-11-08_浏览器主题图标与浮层/check.cjs').route(request, response)) {
        // Synthetic local pages only.
      } else if (request.url === '/new') {
        response.setHeader('Content-Type', 'text/html; charset=utf-8');
        const styles = fs.readdirSync(assets).filter(file => file.endsWith('.css')).map(file => `<link rel="stylesheet" href="/${file}">`).join('');
        response.end(`<!doctype html><meta charset="utf-8">${styles}<div id="app"></div><script type="module" src="/renderer.js"></script>`);
      } else if (request.url === '/fixture-page') {
        response.setHeader('Content-Type', 'text/html'); response.end('<title>Manual browser without a model</title><p>Functional fixture</p>');
      } else { response.statusCode = 404; response.end('No model or chat API in this fixture'); }
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = `http://127.0.0.1:${server.address().port}`;
    const { NativeBrowser, setBrowserDataRoot } = await import(pathToFileURL(path.join(root, 'desktop-electron/src/browser/index.js')));
    setBrowserDataRoot(path.join(output, 'data'));
    window = new BaseWindow({ width: 1200, height: 800, show: false });
    main = new WebContentsView({ webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, preload: path.join(root, 'desktop-electron/src/preload-main.cjs') } });
    window.contentView.addChildView(main); main.setBounds({ x: 0, y: 46, width: 1200, height: 754 });
    manager = new NativeBrowser(window, main, origin);
    main.webContents.on('console-message', (_event, level, message) => {
      if (level >= 2) fs.appendFileSync(path.join(output, 'renderer-errors.log'), message + '\n');
    });
    const run = expression => main.webContents.executeJavaScript(expression);
    const inspect = () => run('window.browserRegression.inspect()');
    main.webContents.session.webRequest.onBeforeRequest({ urls: ['https://*/*'] }, (_details, callback) => callback({ redirectURL: `${origin}/site-icon.svg` }));
    await main.webContents.loadURL(`${origin}/new`);
    window.showInactive();
    await until(() => run('!!window.browserRegression'), 'Vue fixture mounted');
    await run("window.browserRegression.state('unattached')");
    let state = await inspect();
    check('browser chrome renders before desktop attachment instead of a blank reserved area', state.panel && state.tabbar && state.address && !state.lease);
    await run("window.browserRegression.state('model-error')");
    await until(async () => (await inspect()).lease, 'manual browser connects despite model setup error');
    state = await inspect();
    check('no-model error shows real tab strip, address bar and new-tab page', state.panel && state.tabbar && state.address && state.newtab && !state.error);
    check('explicit failed new target owns native tabs without desktop commit', manager.main.owner.includes('tab-no-model'));
    await run(`window.browserRegression.navigate(${JSON.stringify(`${origin}/fixture-page`)})`);
    await until(() => manager.main.active()?.view.webContents.getTitle() === 'Manual browser without a model', 'real address-form navigation');
    await until(() => manager.main.active()?.view.getVisible(), 'native view receives Vue surface layout');
    check('Vue address form opens the actual native website without any model', manager.main.active().view.getVisible());
    await until(() => !manager.main.active().view.webContents.isLoading(), 'page load events settled');
    await delay(100);
    await run('window.browserRegression.menu()');
    await until(() => manager.main.overlayRaised && window.contentView.children.at(-1) === main, 'Vue menu rises above live website');
    check('real Vue menu overlays a still-visible native website', (await inspect()).menu && manager.main.active().view.getVisible());
    await require('../2026-11-08_浏览器原生菜单与叠层/check.cjs').verify({ run, inspect, manager, window, main, origin, check, delay, until });
    if (process.argv.includes('--theme-icons')) await require('../2026-11-08_浏览器主题图标与浮层/check.cjs').verify({ run, inspect, manager, main, origin, check, delay, until });
    if (process.argv.includes('--polling-stability')) {
      await require('../2026-11-08_浏览器轮询稳定性/polling.cjs')({ run, inspect, manager, calls, check, delay, until, output });
    }
    await run("window.browserRegression.state('pending')");
    await until(async () => !(await inspect()).lease, 'pending navigation suspends browser ownership');
    state = await inspect();
    check('pending navigation preserves browser chrome but suspends native commands', state.panel && state.address && !manager.main.active().view.getVisible());
    await run("window.browserRegression.state('committed')");
    await until(async () => (await inspect()).lease, 'committed target connects');
    check('committed navigation switches to its own stable browser owner', manager.main.owner.includes('tab-committed'));
    await run("window.browserRegression.state('foreign')");
    await until(async () => !(await inspect()).lease, 'foreign renderer suspended');
    check('foreign renderer cannot borrow the failed new target', !(await inspect()).lease);
    await run("window.browserRegression.state('settings')");
    await until(async () => !(await inspect()).panel, 'independent page hides browser');
    check('independent application page removes browser panel', !(await inspect()).panel);
    state = await inspect();
    check('Vue component execution has no runtime errors', state.errors.length === 0);
    const guards = await run('window.browserRegression.guards()');
    for (const [name, passed] of Object.entries(guards)) check(`navigation scope guard: ${name}`, passed);
    check('manual browsing never requests model or chat APIs', calls.every(url => !/models|chat|new-conversation/.test(url)));
    fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify({ passed: true, checks }, null, 2));
    console.log(JSON.stringify({ passed: true, checks, output }, null, 2));
  } catch (error) {
    fs.writeFileSync(path.join(output, 'failure.json'), JSON.stringify({ error: error.stack }, null, 2));
    console.error(error.stack); process.exitCode = 1;
  } finally {
    clearTimeout(deadline);
    manager?.dispose(); main?.webContents.close(); window?.destroy(); server?.close();
    app.exit(process.exitCode || 0);
  }
});
