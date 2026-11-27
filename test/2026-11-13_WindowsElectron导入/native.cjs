const { app, BaseWindow, WebContentsView } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const { pathToFileURL } = require('node:url');
const { DatabaseSync } = require('node:sqlite');
const root = path.resolve(__dirname, '../..');
const output = process.env.ASTRION_IMPORT_TEST_ROOT;
if (!output || !fs.existsSync(path.join(output, 'source-hashes.json'))) throw new Error('Explicit synthetic fixture required');
const verify = process.argv.includes('--verify');
app.setPath('userData', path.join(output, 'electron'));
app.setPath('sessionData', path.join(output, 'electron'));
app.disableHardwareAcceleration();
const checks = [];
function check(name, condition) { checks.push({ name, ok: !!condition }); if (!condition) throw new Error(name); }
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(fn) { for (let i = 0; i < 200; i++) { if (await fn()) return; await delay(25); } throw new Error('Test condition timed out'); }
const load = file => import(pathToFileURL(path.join(root, file)).href);
const sourceRoot = path.join(output, 'localapp/Google/Chrome/User Data');
const csv = rows => ['url,username,password', ...rows.map(row => row.map(value => `"${value.replaceAll('"', '""')}"`).join(','))].join('\r\n');
let manager, window, server, result;
(async () => {
  await app.whenReady();
  const { NativeBrowser, setBrowserDataRoot } = await load('desktop-electron/src/browser/index.js');
  const { readWindowsChrome } = await load('desktop-electron/src/browser/chrome/windows-source.js');
  const { ImportCookieStore } = await load('desktop-electron/src/browser/chrome/cookie-store.js');
  const { PasswordVault } = await load('desktop-electron/src/browser/passwords/vault.js');
  server = http.createServer((req, res) => {
    res.setHeader('Content-Type', req.url === '/api/session-status' ? 'application/json' : 'text/html');
    res.end(req.url === '/api/session-status' ? JSON.stringify({ success: true, session: { logged_in: true, username: 'synthetic-import-user' } }) : '<title>Synthetic trusted import fixture</title>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  window = new BaseWindow({ show: false });
  const view = new WebContentsView({ webPreferences: { preload: path.join(root, 'desktop-electron/src/preload-main.cjs'), sandbox: true, contextIsolation: true } });
  window.contentView.addChildView(view);
  await view.webContents.loadURL(`${origin}/new`);
  setBrowserDataRoot(path.join(output, 'data'));
  manager = new NativeBrowser(window, view, origin);
  const resources = process.env.ASTRION_IMPORT_RESOURCES;
  const readerOptions = { env: { ...process.env, LOCALAPPDATA: path.join(output, 'localapp') },
    ...(resources ? { resolveHelper: () => path.join(resources, 'astrion-windows-helper.exe') } : {}) };
  const read = (request, options) => readWindowsChrome(request, { ...readerOptions, ...options });
  manager.credentials.importer.dependencies.windowsRequest = read;
  const run = code => view.webContents.executeJavaScript(code);
  const connected = await run('window.astrionBrowser.connect({mode:"agent",workspace:"synthetic",conversation:"import-test"})');
  const lease = connected.lease;
  const command = payload => run(`window.astrionBrowser.credentials(${JSON.stringify({ ...payload, lease })})`);
  const vault = manager.credentials.vault(manager.main.profile);
  check('Windows renderer exposes file and history import and follow-AI capabilities', await run('window.astrionBrowser.credentialFileImport === true && window.astrionBrowser.credentialHistoryOnly === true && window.astrionBrowser.followAi === true'));
  if (verify) {
    check('DPAPI password vault survives a new Electron process', vault.find('https://csv.example.test', 'csv-user')?.password === 'synthetic-second\nline');
    check('history survives a new Electron process', manager.main.profile.data.history.some(row => row.url === 'https://history.example.test/'));
    const store = new ImportCookieStore(manager.main.profile);
    const cookies = await store.open(); store.close();
    check('persistent domain cookie survives restart', cookies.some(row => row.name === 'json-domain' && row.domain === '.example.test' && row.value === 'synthetic-json-domain'));
    const chips = cookies.filter(row => row.name === 'json-shared' && row.partitionKey);
    check('both partition keys and ancestor flags survive restart', chips.length === 2 && chips.some(row => row.partitionKey.hasCrossSiteAncestor === true) && chips.some(row => row.partitionKey.hasCrossSiteAncestor === false));
  } else {
    const sources = await command({ action: 'sources' });
    check('sources use synthetic Windows LOCALAPPDATA, not macOS paths', sources.sources.length === 1 && sources.sources[0].name === 'Synthetic import source');
    const requests = [];
    manager.credentials.importer.dependencies.windowsRequest = (request, options) => {
      requests.push(request);
      return read(request, options);
    };
    for (const selected of [
      { passwords: true, cookies: false, history: true },
      { passwords: false, cookies: true, history: true },
    ]) {
      check('direct credential selection fails before reading the source', await command({ action: 'preview', value: { source: 'Default', selected } }).then(() => false, () => requests.length === 0));
    }
    const selected = { passwords: false, cookies: false, history: true };
    const preview = (await command({ action: 'preview', value: { source: 'Default', selected } })).preview;
    check('history preview reads only history data', requests.length === 1 && requests[0].action === 'preview-history'
      && preview.stats.passwords.read === 0 && preview.stats.cookies.read === 0 && preview.stats.history.ready === 1
      && !JSON.stringify(preview).includes('https://history.example.test/'));
    manager.credentials.importer.dependencies.windowsRequest = read;
    const report = (await command({ action: 'import', value: { id: preview.id, choices: {}, replaceCookies: false } })).report;
    check('history keeps valid web visits and gives specific skip reasons', report.stats.history.imported === 1
      && report.stats.history.reasons.history_non_web_url === 1 && report.stats.history.reasons.history_invalid_url === 1
      && report.stats.history.reasons.history_invalid_time === 1);
    await command({ action: 'save', value: { origin: 'https://csv.example.test', username: 'csv-user', password: 'synthetic-existing' } });
    const csvValue = { format: 'password-csv', content: '\ufeff' + csv([
      ['https://csv.example.test/login', 'csv-user', 'synthetic-existing'],
      ['https://csv.example.test/a', 'csv-user', 'synthetic-first,"quoted"'],
      ['https://csv.example.test/b', 'csv-user', 'synthetic-second\nline']
    ]) };
    const csvPreview = (await command({ action: 'preview-file', value: csvValue })).preview;
    check('CSV BOM, quotes and multiline passwords reach one explicit conflict group', csvPreview.conflicts.length === 1 && csvPreview.conflicts[0].candidates.length === 2 && csvPreview.stats.passwords.skipped === 1);
    check('CSV preview has metadata only', !JSON.stringify(csvPreview).includes('synthetic-first') && !JSON.stringify(csvPreview).includes('synthetic-second'));
    let blocked = false;
    try { await command({ action: 'import', value: { id: csvPreview.id, choices: {}, replaceCookies: false } }); } catch { blocked = true; }
    check('password conflicts require an explicit choice', blocked);
    const conflict = csvPreview.conflicts[0];
    const csvReport = (await command({ action: 'import', value: { id: csvPreview.id, choices: { [conflict.id]: conflict.candidates[1].id }, replaceCookies: false } })).report;
    check('chosen CSV password is saved without changing its newline', csvReport.stats.passwords.imported === 1 && vault.find('https://csv.example.test', 'csv-user').password === 'synthetic-second\nline');
    check('password file is encrypted and can reopen through Windows safeStorage', !fs.readFileSync(vault.file).includes(Buffer.from('synthetic-second')) && new PasswordVault(manager.main.profile.directory).find('https://csv.example.test', 'csv-user').password === 'synthetic-second\nline');
    const stalePreview = (await command({ action: 'preview-file', value: { format: 'password-csv', content: csv([['https://stale.example.test', 'user', 'synthetic-stale']]) } })).preview;
    await command({ action: 'save', value: { origin: 'https://other.example.test', username: 'user', password: 'synthetic-other' } });
    blocked = false;
    try { await command({ action: 'import', value: { id: stalePreview.id, choices: {}, replaceCookies: false } }); } catch { blocked = true; }
    check('vault changes invalidate the preview instead of overwriting new data', blocked);
    await command({ action: 'cancel-import' });
    const base = { domain: 'cookie.example.test', path: '/', secure: true, httpOnly: true, hostOnly: true, session: false, expirationDate: Math.floor(Date.now() / 1000) + 86400, sameSite: 'no_restriction' };
    const cookies = [
      { ...base, name: 'json-host', value: 'synthetic-json-host', session: true, sameSite: 'lax' },
      { ...base, domain: '.example.test', hostOnly: false, name: 'json-domain', value: 'synthetic-json-domain' },
      { ...base, name: 'json-shared', value: 'synthetic-json-ordinary' },
      ...[true, false].map((ancestor, i) => ({ ...base, name: 'json-shared', value: `synthetic-json-partition-${i}`, partitionKey: { topLevelSite: `https://top-${i}.test`, hasCrossSiteAncestor: ancestor } })),
      { ...base, name: 'expired', value: 'synthetic-expired', expirationDate: 1 },
      { ...base, name: 'invalid', value: 'synthetic-invalid', secure: false }
    ];
    const jsonValue = { format: 'cookie-json', content: JSON.stringify({ format: 'astrion-cookie-export', version: 1, cookies }) };
    const cookiePreview = (await command({ action: 'preview-file', value: jsonValue })).preview;
    check('Cookie JSON validates rows and keeps both partitioned cookies', cookiePreview.stats.cookies.ready === 5 && cookiePreview.stats.cookies.skipped === 1 && cookiePreview.stats.cookies.failed === 1);
    const cookieReport = (await command({ action: 'import', value: { id: cookiePreview.id, choices: {}, replaceCookies: false } })).report;
    check('ordinary, domain and partitioned cookies pass real Chromium readback', cookieReport.stats.cookies.imported === 5 && cookieReport.stats.cookies.failed === 1);
    const store = new ImportCookieStore(manager.main.profile);
    const stored = await store.open(); store.close();
    check('host-only scope and session flag are preserved', stored.some(row => row.name === 'json-host' && row.domain === 'cookie.example.test' && row.session && row.sameSite === 'Lax'));
    check('domain scope, HttpOnly, Secure and SameSite=None are preserved', stored.some(row => row.name === 'json-domain' && row.domain === '.example.test' && row.httpOnly && row.secure && row.sameSite === 'None'));
    const partitioned = stored.filter(row => row.name === 'json-shared' && row.partitionKey);
    check('both ancestor states are preserved without flattening', partitioned.length === 2 && partitioned.some(row => row.partitionKey.hasCrossSiteAncestor) && partitioned.some(row => !row.partitionKey.hasCrossSiteAncestor));
    const repeated = (await command({ action: 'preview-file', value: jsonValue })).preview;
    const skipped = (await command({ action: 'import', value: { id: repeated.id, choices: {}, replaceCookies: false } })).report;
    check('existing cookies are skipped when replacement is disabled', skipped.stats.cookies.imported === 0 && skipped.stats.cookies.skipped === 6);
    const replacement = (await command({ action: 'preview-file', value: { format: 'cookie-json', content: JSON.stringify({ format: 'astrion-cookie-export', version: 1, cookies: [{ ...cookies[0], value: 'synthetic-replaced' }] }) } })).preview;
    const replaced = (await command({ action: 'import', value: { id: replacement.id, choices: {}, replaceCookies: true } })).report;
    const replacementStore = new ImportCookieStore(manager.main.profile);
    const replacementRows = await replacementStore.open(); replacementStore.close();
    check('explicit replacement updates the existing cookie and verifies its value', replaced.stats.cookies.imported === 1 && replacementRows.some(row => row.name === 'json-host' && row.value === 'synthetic-replaced'));
    const invalid = await command({ action: 'preview-file', value: { format: 'cookie-json', content: '[]' } }).then(() => false, () => true);
    check('unversioned cookie input is rejected', invalid);
    const aborted = new AbortController(); aborted.abort();
    check('a cancelled request does not start the native helper', await readWindowsChrome({ action: 'sources' }, { ...readerOptions, signal: aborted.signal, resolveHelper: () => { throw new Error('helper resolution should be skipped'); } }).then(() => false, error => error.message === 'import_cancelled'));
    let waiting = false;
    manager.credentials.importer.dependencies.windowsRequest = (_request, { signal }) => new Promise((_, reject) => { waiting = true; signal.addEventListener('abort', () => reject(new Error('import_cancelled')), { once: true }); });
    const pending = command({ action: 'preview-file', value: csvValue }).catch(() => null);
    await until(() => waiting);
    await command({ action: 'cancel-import' }); await pending;
    check('cancel waits for the reader and clears both lock and preview secrets', !manager.credentials.operation && manager.credentials.importer.jobs.size === 0);
    manager.credentials.importer.dependencies.windowsRequest = read;
    const macRoot = path.join(output, 'synthetic-mac'); fs.mkdirSync(path.join(macRoot, 'Default'), { recursive: true });
    const macKey = crypto.pbkdf2Sync('synthetic-mac-secret', 'saltysalt', 1003, 16, 'sha1');
    const cipher = crypto.createCipheriv('aes-128-cbc', macKey, Buffer.alloc(16, 0x20));
    const macValue = Buffer.concat([Buffer.from('v10'), cipher.update('synthetic-mac-password'), cipher.final()]);
    const db = new DatabaseSync(path.join(macRoot, 'Default/Login Data'));
    db.exec('CREATE TABLE logins(origin_url TEXT,username_value TEXT,password_value BLOB)');
    db.prepare('INSERT INTO logins VALUES(?,?,?)').run('https://mac-fixture.example.test/', 'mac-user', macValue); db.close();
    const { ChromeImporter } = await load('desktop-electron/src/browser/chrome/importer.js');
    const mac = new ChromeImporter({ platform: 'darwin', root: macRoot, keySecret: async () => Buffer.from('synthetic-mac-secret') });
    const macPreview = await mac.preview(manager.main.profile, vault, { source: 'Default', selected: { passwords: true, cookies: false, history: false } }, () => {});
    const macReport = await mac.commit(manager.main.profile, vault, { id: macPreview.id, choices: {}, replaceCookies: false }, () => {});
    check('macOS AES-CBC path still shares the destination commit flow', macReport.stats.passwords.imported === 1 && vault.find('https://mac-fixture.example.test', 'mac-user').password === 'synthetic-mac-password');
    mac.dispose(); macKey.fill(0);
    const expected = JSON.parse(fs.readFileSync(path.join(output, 'source-hashes.json')));
    check('source databases and Local State remain byte-identical', Object.entries(expected).every(([name, hash]) => crypto.createHash('sha256').update(fs.readFileSync(path.join(sourceRoot, name))).digest('hex') === hash));
    const wrongLease = await run('window.astrionBrowser.credentials({action:"list",lease:"retired"})').then(() => false, () => true);
    check('retired renderer lease cannot access credential operations', wrongLease);
    const owner = manager.main.owner;
    await run(`window.astrionBrowser.command(${JSON.stringify({ lease, action: 'popout' })})`);
    const popup = manager.popouts.get(owner); popup.window.hide();
    await until(() => !popup.view.webContents.isLoading());
    const popupRun = code => popup.view.webContents.executeJavaScript(code);
    check('independent browser window exposes file and history import and follow-AI', await popupRun('window.astrionBrowser.credentialFileImport === true && window.astrionBrowser.credentialHistoryOnly === true && window.astrionBrowser.followAi === true'));
    const popupConnection = await popupRun('window.astrionBrowser.connect({})');
    const popupPreview = await popupRun(`window.astrionBrowser.credentials(${JSON.stringify({ lease: popupConnection.lease, action: 'preview-file', value: { format: 'password-csv', content: csv([['https://popup.example.test', 'popup-user', 'synthetic-popup']]) } })})`);
    check('independent window uses the same file preview service without secrets', popupPreview.preview.stats.passwords.ready === 1 && !JSON.stringify(popupPreview).includes('synthetic-popup'));
    const popupReport = await popupRun(`window.astrionBrowser.credentials(${JSON.stringify({ lease: popupConnection.lease, action: 'import', value: { id: popupPreview.preview.id, choices: {}, replaceCookies: false } })})`);
    check('independent window commits into the shared user vault', popupReport.report.stats.passwords.imported === 1 && vault.find('https://popup.example.test', 'popup-user').password === 'synthetic-popup');
    popup.window.close();
  }
  await manager.main.profile.close();
  result = { success: true, phase: verify ? 'restart' : 'import', checks, real_user_data_read: false };
})().catch(error => { result = { success: false, phase: verify ? 'restart' : 'import', checks, error: error.message, real_user_data_read: false }; }).finally(async () => {
  try { manager?.dispose(); } catch {}
  if (window && !window.isDestroyed()) window.destroy();
  if (server) await new Promise(resolve => server.close(resolve));
  fs.writeFileSync(path.join(output, verify ? 'restart-results.json' : 'import-results.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ success: result.success, phase: result.phase, checks: checks.length, ...(result.success ? {} : { error: result.error }) }));
  app.exit(result.success ? 0 : 1);
});
