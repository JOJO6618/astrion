// Functional native integration checks; no screenshots or visual acceptance.
const { app, BaseWindow, WebContentsView } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const root = path.resolve(__dirname, '../..');
const output = path.join(root, '_experiments/browser-native-integration', String(Date.now()));
fs.mkdirSync(output, { recursive: true });
app.setPath('userData', path.join(output, 'electron'));
const downloads = path.join(output, 'downloads'); fs.mkdirSync(downloads); app.setPath('downloads', downloads);
const checks = [];
let user = 'fixture-user-a', server, window, manager;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check, label, timeout = 10000) { const end = Date.now() + timeout; while (Date.now() < end) { if (await check()) return; await delay(25); } throw new Error(`Timeout: ${label}`); }
const check = (name, condition) => { assert.ok(condition, name); checks.push(name); };
app.whenReady().then(async () => {
  const deadline = setTimeout(() => { console.error('Native regression exceeded deadline'); app.exit(1); }, 90000);
  try {
    server = http.createServer((request, response) => {
      if (request.url === '/api/session-status') { response.setHeader('Content-Type', 'application/json'); response.end(JSON.stringify({ success: true, session: { logged_in: true, host_mode: true, username: user } })); return; }
      if (request.url === '/download') { response.setHeader('Content-Type', 'application/octet-stream'); response.setHeader('Content-Disposition', 'attachment; filename="fixture.txt"'); response.end('native-download-fixture'); return; }
      response.setHeader('Content-Type', 'text/html; charset=utf-8');
      if (request.url.startsWith('/new') || request.url.startsWith('/conv_')) { response.end('<!doctype html><title>Trusted shell fixture</title><body>Native integration fixture</body>'); return; }
      response.end(`<!doctype html><title>${request.url === '/page-two' ? 'Page Two' : 'Browser Fixture'}</title><body><p>Grid layout. Grid layout.</p><input id="field"><a id="blank" target="_blank" href="/popup">Popup</a><a id="download" href="/download">Download</a></body>`);
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = `http://127.0.0.1:${server.address().port}`;
    const { NativeBrowser, setBrowserDataRoot } = await import(pathToFileURL(path.join(root, 'desktop-electron/src/browser/index.js')));
    setBrowserDataRoot(path.join(output, 'data'));
    window = new BaseWindow({ width: 1100, height: 750, show: false });
    const main = new WebContentsView({ webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, preload: path.join(root, 'desktop-electron/src/preload-main.cjs') } });
    window.contentView.addChildView(main); main.setBounds({ x: 0, y: 46, width: 1100, height: 704 });
    manager = new NativeBrowser(window, main, origin);
    await main.webContents.loadURL(`${origin}/new`);
    window.showInactive();
    const bridge = expression => main.webContents.executeJavaScript(expression);
    const connect = scope => bridge(`window.astrionBrowser.connect(${JSON.stringify(scope)})`);
    const scopeA = { mode: 'agent', workspace: 'workspace-a', conversation: 'desktop-tab-a' };
    let snapshot = await connect(scopeA), lease = snapshot.lease;
    const command = async (action, value, id, useLease = lease) => { const result = await bridge(`window.astrionBrowser.command(${JSON.stringify({lease:useLease,action,value,id})})`); return result; };
    const layout = async visible => { await bridge(`window.astrionBrowser.layout(${JSON.stringify({ lease, sequence: ++layout.sequence, visible, rect: { x: 500, y: 92, width: 600, height: 600 } })})`); await delay(40); };
    layout.sequence = 0;
    check('trusted IPC connects with backend-derived user identity', snapshot.tabs.length === 1);
    await command('navigate', `${origin}/page-one`);
    await until(() => manager.active().view.webContents.getTitle() === 'Browser Fixture', 'first native page');
    const page = manager.active().view.webContents;
    await layout(true);
    check('real WebContentsView is visible at the right-side bounds', manager.active().view.getVisible() && manager.active().view.getBounds().x === 500 && manager.active().view.getBounds().y === 138);
    check('webpage has no Node or trusted preload', await page.executeJavaScript("typeof require === 'undefined' && typeof window.astrionBrowser === 'undefined'"));
    check('native webpage is sandboxed and context-isolated', page.getLastWebPreferences().sandbox && page.getLastWebPreferences().contextIsolation && !page.getLastWebPreferences().nodeIntegration);
    await page.executeJavaScript("document.cookie='shared_fixture=yes; path=/'; localStorage.setItem('shared-fixture','yes')");
    await command('navigate', `${origin}/page-two`); await until(() => page.getTitle() === 'Page Two', 'second page');
    await command('back'); await until(() => page.getURL().endsWith('/page-one'), 'back');
    await command('forward'); await until(() => page.getURL().endsWith('/page-two'), 'forward');
    check('back and forward operate on actual Chromium history', page.getURL().endsWith('/page-two'));
    await until(async () => !page.isLoading() && await page.executeJavaScript("document.body.textContent.includes('Grid layout')"), 'rendered text before find');
    page.on('found-in-page', (_event, result) => fs.appendFileSync(path.join(output, 'find-events.jsonl'), JSON.stringify(result) + '\n'));
    await command('find', { text: 'Grid', next: false });
    await until(() => manager.active().find?.matches === 2, 'native find');
    check('find-in-page uses real webpage matches', manager.active().find.matches === 2);
    await command('zoom', 120); check('webpage zoom changes independently', Math.round(page.getZoomFactor() * 100) === 120);
    await layout(false); check('DOM overlay hides native page', !manager.active().view.getVisible());
    await layout(true); check('native page resumes with the same contents', manager.active().view.getVisible() && page.getURL().endsWith('/page-two'));
    main.webContents.setZoomFactor(1.25); await layout(true);
    check('native bounds account for main-renderer zoom', manager.active().view.getBounds().x === 625);
    main.webContents.setZoomFactor(1); await layout(true);
    const beforePopup = manager.tabs.size;
    await page.executeJavaScript("document.getElementById('blank').click()");
    await until(() => manager.tabs.size === beforePopup + 1 && manager.active().view.webContents.getURL().endsWith('/popup'), 'popup');
    check('target blank becomes a native tab, not an extra window', BaseWindow.getAllWindows().length === 1 && manager.tabs.size === beforePopup + 1);
    const popup = manager.active().view.webContents;
    check('default target blank opener remains null', await popup.executeJavaScript('window.opener === null'));
    await popup.executeJavaScript("document.getElementById('download').click()");
    await until(() => manager.profile.data.downloads.some(row => row.state === 'completed'), 'download');
    await popup.executeJavaScript("document.getElementById('download').click()");
    await until(() => manager.profile.data.downloads.filter(row => row.state === 'completed').length === 2, 'duplicate download');
    const rows = manager.profile.data.downloads;
    check('downloads use unique names and retain exact contents', rows[0].path !== rows[1].path && rows.every(row => fs.readFileSync(row.path, 'utf8') === 'native-download-fixture'));
    await popup.executeJavaScript("window.permissionResult='pending'; navigator.geolocation.getCurrentPosition(()=>window.permissionResult='allowed',()=>window.permissionResult='denied');");
    await until(() => manager.profileFeatures(manager.profile).pending.size > 0, 'permission request');
    const permission = [...manager.profileFeatures(manager.profile).pending.values()][0];
    await command('permission', false, permission.id); await until(async () => await popup.executeJavaScript('window.permissionResult') === 'denied', 'denied permission');
    check('custom permission rejection reaches the real website', await popup.executeJavaScript('window.permissionResult') === 'denied');
    const ownerA = manager.owner, oldLease = lease;
    snapshot = await connect({mode:'chat',workspace:'',conversation:'desktop-tab-chat'}); lease = snapshot.lease; layout.sequence = 0;
    await command('navigate', `${origin}/shared`); await until(() => manager.active().view.webContents.getURL().endsWith('/shared'), 'shared profile');
    check('same user shares cookies and local storage across workspaces and Chat', await manager.active().view.webContents.executeJavaScript("document.cookie.includes('shared_fixture=yes') && localStorage.getItem('shared-fixture') === 'yes'"));
    check('dialog scopes retain separate native tabs', manager.owner !== ownerA && [...manager.tabs.values()].some(tab => tab.owner === ownerA));
    await assert.rejects(() => command('zoom', 100, undefined, oldLease)); checks.push('stale renderer lease cannot operate current tabs');
    user = 'fixture-user-b'; snapshot = await connect(scopeA); lease = snapshot.lease;
    await command('navigate', `${origin}/other-user`); await until(() => manager.active().view.webContents.getURL().endsWith('/other-user'), 'other user');
    check('different authenticated users have separate website data', await manager.active().view.webContents.executeJavaScript("!document.cookie.includes('shared_fixture=yes') && !localStorage.getItem('shared-fixture')"));
    user = 'fixture-user-a'; snapshot = await connect(scopeA); lease = snapshot.lease;
    await bridge("history.pushState({},'', '/conv_native_fixture')"); await command('zoom', 100);
    check('new-to-conversation same-document navigation keeps browser ownership', manager.lease === lease);
    const foreign = new WebContentsView({ webPreferences: { sandbox:true, contextIsolation:true, nodeIntegration:false, preload:path.join(root, 'desktop-electron/src/preload-main.cjs') } });
    window.contentView.addChildView(foreign); await foreign.webContents.loadURL(`${origin}/new`);
    await assert.rejects(() => foreign.webContents.executeJavaScript(`window.astrionBrowser.connect(${JSON.stringify(scopeA)})`)); foreign.webContents.close();
    checks.push('same-origin foreign webContents cannot invoke the trusted bridge');
    const savedProfile = manager.profile, count = manager.snapshot().tabs.length;
    await savedProfile.session.cookies.set({ url: origin, name: 'persistent-fixture', value: 'saved', expirationDate: Math.floor(Date.now() / 1000) + 86400 });
    await savedProfile.close();
    check('persistent cookie storage flushes to the profile directory', savedProfile.session.isPersistent() && (fs.existsSync(path.join(savedProfile.session.storagePath, 'Cookies')) || fs.existsSync(path.join(savedProfile.session.storagePath, 'Network', 'Cookies'))));
    manager.dispose(); manager = new NativeBrowser(window, main, origin);
    snapshot = await connect(scopeA); lease = snapshot.lease;
    check('browser tabs and metadata restore from local persistent profile', snapshot.tabs.length === count && snapshot.history.length > 0);
    await command('clear', {history:true,cookies:true,cache:true});
    check('clear browsing data clears only the selected profile history', manager.profile.data.history.length === 0);
    const old = lease; await main.webContents.reload(); await delay(100);
    await assert.rejects(() => command('zoom', 100, undefined, old)); checks.push('full renderer reload invalidates previous lease');
    fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify({passed:true, checks, output}, null, 2));
    console.log(JSON.stringify({passed:true, count:checks.length, checks, output}, null, 2));
  } catch (error) {
    const active = manager?.active();
    const diagnostic = active ? { snapshot: manager.snapshot(), visible: active.view.getVisible(), bounds: active.view.getBounds(), loading: active.view.webContents.isLoading(), text: await active.view.webContents.executeJavaScript('document.body.innerText').catch(() => '') } : {};
    fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify({passed:false, checks, error:error.stack, diagnostic, output}, null, 2));
    console.error(error.stack); console.error(JSON.stringify(diagnostic)); process.exitCode = 1;
  } finally {
    clearTimeout(deadline); manager?.dispose();
    if (window && !window.isDestroyed()) window.destroy();
    if (server) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
    app.exit(process.exitCode || 0);
  }
});
