import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { app, BaseWindow, WebContentsView } from 'electron';
import { NativeBrowser, setBrowserDataRoot } from '../../desktop-electron/src/browser/index.js';
import { aiOwner } from '../../desktop-electron/src/browser/ai/controller.js';

const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'astrion-browser-download-'));
const downloadDir = path.join(temporary, 'workspace');
const userDownloadDir = path.join(temporary, 'user-downloads');
fs.mkdirSync(userDownloadDir);
app.setPath('userData', path.join(temporary, 'electron'));
app.setPath('downloads', userDownloadDir);
setBrowserDataRoot(path.join(temporary, 'profiles'));

const PAGE = `<!doctype html><title>Downloads regression</title>
<a href="/fast">Quick attachment</a>
<a href="/redirect" target="_blank">Popup attachment</a>
<a href="/slow">Known size</a>
<a href="/unknown">Unknown size</a>
<button onclick="setTimeout(() => { location.href = '/late' }, 2600)">Delayed attachment</button>`;
const CHUNK = Buffer.alloc(64 * 1024, 'a');
const CHUNKS = 16;

function serve(request, response) {
  const route = request.url;
  if (route === '/') {
    response.setHeader('Content-Type', 'text/html');
    response.end(PAGE);
    return;
  }
  if (route === '/redirect') {
    response.writeHead(302, { Location: '/popup' });
    response.end();
    return;
  }
  response.setHeader('Content-Disposition', `attachment; filename="${route.slice(1)}.bin"`);
  response.setHeader('Content-Type', 'application/octet-stream');
  if (route !== '/slow' && route !== '/unknown') {
    response.end(route);
    return;
  }
  if (route === '/slow') response.setHeader('Content-Length', CHUNK.length * CHUNKS);
  let sent = 0;
  let next;
  const send = () => {
    if (sent === CHUNKS) {
      response.end();
      return;
    }
    response.write(CHUNK);
    sent++;
    next = setTimeout(send, sent === 6 && route === '/slow' ? 4700 : 300);
  };
  response.once('close', () => clearTimeout(next));
  send();
}

async function until(read, label, timeout = 7000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const result = await read();
    if (result) return result;
    await delay(60);
  }
  assert.fail(`Timed out: ${label}`);
}

app.whenReady().then(run);

