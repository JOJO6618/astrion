import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { EventEmitter } from 'node:events';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { TabDriver } from '../../desktop-electron/src/browser/ai/driver.js';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const checks = {};
function check(name, value) { assert.ok(value, name); checks[name] = true; }
class View {
  constructor() {
    const wc = this.webContents = new EventEmitter();
    wc.url = '';
    wc.getURL = () => wc.url;
    wc.getTitle = () => '';
    wc.loadURL = async url => { wc.url = url; };
    wc.isDestroyed = () => false;
    wc.isFocused = () => false;
    wc.setWindowOpenHandler = callback => { wc.open = callback; };
    this.setBounds = this.setVisible = this.setBackgroundColor = () => {};
  }
}
globalThis.__fixtureView = View;
const filename = path.join(root, 'desktop-electron/src/browser/tabs.js');
const moduleURL = pathToFileURL(filename).href;
let code = fs.readFileSync(filename, 'utf8')
  .replace("import { WebContentsView } from 'electron';", 'const WebContentsView = globalThis.__fixtureView;')
  .replace(/from '(\.\/[^']+)'/g, (_, target) => `from '${new URL(target, moduleURL).href}'`)
  .replaceAll('import.meta.url', JSON.stringify(moduleURL));
const { BrowserTabs } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
const profile = { data: { owners: {}, history: [] }, save() {}, record() {}, searchEngine: () => 'google' };
const manager = {
  tabs: new Map(), selected: new Map(), owner: 'synthetic', profile, surfaceColor: 'white',
  window: { isDestroyed: () => false, contentView: { addChildView() {}, removeChildView() {} } },
  profileFeatures: () => ({ denyStale() {} }), layout() {}, publish() {}, message() {}
};
const registry = new BrowserTabs(manager);
const local = registry.create('file:///C:/example%20dir/local.html');
check('electronManualLoadUsesFilePolicy', local.view.webContents.getURL().startsWith('file:'));
let prevented = false;
local.view.webContents.emit('will-navigate', { preventDefault() { prevented = true; } }, 'file:///C:/example%20dir/second.html');
check('electronRelativeFileNavigationAllowed', !prevented);
check('electronLocalPopupAllowed', local.view.webContents.open({ url: 'file:///C:/example%20dir/second.html' }).action === 'allow');
const remote = registry.create('https://example.test/');
prevented = false;
remote.view.webContents.emit('will-navigate', { preventDefault() { prevented = true; } }, 'file:///C:/private.html');
check('electronRemoteFileNavigationDenied', prevented);
check('electronRemoteFilePopupDenied', remote.view.webContents.open({ url: 'file:///C:/private.html' }).action === 'deny');
prevented = false;
local.view.webContents.emit('will-redirect', { preventDefault() { prevented = true; } }, 'file:///C:/private.html');
check('electronHttpRedirectToFileDenied', prevented);
let ipcCount = 0;
const preload = fs.readFileSync(path.join(root, 'desktop-electron/src/browser/passwords/page-preload.cjs'), 'utf8');
vm.runInNewContext(`(function(){${preload}\n})()`, { require: () => ({ ipcRenderer: { send() { ipcCount++; }, on() { ipcCount++; } } }), process: { isMainFrame: true }, location: { protocol: 'file:' } });
check('filePageDoesNotInstallPasswordAdapter', ipcCount === 0);
const wc = { getURL: () => 'file:///C:/private.html', isDestroyed: () => false };
const driver = new TabDriver({ view: { webContents: wc } });
let reached = false;
await assert.rejects(driver.run(async () => { reached = true; }), /本地文件/);
check('aiDoesNotRunOnManualFilePage', !reached);
assert.throws(() => driver.cdp('Runtime.evaluate', { expression: 'document.body.innerText' }), /本地文件/);
check('aiCannotReadFileViaCdp', true);
const report = { checks, scope: 'Electron production modules with a native view test double; macOS runtime and visuals require an actual Mac.' };
fs.writeFileSync(path.join(root, '_experiments/windows-browser-20261109/file-browser-policy.json'), JSON.stringify(report, null, 2));
console.log(JSON.stringify(report, null, 2));
