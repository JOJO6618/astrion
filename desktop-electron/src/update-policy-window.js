import { app, BrowserWindow } from 'electron';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { checkUpdatePolicy, onUpdatePolicyChange } from './update-policy.js';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), 'update-policy-ui');
let gate = null;
let gateToken = '';
let transitioning = false;

export function focusUpdatePolicyWindow() {
  if (!gate || gate.isDestroyed()) return false;
  if (gate.isMinimized()) gate.restore();
  gate.show();
  gate.focus();
  return true;
}
export function updatePolicyTransitioning() { return transitioning; }
export function finishUpdatePolicyTransition() { transitioning = false; }
export function authorizeUpdatePolicyWindow(req) {
  const value = req.headers['x-astrion-update-gate'];
  return Boolean(gate && gateToken && typeof value === 'string' && /^[a-f0-9]{64}$/.test(value) && timingSafeEqual(Buffer.from(value), Buffer.from(gateToken)));
}

export function serveUpdatePolicyPage(req, res, pathname) {
  const resources = {
    '/update/required': ['index.html', 'text/html; charset=utf-8'],
    '/update/required/style.css': ['style.css', 'text/css; charset=utf-8'],
    '/update/required/app.js': ['app.js', 'text/javascript; charset=utf-8'],
  };
  if (req.method !== 'GET') return false;
  if (pathname === '/update/required/strings') {
    const locale = app.getLocale().toLowerCase().startsWith('zh') ? 'zh-CN' : 'en-US';
    const filename = app.isPackaged
      ? path.join(process.resourcesPath, 'update-policy-locales', locale + '.json')
      : path.resolve(root, '../../../static/src/locales', locale, 'updatePolicy.json');
    const body = fs.readFileSync(filename);
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
    res.end(body);
    return true;
  }
  const resource = resources[pathname];
  if (!resource) return false;
  const body = fs.readFileSync(path.join(root, resource[0]));
  res.writeHead(200, {
    'Content-Type': resource[1], 'Cache-Control': 'no-store',
    'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
  });
  res.end(body);
  return true;
}

export async function enforceStartupUpdatePolicy(bridgePort) {
  const policy = await checkUpdatePolicy({ force: true });
  if (!policy.required) return true;
  if (!bridgePort) throw new Error('required_update_bridge_unavailable');
  gateToken = randomBytes(32).toString('hex');
  const url = `http://127.0.0.1:${bridgePort}/update/required?token=${gateToken}`;
  gate = new BrowserWindow({
    width: 560, height: 440, minWidth: 460, minHeight: 380,
    maxWidth: 760, maxHeight: 680, title: 'Astrion', show: false,
    webPreferences: { contextIsolation: true, sandbox: true, nodeIntegration: false },
  });
  gate.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  gate.webContents.on('will-navigate', (event, target) => { if (target !== url) event.preventDefault(); });
  let released = false;
  const completed = new Promise(resolve => {
    const unsubscribe = onUpdatePolicyChange(next => {
      if (!next.required && gate && !gate.isDestroyed()) {
        released = true;
        transitioning = true;
        gate.close();
      }
    });
    gate.once('closed', () => {
      unsubscribe();
      gate = null;
      gateToken = '';
      resolve(released);
      if (!released) app.quit();
    });
  });
  try {
    await gate.loadURL(url);
    if (gate && !gate.isDestroyed()) gate.show();
  } catch (error) {
    if (gate && !gate.isDestroyed()) gate.close();
    throw error;
  }
  return completed;
}
