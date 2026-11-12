// Synthetic local pages only; exercise production NativeBrowser and its native views.
import { app, BaseWindow, WebContentsView } from 'electron';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'astrion-retention-'));
app.setPath('userData', path.join(temp, 'electron'));
app.whenReady().then(async () => {
const { NativeBrowser, setBrowserDataRoot } = await import('../../desktop-electron/src/browser/index.js');
let decision = { release: [], deleted: [], retained: [] };
let requested;
const server = http.createServer((request, response) => {
  if (request.url === '/api/browser-retention') {
    requested = JSON.parse(request.headers['x-astrion-browser-state']);
    response.setHeader('Content-Type', 'application/json');
    response.end(JSON.stringify({ success: true, username: 'host', ...decision }));
  } else response.end('<!doctype html><title>Retention fixture</title><button>Test</button>');
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const window = new BaseWindow({ show: false, width: 1000, height: 700 });
const main = new WebContentsView();
window.contentView.addChildView(main);
await main.webContents.loadURL(origin);
setBrowserDataRoot(path.join(temp, 'data'));
const browser = new NativeBrowser(window, main, origin);
browser.retention.dispose();
const profile = browser.ensureProfile('host');
const scope = JSON.stringify(['agent', 'ws', 'conv_a']);
browser.main.profile = profile; browser.main.owner = `${profile.key}:${scope}`;
let checks = 0;
const check = (name, predicate) => { assert.ok(predicate, name); checks++; console.log(`PASS ${name}`); };
try {
  const first = browser.registry.create('about:blank');
  await first.view.webContents.loadURL(origin);
  const second = browser.registry.create('about:blank');
  await second.view.webContents.loadURL(origin);
  const oldView = first.view;
  const short = browser.ai.shortId(browser.main.owner, first);
  first.lastActive = Date.now() - 31 * 60 * 1000;
  decision = { release: [first.id], deleted: [], retained: [scope] };
  await browser.retention.poll();
  check('native poll sends owner scope and selected state', requested.owners.some(row => row[2] === 'conv_a') && requested.tabs.some(row => row.id === second.id && row.selected));
  check('idle page releases its native view', first.suspended && first.view === null);
  check('suspension preserves URL, identity and tab order', browser.tabs.has(first.id) && browser.snapshot(browser.main).tabs[0].id === first.id && first.url === origin + '/');
  check('other selected native page stays alive', !second.view.webContents.isDestroyed());
  await browser.command(browser.main, { action: 'select', id: first.id });
  const resumed = browser.tabs.get(first.id);
  check('select recreates a distinct native view', !resumed.suspended && resumed.view !== oldView);
  check('short AI tab identity survives recreation', browser.ai.shortId(browser.main.owner, resumed) === short);
  const identity = { username: 'host', workspace_id: 'ws', conversation_id: 'conv_a' };
  const result = await browser.ai.call({ call_id: 'retention-check', op: 'snapshot', args: { tab_id: short }, identity, epoch: 0, any_site: true });
  check('AI can operate the recreated page', result.success && result.snapshot.text.includes('Test'));
  check('AI receives reload/state-loss notice', result.notes.some(note => note.includes('重新加载')));
  check('AI activity timestamp is updated', resumed.lastAi > 0);
  await browser.command(browser.main, { action: 'select', id: second.id });
  browser.retention.suspend(resumed);
  const again = await browser.ai.call({ call_id: 'retention-again', op: 'snapshot', args: { tab_id: short }, identity, epoch: 0, any_site: true });
  check('AI restores only its target without changing user selection', again.success && browser.selected.get(browser.main.owner) === second.id);
  check('restored AI snapshot resets the old baseline', again.snapshot.kind === 'full');
  browser.retention.suspend(browser.tabs.get(first.id));
  check('all-suspended snapshots are readable', browser.snapshot(browser.main).tabs.some(tab => tab.id === resumed.id));
  browser.retention.apply({ release: [], deleted: [scope], retained: [] }, profile.key);
  check('deletion removes all owner tabs', browser.tabs.size === 0);
  check('deletion removes recovery and AI owner state', !profile.data.owners[browser.main.owner] && !browser.ai.state.has(browser.main.owner));
  console.log(`${checks} native retention checks passed`);
} finally {
  browser.dispose(); window.destroy();
  await new Promise(resolve => server.close(resolve));
  fs.rmSync(temp, { recursive: true, force: true });
  app.quit();
}
}).catch(error => { console.error(error); app.exit(1); });
