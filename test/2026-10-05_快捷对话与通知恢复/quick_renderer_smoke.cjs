const { app, BrowserWindow, ipcMain, protocol } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const repo = path.resolve(__dirname, '../..');
const runtime = path.join(__dirname, 'runtime');
fs.mkdirSync(runtime, { recursive: true });
app.setPath('userData', runtime);
protocol.registerSchemesAsPrivileged([{ scheme: 'astrion-quick', privileges: {
  standard: true, secure: true, supportFetchAPI: true, corsEnabled: true
} }]);
const timeout = setTimeout(() => { console.error('Quick renderer smoke timed out'); app.exit(1); }, 15000);

app.whenReady().then(async () => {
  const { startQuickAssets } = await import(pathToFileURL(path.join(repo, 'desktop-electron/src/quick/assets.js')));
  const assets = await startQuickAssets();
  let ready;
  const rendererReady = new Promise(resolve => { ready = resolve; });
  ipcMain.on('quick:ready', ready);
  ipcMain.handle('quick:info', () => ({ enabled: true, modifier: 'option', workspace: '', model: '', debug: false }));
  ipcMain.handle('quick:request', (_event, request) => {
    if (request.route === '/api/host/workspaces') return { success: true, data: { workspaces: [] } };
    if (request.route === '/api/v1/models') return { items: [] };
    if (request.route === '/api/personalization') return { personalization: {} };
    return { success: true, data: {}, conversations: [] };
  });
  const window = new BrowserWindow({ show: false, transparent: true, frame: false,
    webPreferences: { preload: path.join(repo, 'desktop-electron/src/quick/preload.cjs'),
      contextIsolation: true, sandbox: true, nodeIntegration: false, backgroundThrottling: false }
  });
  const errors = [];
  window.webContents.on('console-message', (event) => {
    if (event.level === 'error') errors.push(event.message);
  });
  window.webContents.on('preload-error', (_event, _file, error) => errors.push(String(error)));
  await window.loadURL(assets.url);
  await rendererReady;
  // Verify the IPC show event changes the actual mounted renderer, without
  // opening a visible window, touching a real backend, or requesting TCC access.
  window.webContents.send('quick:show');
  await new Promise(resolve => setTimeout(resolve, 400));
  const result = await window.webContents.executeJavaScript(`({
    mounted: !!document.querySelector('.composer'),
    open: !!document.querySelector('.quick-window.is-open'),
    authorizationButton: [...document.querySelectorAll('button')].some(button => button.textContent.includes('开启截图权限'))
  })`);
  assert.equal(result.mounted, true);
  assert.equal(result.open, true);
  assert.equal(result.authorizationButton, false);
  assert.deepEqual(errors, []);
  console.log('Quick renderer smoke passed: production assets mount, ready handshake, show IPC, no authorization button');
  clearTimeout(timeout);
  window.destroy();
  assets.close();
  app.quit();
}).catch(error => {
  console.error(error.stack || error);
  clearTimeout(timeout);
  app.exit(1);
});
