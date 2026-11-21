const assert = require('node:assert/strict');
const { createServer } = require('node:http');
const { mkdtempSync, readFileSync } = require('node:fs');
const { tmpdir } = require('node:os');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { app, BaseWindow, ipcMain, WebContentsView } = require('electron');

const root = path.resolve(__dirname, '../..');
const shellDir = path.join(root, 'desktop-electron/src');
const dist = path.join(root, 'static/dist');
const manifest = JSON.parse(readFileSync(path.join(dist, '.vite/manifest.json')));
const entry = manifest['static/src/chrome.ts'];
const profile = mkdtempSync(path.join(tmpdir(), 'astrion-sidebar-regression-'));
app.setPath('userData', profile);
app.commandLine.appendSwitch('disable-gpu');

const conversation = (key, title) => ({
  kind: 'conv', key, title, workspace_id: 'work', conversation_id: `conv_${key}`,
  conversation_kind: 'agent', placement: 'main'
});
const first = conversation('first', 'First conversation');
const second = conversation('second', 'Second conversation');
const settings = { kind: 'page', key: 'settings', route: '/settings', page_type: 'settings', opener_key: first.key, placement: 'main' };
const state = { revision: 1, tabs: [first, settings, second], active_key: first.key, workspace_id: 'work', renderers: {} };
const history = [first, second].map((tab) => ({ id: tab.conversation_id, title: tab.title, updated_at: '2026-11-21T00:00:00Z' }));
const routes = new Map([
  ['/api/host/workspaces', { workspaces: [{ workspace_id: 'work', label: 'Work', path: '/work', running_task_count: 0 }], default_workspace_id: 'work' }],
  ['/api/tasks', []],
  ['/api/personalization', { group_sidebar_by_workspace: false }],
  ['/api/session-status', { session: { username: 'sidebar-test' } }],
  ['/api/csrf-token', { token: 'sidebar-test' }]
]);
const content = `<!doctype html><textarea aria-label="content input"></textarea><script>
  astrionShell.onBrowserPanelOpen(open => { window.browserOpen = open; });
  astrionShell.browserPanelReady();
</script>`;

function json(response, data) {
  response.setHeader('Content-Type', 'application/json');
  response.end(JSON.stringify(data));
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url, 'http://127.0.0.1');
  if (url.pathname === '/chrome') {
    response.setHeader('Content-Type', 'text/html; charset=utf-8');
    const css = entry.css.map((file) => `<link rel="stylesheet" href="/static/dist/${file}">`).join('');
    response.end(`<!doctype html>${css}<script src="/static/security.js"></script><div id="chrome-app"></div><script type="module" src="/static/dist/${entry.file}"></script>`);
  } else if (url.pathname.startsWith('/static/')) {
    try {
      const filename = path.join(root, url.pathname);
      if (filename.endsWith('.js')) response.setHeader('Content-Type', 'text/javascript');
      if (filename.endsWith('.css')) response.setHeader('Content-Type', 'text/css');
      response.end(readFileSync(filename));
    } catch {
      response.writeHead(404).end();
    }
  } else if (url.pathname === '/api/conversation-tabs') {
    json(response, { success: true, ...state });
  } else if (url.pathname === '/api/conversation-tabs/commands') {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const { action, payload } = JSON.parse(Buffer.concat(chunks));
    if (action === 'activate') state.active_key = payload.key;
    if (action === 'open') state.active_key = state.tabs.find((tab) => tab.conversation_id === payload.conversation_id).key;
    state.revision++;
    json(response, { success: true, state, result: { key: state.active_key } });
  } else if (url.pathname === '/api/conversations') {
    json(response, { success: true, data: { conversations: history, has_more: false } });
  } else if (url.pathname === '/api/conversations/search') {
    const query = url.searchParams.get('q') || '';
    json(response, { success: true, data: { results: history.filter((row) => row.title.includes(query)) } });
  } else if (routes.has(url.pathname)) {
    json(response, { success: true, data: routes.get(url.pathname), ...routes.get(url.pathname) });
  } else if (url.pathname.startsWith('/api/')) {
    json(response, { success: true, data: {} });
  } else {
    response.setHeader('Content-Type', 'text/html; charset=utf-8');
    if (url.searchParams.get('tab') === second.key) setTimeout(() => response.end(content), 1400);
    else response.end(content);
  }
});

async function until(check, description) {
  const deadline = Date.now() + 12000;
  while (Date.now() < deadline) {
    const result = await check();
    if (result) return result;
    await new Promise((resolve) => setTimeout(resolve, 40));
  }
  throw new Error(description);
}

