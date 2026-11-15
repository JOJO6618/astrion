// 合成验证：新窗口 showInactive 出现在光标下后，chrome 视图的指针捕获是否仍持续收到 pointermove。
// 注意：sendInputEvent 绕过 OS 事件流，只能验证 Chromium 层；macOS mouseDragged 路由与 Windows SetCapture 需实机验收。
const { app, BaseWindow, WebContentsView } = require('electron');
const sleep = ms => new Promise(r => setTimeout(r, ms));
app.on('window-all-closed', () => {});
app.whenReady().then(async () => {
  setTimeout(() => app.exit(2), 30000);
  const w1 = new BaseWindow({ width: 600, height: 300, x: 100, y: 100 });
  const chrome = new WebContentsView({ webPreferences: { sandbox: true } });
  w1.contentView.addChildView(chrome); chrome.setBounds({ x: 0, y: 0, width: 600, height: 46 });
  await chrome.webContents.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(`<body style="margin:0"><div id=t style="width:200px;height:40px;background:#ccc"></div><script>
    window.log={move:0,lost:0,up:0,down:0,afterWindow:0,phase:0};
    const t=document.getElementById('t');
    t.addEventListener('pointerdown',e=>{log.down++;t.setPointerCapture(e.pointerId)});
    window.addEventListener('pointermove',e=>{log.move++; if(log.phase) log.afterWindow++});
    window.addEventListener('lostpointercapture',()=>log.lost++,true);
    window.addEventListener('pointerup',()=>log.up++);
  </script>`));
  chrome.webContents.focus(); await sleep(300);
  const ev = (type, x, y, extra = {}) => chrome.webContents.sendInputEvent({ type, x, y, button: 'left', clickCount: 1, modifiers: type === 'mouseUp' ? [] : ['leftButtonDown'], ...extra });
  ev('mouseDown', 50, 20); await sleep(50);
  for (let i = 1; i <= 5; i++) { ev('mouseMove', 50 + i * 10, 20 + i * 20); await sleep(20); }
  await chrome.webContents.executeJavaScript('log.phase=1');
  // 在光标（屏幕坐标约 (200,260)）下出现一个新窗口（不聚焦）
  const w2 = new BaseWindow({ width: 500, height: 300, x: 150, y: 240, show: false });
  w2.showInactive(); await sleep(300);
  for (let i = 6; i <= 12; i++) { ev('mouseMove', 50 + i * 10, 20 + i * 20); w2.setPosition(150 + i * 10, 240 + i * 20); await sleep(20); }
  ev('mouseUp', 170, 260); await sleep(100);
  console.log(JSON.stringify(await chrome.webContents.executeJavaScript('log')));
  app.exit(0);
});
