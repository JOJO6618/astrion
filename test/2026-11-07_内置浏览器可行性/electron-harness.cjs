// Isolated feasibility host. Never imports Astrion app/backend/preloads.
const { app, BaseWindow, WebContentsView, session } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const output = process.env.BROWSER_LAB_OUTPUT;
process.on('uncaughtException', error => {
  fs.appendFileSync(path.join(output, 'harness-errors.log'), String(error.stack || error) + '\n');
  app.exit(1);
});
app.setPath('userData', path.join(output, 'electron-profile'));
const state = { views: {}, windows: [], base: process.env.BROWSER_LAB_ORIGIN };
global.browserLab = state;
const prefs = { sandbox: true, contextIsolation: true, nodeIntegration: false };
const closeViews = () => {
  for (const view of Object.values(state.views)) {
    if (!view.webContents.isDestroyed()) view.webContents.close();
  }
};
app.on('before-quit', closeViews);
app.whenReady().then(async () => {
  const win = new BaseWindow({ width: 1100, height: 760, show: false });
  state.window = win;
  state.windows.push(win);
  const trusted = new WebContentsView({ webPreferences: { ...prefs, partition: 'persist:lab-trusted' } });
  const browser = new WebContentsView({ webPreferences: { ...prefs, partition: 'persist:lab-browser-a' } });
  state.views.trusted = trusted;
  state.views.browser = browser;
  const layout = () => {
    const { width, height } = win.getContentBounds();
    trusted.setBounds({ x: 0, y: 0, width: Math.floor(width * 0.4), height });
    browser.setBounds({ x: Math.floor(width * 0.4), y: 46, width: width - Math.floor(width * 0.4), height: height - 46 });
  };
  win.contentView.addChildView(trusted);
  win.contentView.addChildView(browser);
  win.on('resize', layout);
  layout();
  browser.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  await Promise.all([
    trusted.webContents.loadURL(`${state.base}/trusted`),
    browser.webContents.loadURL(`${state.base}/fixture`),
  ]);
  state.ready = true;
  state.sessions = { trusted: trusted.webContents.session, browser: browser.webContents.session };
  state.createIsolatedView = async () => {
    const other = new WebContentsView({ webPreferences: { ...prefs, partition: 'persist:lab-browser-b' } });
    state.views.other = other;
    await other.webContents.loadURL(`${state.base}/fixture`);
    return other.webContents.id;
  };
  fs.writeFileSync(path.join(output, 'harness-ready.json'), JSON.stringify({
    versions: process.versions,
    viewIds: Object.fromEntries(Object.entries(state.views).map(([name, view]) => [name, view.webContents.id])),
    bounds: Object.fromEntries(Object.entries(state.views).map(([name, view]) => [name, view.getBounds()])),
    sameSession: trusted.webContents.session === browser.webContents.session,
    offscreen: browser.webContents.isOffscreen(),
    remoteDebuggingPort: app.commandLine.getSwitchValue('remote-debugging-port'),
  }, null, 2));
}).catch(error => { console.error(error); app.exit(1); });