async function run() {
  let server, win, host, browser;
  let exit = 0;
  try {
    server = http.createServer(serve);
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    const origin = `http://127.0.0.1:${server.address().port}`;
    win = new BaseWindow({ show: false, width: 1200, height: 800 });
    host = new WebContentsView();
    win.contentView.addChildView(host);
    host.setBounds({ x: 0, y: 0, width: 1200, height: 800 });
    browser = new NativeBrowser(win, host, origin);
    const identity = {
      username: 'fixture',
      workspace_id: 'workspace',
      conversation_id: 'download'
    };
    const otherIdentity = { ...identity, conversation_id: 'other' };
    let sequence = 0;
    const call = (op, args = {}, caller = identity) =>
      browser.ai.call({
        call_id: `fixture-${++sequence}`,
        op,
        args,
        identity: caller,
        any_site: true,
        download_dir: downloadDir
      });
    const info = async (id) => (await call('downloads', { download_id: id })).downloads[0];
    async function click(label) {
      const { snapshot } = await call('snapshot');
      const line = snapshot.text.split('\n').find((row) => row.includes(`"${label}"`));
      const target = line?.match(/\[ref=(e\d+)\]/)?.[1];
      assert.ok(target, `Missing control ${label} in ${snapshot.text}`);
      return call('click', { target });
    }

    assert.deepEqual((await call('downloads')).downloads, []);
    await call('navigate', { url: origin });
    const owner = aiOwner(identity).owner;
    const profile = browser.ensureProfile(identity.username);
    const features = browser.profileFeatures(profile);
    const openingTab = browser.ai.current(owner);
    const readSnapshot = browser.ai.snapshotFor.bind(browser.ai);
    browser.ai.snapshotFor = async (tab, ...args) => {
      if (tab.aiDownload?.items.some((row) => row.url === `${origin}/fast`))
        throw new Error('Fixture: page snapshot unavailable after download');
      return readSnapshot(tab, ...args);
    };
    const fast = await click('Quick attachment');
    browser.ai.snapshotFor = readSnapshot;
    assert.equal(fast.success, true);
    assert.ok(fast.notes.some((note) => note.includes('网页快照不可用')));
    assert.equal(fast.downloads[0].url, `${origin}/fast`);
    const quick = await until(async () => {
      const row = await info(fast.downloads[0].id);
      return row.state === 'completed' && row;
    }, 'quick completion');
    assert.equal(fs.readFileSync(quick.path, 'utf8'), '/fast');
    assert.ok(quick.completedAt);
    assert.equal(quick.estimatedCompletionAt, null);
    assert.equal(quick.bytesPerSecond, null);
    console.log(
      'PASS download acknowledgement survives snapshot failure and reports actual completion'
    );

    const popup = await click('Popup attachment');
    assert.equal(popup.success, true);
    const redirected =
      popup.downloads?.[0] ||
      (await until(
        async () =>
          (await call('downloads')).downloads.find((row) => row.url === `${origin}/popup`),
        'popup attachment'
      ));
    assert.equal(redirected.url, `${origin}/popup`);
    assert.equal(path.dirname(redirected.path), downloadDir);
    await until(() => browser.ai.ownerTabs(owner).length === 1, 'download-only popup closed');
    console.log('PASS redirected popup attachment is recorded under the opener conversation');

    const delayed = await click('Delayed attachment');
    assert.equal(delayed.success, true);
    assert.equal(delayed.downloads, undefined);
    await call('snapshot');
    const late = await until(
      async () => (await call('downloads')).downloads.find((row) => row.url === `${origin}/late`),
      'delayed download'
    );
    assert.equal(path.dirname(late.path), downloadDir);
    console.log('PASS delayed downloads are discoverable even across a read-only page operation');

    const slow = (await click('Known size')).downloads[0];
    const moving = await until(async () => {
      const row = await info(slow.id);
      return row.bytesPerSecond > 0 && row.estimatedCompletionAt && row;
    }, 'speed and estimated completion');
    assert.ok(moving.received > 0 && moving.received < moving.total);
    assert.equal(moving.total, CHUNKS * CHUNK.length);
    assert.ok(moving.estimatedCompletionAt > Date.now());
    const ui = browser.snapshot({ active: () => undefined, profile, kind: 'view', detached: true });
    const uiDownload = ui.downloads.find((row) => row.id === slow.id);
    assert.equal(uiDownload.bytesPerSecond, moving.bytesPerSecond);
    assert.equal(uiDownload.estimatedCompletionAt, moving.estimatedCompletionAt);
    assert.equal(uiDownload.path, undefined);
    assert.equal(uiDownload.url, undefined);
    await until(async () => {
      const row = await info(slow.id);
      return (
        row.state === 'progressing' &&
        row.bytesPerSecond === null &&
        row.estimatedCompletionAt === null
      );
    }, 'stalled transfer clears rate and estimate');
    const completed = await until(async () => {
      const row = await info(slow.id);
      return row.state === 'completed' && row;
    }, 'slow completion');
    assert.equal(completed.received, completed.total);
    assert.ok(completed.completedAt);
    assert.equal(completed.bytesPerSecond, null);
    console.log(
      'PASS native and tool progress agree, stalled speeds clear, and successful downloads record a completion time'
    );

    const unknown = (await click('Unknown size')).downloads[0];
    const unknownProgress = await until(async () => {
      const row = await info(unknown.id);
      return row.state === 'progressing' && row.bytesPerSecond > 0 && row;
    }, 'unknown-length transfer sampled');
    assert.equal(unknownProgress.total, null);
    assert.equal(unknownProgress.estimatedCompletionAt, null);
    await features.command('download-cancel', unknown.id);
    await until(async () => (await info(unknown.id)).state === 'cancelled', 'cancelled download');
    assert.ok(openingTab.aiDownload.until > Date.now());
    openingTab.view.webContents.emit('input-event', {}, { type: 'mouseDown' });
    openingTab.view.webContents.downloadURL(`${origin}/manual`);
    const manual = await until(
      () =>
        profile.data.downloads.find(
          (row) => row.name === 'manual.bin' && row.state === 'completed'
        ),
      'manual attachment'
    );
    assert.equal(path.dirname(manual.path), path.join(userDownloadDir, 'Astrion'));
    await assert.rejects(info(manual.id), { code: 'unknown_download' });
    await call('tabs', { action: 'close', tab_id: browser.ai.shortId(owner, openingTab) });
    await until(() => browser.ai.ownerTabs(owner).length === 0, 'source tab closed');
    assert.equal((await info(quick.id)).state, 'completed');
    assert.deepEqual((await call('downloads', {}, otherIdentity)).downloads, []);
    await assert.rejects(call('downloads', { download_id: quick.id }, otherIdentity), {
      code: 'unknown_download'
    });
    console.log(
      'PASS unknown length, cancellation, closed tabs, manual downloads, and conversation isolation'
    );
  } catch (error) {
    console.error(error);
    exit = 1;
  } finally {
    browser?.dispose();
    if (host && !host.webContents.isDestroyed()) host.webContents.close();
    if (win && !win.isDestroyed()) win.destroy();
    server?.closeAllConnections();
    server?.close();
    app.exit(exit);
  }
}
