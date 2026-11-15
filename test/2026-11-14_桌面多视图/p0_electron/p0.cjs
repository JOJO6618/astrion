// P0 实验：隐藏 WebContentsView 的可见性/定时器节流；视图跨 BaseWindow 迁移后的状态保持。
const { app, BaseWindow, WebContentsView } = require('electron');
const html = 'data:text/html;charset=utf-8,' + encodeURIComponent(`<script>
window.n=0;setInterval(()=>window.n++,50);
window.vis=[];document.addEventListener('visibilitychange',()=>vis.push(document.visibilityState));
window.ticks=0;window.t0=performance.now();setInterval(()=>window.ticks++,100);
</script>`);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const ev = (v, code) => v.webContents.executeJavaScript(code);
const mk = bp => new WebContentsView({ webPreferences: { sandbox: true, contextIsolation: true, ...bp } });
const win = () => new BaseWindow({ width: 300, height: 200, show: true });
async function hidden(bp, ms) {
  const w = win(), v = mk(bp); w.contentView.addChildView(v); v.setBounds({ x: 0, y: 0, width: 300, height: 200 });
  await v.webContents.loadURL(html); await sleep(500);
  const before = await ev(v, 'document.visibilityState');
  await ev(v, 'window.ticks=0;window.t0=performance.now();0');
  v.setVisible(false); await sleep(ms);
  const r = await ev(v, '({vs:document.visibilityState,hidden:document.hidden,vis:window.vis,ticks:ticks,elapsed:performance.now()-t0})');
  v.setVisible(true); await sleep(300);
  const after = await ev(v, 'document.visibilityState');
  v.webContents.close(); w.destroy(); await sleep(500);
  return { before, ...r, rateHz: +(r.ticks / (r.elapsed / 1000)).toFixed(2), expectedHz: 10, after };
}
async function move() {
  const A = win(), B = win(), v = mk({}); A.contentView.addChildView(v); v.setBounds({ x: 0, y: 0, width: 300, height: 200 });
  await v.webContents.loadURL(html); await sleep(700);
  const n1 = await ev(v, 'window.n'); let reloads = 0; v.webContents.on('did-start-loading', () => reloads++);
  A.contentView.removeChildView(v); B.contentView.addChildView(v); v.setBounds({ x: 0, y: 0, width: 300, height: 200 });
  await sleep(700); const n2 = await ev(v, 'window.n');
  A.destroy(); await sleep(700); const n3 = await ev(v, 'window.n');
  const alive = !v.webContents.isDestroyed();
  const C = win(); B.contentView.removeChildView(v); C.contentView.addChildView(v); await sleep(300); const n4 = await ev(v, 'window.n');
  v.webContents.close(); B.destroy(); C.destroy(); await sleep(300);
  return { n1, n2, n3, n4, counterMonotonic: n1 < n2 && n2 < n3 && n3 < n4, reloads, aliveAfterOriginalClosed: alive };
}
app.on('window-all-closed', () => {}); // 阻止所有窗口关闭时默认退出
app.whenReady().then(async () => {
  const out = { electron: process.versions.electron, platform: process.platform };
  setTimeout(() => { console.log('TIMEOUT'); app.exit(2); }, 60000);
  try {
    out.hiddenDefault = await hidden({}, 15000);
    out.hiddenNoThrottle = await hidden({ backgroundThrottling: false }, 15000);
    out.moveAcrossWindows = await move();
  } catch (e) { out.error = String(e); }
  console.log(JSON.stringify(out, null, 1)); app.exit(0);
});
