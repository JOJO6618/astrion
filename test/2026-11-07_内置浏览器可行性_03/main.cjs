const { app, BaseWindow, WebContentsView, ipcMain } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { TabRegistry, secure, webUrl } = require('./tab-registry.cjs');
const { SessionFeatures } = require('./session-features.cjs');
const output = process.env.BROWSER_LAB_OUTPUT;
const origin = process.env.BROWSER_LAB_ORIGIN;
fs.mkdirSync(path.join(output, 'profile'), { recursive: true });
app.setPath('userData', path.join(output, 'profile'));
let window, shell, registry, features, userOverlay = false, closing = false;
const events = [];
function snapshot() {
  return { ...registry.state(), ...features.state(), events,
    nativeWindows: BaseWindow.getAllWindows().length, nativeChildren: window.contentView.children.length };
}
function changed(message) {
  if (!registry || !features || closing) return;
  features.denyInvalid();
  registry.obscured = userOverlay || features.pending.size > 0;
  registry.layout();
  if (message) { events.push({ time: new Date().toLocaleTimeString('zh-CN'), message }); if (events.length > 30) events.shift(); }
  if (shell && !shell.webContents.isDestroyed() && !shell.webContents.isLoadingMainFrame()) shell.webContents.send('browser-lab:state', snapshot());
}
function trusted(event) {
  if (event.sender !== shell.webContents || event.senderFrame !== shell.webContents.mainFrame ||
    event.senderFrame.url !== `${origin}/shell`) throw Error('untrusted_sender');
}
async function command(event, request) {
  trusted(event);
  const { action, id, url, conversation, allowed, open, rect } = request || {};
  const active = () => registry.active()?.view.webContents;
  switch (action) {
    case 'state': return snapshot();
    case 'geometry': registry.setRect(rect); break;
    case 'new': registry.create(url ? webUrl(url) : undefined); break;
    case 'select': registry.select(id); break;
    case 'close': registry.close(id); break;
    case 'navigate': if (active()) await active().loadURL(webUrl(url)); break;
    case 'back': if (active()?.navigationHistory.canGoBack()) active().navigationHistory.goBack(); break;
    case 'forward': if (active()?.navigationHistory.canGoForward()) active().navigationHistory.goForward(); break;
    case 'reload': active()?.reload(); break;
    case 'conversation': userOverlay = false; registry.switchConversation(conversation); break;
    case 'collapse': registry.collapsed = !registry.collapsed; registry.layout(); break;
    case 'downloads': userOverlay = open === true; break;
    case 'dragging': userOverlay = open === true; break;
    case 'cancel-download': features.cancelDownload(id); break;
    case 'permission': features.resolvePermission(id, allowed); break;
    default: throw Error('unsupported_command');
  }
  changed(); return snapshot();
}
process.on('uncaughtException', error => {
  fs.appendFileSync(path.join(output, 'host-errors.log'), String(error.stack || error) + '\n'); app.exit(1);
});
app.on('window-all-closed', () => app.quit());
app.whenReady().then(async () => {
  window = new BaseWindow({ width: 1280, height: 820, minWidth: 860, minHeight: 580,
    title: 'Astrion 原生浏览器 · 独立实验', show: true });
  shell = new WebContentsView({ webPreferences: { ...secure, partition: 'persist:lab-shell', preload: path.join(__dirname, 'preload.cjs') } });
  window.contentView.addChildView(shell);
  features = new SessionFeatures(output, () => registry, changed);
  registry = new TabRegistry(window, origin, changed, (ses, owner) => features.attach(ses, owner));
  const layoutShell = () => { const { width, height } = window.getContentBounds(); shell.setBounds({ x: 0, y: 0, width, height }); };
  window.on('resize', layoutShell); layoutShell();
  window.on('close', () => {
    closing = true; features.destroy(); registry.destroy(); shell.webContents.close();
    fs.writeFileSync(path.join(output, 'closed.json'), JSON.stringify({ closed: true, remainingTabs: registry.tabs.size }));
  });
  shell.webContents.on('will-navigate', (event, url) => { if (url !== `${origin}/shell`) event.preventDefault(); });
  shell.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  shell.webContents.session.setPermissionRequestHandler((_wc, _permission, done) => done(false));
  ipcMain.handle('browser-lab:command', command);
  registry.create();
  await shell.webContents.loadURL(`${origin}/shell`);
  global.browserPrototype = { window, shell, registry, features, snapshot,
    async target(id) {
      const tab = registry.tab(id); const wc = tab.view.webContents;
      wc.debugger.attach('1.3');
      try { return (await wc.debugger.sendCommand('Target.getTargetInfo')).targetInfo.targetId; }
      finally { wc.debugger.detach(); }
    } };
  changed('原生浏览器已就绪');
  fs.writeFileSync(path.join(output, 'ready.json'), JSON.stringify({ versions: process.versions, shellId: shell.webContents.id }));
}).catch(error => { fs.appendFileSync(path.join(output, 'host-errors.log'), String(error.stack || error)); app.exit(1); });
