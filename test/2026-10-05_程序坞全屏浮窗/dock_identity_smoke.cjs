const { app, BrowserWindow } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { execFileSync } = require('node:child_process');
const assert = require('node:assert/strict');
const runtime = path.join(__dirname,'runtime');
fs.mkdirSync(runtime,{recursive:true});
app.setPath('userData',runtime);
const timer = setTimeout(()=>{console.error('Dock identity smoke timed out');app.exit(1);},15000);
function applicationType() {
  const list = execFileSync('/usr/bin/lsappinfo',['list'],{encoding:'utf8'});
  return list.match(new RegExp(`pid\\s*=\\s*${process.pid}\\s+type="([^"]+)"`))?.[1];
}
app.whenReady().then(async()=>{
  app.setActivationPolicy('regular');
  const { configureWorkspaceVisibility } = await import(pathToFileURL(path.resolve(__dirname,'../../desktop-electron/src/quick/workspace-visibility.js')));
  const windows = [];
  for (let index=0;index<2;index++) {
    const window = new BrowserWindow({show:false,frame:false,transparent:true,skipTaskbar:false,
      webPreferences:{contextIsolation:true,nodeIntegration:false,sandbox:true}});
    windows.push(window);
    configureWorkspaceVisibility(window);
    window.setAlwaysOnTop(true,'floating');
  }
  await new Promise(resolve=>setTimeout(resolve,700));
  assert.equal(app.dock.isVisible(),true);
  assert.ok(windows.every(window=>window.isVisibleOnAllWorkspaces()));
  const type = applicationType();
  assert.equal(type,'Foreground');
  console.log('Dock identity smoke passed: two full-screen-compatible floating windows retain Foreground identity and visible Dock');
  clearTimeout(timer);
  windows.forEach(window=>window.destroy());
  app.quit();
}).catch(error=>{console.error(error.stack||error);clearTimeout(timer);app.exit(1);});
