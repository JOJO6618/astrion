// P2 冒烟：真实 Electron 下 ViewManager + detached-window：拖出迁移（不重载）、关闭→放回→request-dock 顺序。
const { app, BaseWindow, WebContentsView } = require('electron');
const root = '/Users/jojo/Desktop/agents/正在修复中/agents-multi-view/desktop-electron/src/views/';
const sleep = ms => new Promise(r => setTimeout(r, ms));
app.on('window-all-closed', () => {});
app.whenReady().then(async () => {
  setTimeout(() => app.exit(2), 40000);
  app.setPath('userData', require('os').tmpdir() + '/astrion-p2-smoke');
  const { ViewManager } = await import(root + 'manager.js');
  const { createDetachedWindow } = await import(root + 'detached-window.js');
  const win = new BaseWindow({ width: 600, height: 400 });
  const chromeView = new WebContentsView(); win.contentView.addChildView(chromeView);
  const log = [];
  chromeView.webContents.loadURL('data:text/html,chrome');
  const mk = ({ key }) => { const v = new WebContentsView({ webPreferences: { sandbox: true } });
    v.webContents.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(`<script>window.n=0;setInterval(()=>n++,50)</script>${key}`)); v.webContents.on('did-start-loading', () => log.push('load:' + key)); return v; };
  const vm = new ViewManager({ origin: 'http://127.0.0.1:1', window: win, chromeView, createView: mk,
    createDetachedWindow: ({ key, point, focus }, cb) => createDetachedWindow({ key, point, focus, occupied: [], colors: { surface: '#ffffff', chrome: '#ffffff', text: '#000000' } }, cb),
    getCursor: () => ({ x: 200, y: 200 }), hooks: { onViewMoved: (e) => log.push('moved:' + e.key + ':' + e.placement) } });
  const conv = (key) => ({ key, kind: 'conv', conversation_kind: 'agent', conversation_id: 'conv_' + key, workspace_id: 'w', placement: 'main' });
  vm.handleSnapshot({ revision: 1, tabs: [conv('a'), conv('b')], active_key: 'b' }, null);
  await sleep(800);
  const view = vm.entries.get('b').view; const n0 = await view.webContents.executeJavaScript('n');
  const sent = []; const orig = chromeView.webContents.send.bind(chromeView.webContents);
  chromeView.webContents.send = (c, d) => { sent.push([c, vm.entries.get('b').placement]); orig(c, d); };
  vm.dragTab({ key: 'b', phase: 'drop', screenX: 300, screenY: 200 });
  await sleep(800);
  const n1 = await view.webContents.executeJavaScript('n');
  const detached = vm.entries.get('b').placement === 'detached' && vm.detachedWindows.size === 1;
  const w = vm.detachedWindows.get('b'); w.close(); await sleep(500);
  const n2 = await view.webContents.executeJavaScript('n');
  console.log(JSON.stringify({ n0, n1, n2, survived: n0 < n1 && n1 < n2, detached, sent, log, placementAfterClose: vm.entries.get('b').placement, active: vm.activeKey, bounds: vm.entries.get('b').view.getBounds() }, null, 1));
  vm.dispose(); win.destroy(); app.exit(0);
});