app.whenReady().then(async () => {
  const watchdog = setTimeout(() => app.exit(2), 60000);
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const { ViewManager } = await import(pathToFileURL(path.join(shellDir, 'views/manager.js')));
  const window = new BaseWindow({ width: 1100, height: 760 });
  const makeView = (preload) => new WebContentsView({ webPreferences: { preload: path.join(shellDir, preload), sandbox: true } });
  const chrome = makeView('preload-chrome.cjs');
  chrome.setBackgroundColor('#00000000');
  window.contentView.addChildView(chrome);
  const manager = new ViewManager({ origin, window, chromeView: chrome, maxViews: 1, createView: ({ url }) => {
    const view = makeView('preload-view.cjs');
    void view.webContents.loadURL(url).catch(() => {});
    return view;
  } });
  ipcMain.on('shell:theme-get', (event) => { event.returnValue = 'classic'; });
  ipcMain.on('shell:tabs-forward', (event, snapshot) => manager.handleSnapshot(snapshot, event.sender));
  ipcMain.on('shell:show', (_event, key) => manager.show(key));
  ipcMain.on('shell:sidebar-width', (_event, width) => manager.setSidebarWidth(Math.round(width)));
  ipcMain.on('shell:chrome-overlay', (_event, raised) => manager.setChromeOverlay(raised));
  ipcMain.handle('shell:browser-panel-states', () => Object.fromEntries(manager.browserPanels));
  ipcMain.on('shell:browser-panel-toggle', (_event, key) => manager.toggleBrowserPanel(key));
  ipcMain.on('shell:browser-panel-ready', (event) => manager.browserPanelReady(event.sender));
  ipcMain.on('shell:browser-panel-report', (event, open) => manager.reportBrowserPanel(event.sender, open));
  const evaluate = (expression) => chrome.webContents.executeJavaScript(expression);
  const click = (selector) => evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
  const sidebarText = () => evaluate('document.querySelector(".conversation-sidebar")?.textContent || ""');
  const tab = (key) => `[data-key="${key}"]`;
  const browser = '.sidebar-nav-row[aria-pressed]';
  try {
    manager.layout();
    await chrome.webContents.loadURL(`${origin}/chrome`);
    await until(() => manager.getActiveEntry()?.key === first.key && manager.sidebarWidth === 44, 'Initial conversation and collapsed sidebar');
    await click('.conversation-sidebar-toggle');
    await until(() => manager.sidebarWidth === 306, 'Expanded sidebar bounds');
    await until(async () => (await sidebarText()).includes(first.title), 'Conversation list in chrome');
    assert.equal(manager.getActiveView().getBounds().x, 306);
    await evaluate('document.querySelector(".conversation-sidebar").dataset.testIdentity = "persistent"');
    await click(browser);
    await until(() => manager.browserPanels.get(first.key), 'Browser open state on the first conversation');

    await click(tab(settings.key));
    await until(() => manager.getActiveEntry()?.key === settings.key, 'Settings activation');
    assert.equal(manager.getActiveView().getBounds().x, 306);
    await until(() => evaluate(`document.querySelector('${browser}').disabled`), 'Browser shortcut disables on settings');
    assert.equal(await evaluate('document.querySelector(".conversation-sidebar").dataset.testIdentity'), 'persistent');

    first.title = 'Updated first conversation';
    history[0].title = first.title;
    state.revision++;
    await until(async () => (await sidebarText()).includes(first.title), 'Title updates while settings is active');

    const scheduled = { id: 'conv_scheduled', title: 'Scheduled conversation', updated_at: '2026-11-21T00:00:00Z' };
    history.push(scheduled);
    routes.set('/api/tasks', [{ task_id: 'scheduled', workspace_id: 'work', conversation_id: scheduled.id, conversation_title: scheduled.title, status: 'running' }]);
    await until(async () => (await sidebarText()).includes(scheduled.title), 'Background task appears while settings is active');

    await evaluate(`(() => {
      const input = document.querySelector('.search-input');
      input.value = 'Second';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    })()`);
    await until(async () => (await sidebarText()).includes(second.title) && !(await sidebarText()).includes(first.title), 'Search from the settings tab');

    await evaluate(`Array.from(document.querySelectorAll('.conversation-item'))
      .find(row => row.textContent.includes(${JSON.stringify(second.title)})).click()`);
    await until(() => manager.getActiveEntry()?.key === second.key, 'Cold conversation opened from settings');
    assert.equal(manager.getActiveView().webContents.isLoadingMainFrame(), true);
    assert.equal(await evaluate('document.querySelector(".conversation-sidebar").dataset.testIdentity'), 'persistent');

    await click('.workspace-entry-btn');
    await until(() => manager.chromeOverlay, 'Workspace switcher raises chrome');
    const simulatedBrowser = new WebContentsView();
    window.contentView.addChildView(simulatedBrowser);
    manager.restoreChromeOverlay();
    assert.equal(window.contentView.children.at(-1), chrome);
    await evaluate('document.body.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true })); document.body.click()');
    await until(() => !manager.chromeOverlay, 'Workspace switcher closes');
    assert.equal(window.contentView.children[0], chrome);
    window.contentView.removeChildView(simulatedBrowser);
    simulatedBrowser.webContents.close();

    await click(tab(first.key));
    await until(() => manager.getActiveEntry()?.key === first.key, 'First conversation reopened');
    await until(() => evaluate(`document.querySelector('${browser}').getAttribute('aria-pressed') === 'true'`), 'Browser button shows the first conversation state');
    await until(async () => {
      const view = manager.getActiveView();
      return !view.webContents.isLoadingMainFrame() && await view.webContents.executeJavaScript('window.browserOpen === true');
    }, 'Browser panel state restored in the reloaded content renderer');

    const loginView = manager.beginLogin('/login?logged_out=1');
    assert.equal(loginView.getBounds().x, 0);
    assert.equal(manager.realEntries().length, 0);
    assert.equal(manager.handleSnapshot(state), false);
    assert.equal(manager.browserPanels.size, 0);
    manager.waitingForLogin = false;
    assert.equal(manager.handleSnapshot({ ...state, revision: 1 }), true);
    assert.equal(manager.getActiveView().getBounds().x, 306);
    console.log('PASS: persistent sidebar, live updates in settings, cold switch, native overlay ordering, browser state restoration, login view reset');
    app.exit(0);
  } finally {
    clearTimeout(watchdog);
    manager.dispose();
    window.destroy();
    server.close();
  }
}).catch((error) => {
  console.error(error);
  app.exit(1);
});
