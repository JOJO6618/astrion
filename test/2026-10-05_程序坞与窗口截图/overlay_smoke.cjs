const { app, BrowserWindow } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const repo = path.resolve(__dirname, '../..');
const runtime = path.join(__dirname, 'runtime', 'electron');
fs.mkdirSync(runtime, {recursive:true});
app.setPath('userData', runtime);
const timer = setTimeout(() => {console.error('Overlay smoke timed out');app.exit(1);},15000);
app.whenReady().then(async () => {
  app.setActivationPolicy('regular');
  const window = new BrowserWindow({show:false,frame:false,transparent:true,width:1470,height:956,skipTaskbar:false,
    webPreferences:{preload:path.join(repo,'desktop-electron/src/quick/capture-preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true,backgroundThrottling:false}});
  const errors = [];
  window.webContents.on('console-message', event => {if(event.level==='error')errors.push(event.message);});
  window.webContents.on('preload-error', (_event,_file,error) => errors.push(String(error)));
  await window.loadFile(path.join(repo,'desktop-electron/src/quick/capture.html'));
  window.webContents.send('quick:windows', {
    display:{x:0,y:0,width:1470,height:956},exclude:[],label:'Send {app} screenshot',capturing:false,
    windows:[{id:1,app:'Editor A',candidate:true,x:20,y:70,width:650,height:300},{id:2,app:'Editor B',candidate:true,x:800,y:100,width:500,height:400}]
  });
  await new Promise(resolve=>setTimeout(resolve,300));
  const buttons = await window.webContents.executeJavaScript(`Array.from(document.querySelectorAll('.window-capture')).map(button=>({hidden:button.hidden,text:button.textContent,clip:button.style.clipPath}))`);
  assert.equal(buttons.length,2);
  assert.ok(buttons.every(button=>!button.hidden&&button.clip.startsWith('path(')));
  assert.equal(app.dock.isVisible(),true);
  assert.deepEqual(errors,[]);
  console.log('Overlay smoke passed: two visible window-capture buttons, valid clipping, regular Dock identity, no console errors');
  clearTimeout(timer);
  window.destroy();
  app.quit();
}).catch(error=>{console.error(error.stack||error);clearTimeout(timer);app.exit(1);});
