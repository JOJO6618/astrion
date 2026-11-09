import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash, generateKeyPairSync } from 'node:crypto';
import { chromium } from '../../node_modules/playwright/index.mjs';
import { manualNavigationURL, navigationURL, isLocalFile } from '../../desktop-electron/src/browser/policy.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const artifact = path.join(root, '_experiments/windows-browser-20261109');
const run = fs.mkdtempSync(path.join(artifact, 'extension-'));
const source = path.join(root, 'desktop/browser-migration-extension');
const ext = path.join(run, 'extension');
fs.cpSync(source, ext, { recursive: true });
const manifest = JSON.parse(fs.readFileSync(path.join(ext, 'manifest.json')));
// Only the fixture grants fake sites up front. Production asks on the user's click.
const key = generateKeyPairSync('rsa', { modulusLength: 1024 }).publicKey.export({ type: 'spki', format: 'der' });
manifest.key = key.toString('base64');
manifest.host_permissions = ['https://*.example.test/*'];
fs.writeFileSync(path.join(ext, 'manifest.json'), JSON.stringify(manifest));
const id = [...createHash('sha256').update(key).digest().subarray(0, 16)].flatMap(byte => [byte >> 4, byte & 15]).map(n => String.fromCharCode(97 + n)).join('');
const checks = {};
function check(name, condition) { assert.ok(condition, name); checks[name] = true; }
for (const url of ['file://server/share/x', 'file:////server/share/x', 'file:///C:/bad%00name', 'file:///C:/bad%2Fname']) assert.throws(() => manualNavigationURL(url));
check('manualFilePolicyAllowsLocalOnly', isLocalFile('file:///C:/example%20dir/local.html'));
assert.throws(() => navigationURL('file:///C:/local.html'));
check('aiNavigationRemainsWebOnly', true);
const context = await chromium.launchPersistentContext(path.join(run, 'profile'), {
  channel: 'chromium', headless: true,
  args: [`--disable-extensions-except=${ext}`, `--load-extension=${ext}`]
});
try {
  const page = await context.newPage();
  await page.goto(`chrome-extension://${id}/index.html`);
  await page.waitForSelector('#consent');
  check('extensionLoadsWithProductionScripts', await page.locator('#export').isDisabled());
  const result = await page.evaluate(async () => {
    const { exportCookies, selectedSites, permissionOrigins } = await import('./export.js');
    const base = { url: 'https://migration.example.test/', name: 'same', value: 'fake-unpartitioned', secure: true, httpOnly: true, sameSite: 'no_restriction', expirationDate: 4102444800 };
    await chrome.cookies.set(base);
    await chrome.cookies.set({ ...base, value: 'fake-one', partitionKey: { topLevelSite: 'https://one.test', hasCrossSiteAncestor: true } });
    await chrome.cookies.set({ ...base, value: 'fake-two', partitionKey: { topLevelSite: 'https://example.test', hasCrossSiteAncestor: false } });
    await chrome.cookies.set({ ...base, name: 'domain', domain: '.example.test', value: 'fake-domain' });
    const result = await exportCookies(chrome.cookies, ['example.test']);
    const denied = [];
    const accepted = [];
    for (const value of ['file:///private', 'https://user:secret@example.test', 'https://*.example.test']) {
      try { accepted.push({ input: value, output: selectedSites(value) }); } catch { denied.push(value); }
    }
    const bundle = JSON.parse(result.content);
    return { ...result, denied: denied.length, accepted, domains: selectedSites('example.test\nhttps://example.test/path'), origins: permissionOrigins(['example.test']), cookies: bundle.cookies };
  });
  assert.deepEqual(result.accepted, []);
  check('cookieApiExportsAllPartitionKeys', result.count === 4 && result.partitioned === 2);
  check('exportPreservesHostOnlyAndHttpOnly', result.cookies.some(row => row.name === 'same' && row.hostOnly && row.httpOnly));
  check('exportPreservesDomainScope', result.cookies.some(row => row.name === 'domain' && !row.hostOnly && row.domain === '.example.test'));
  assert.deepEqual({ domains: result.domains, origins: result.origins, denied: result.denied }, { domains: ['example.test'], origins: ['http://*.example.test/*', 'https://*.example.test/*'], denied: 3 });
  check('permissionScopeIsExplicit', true);
  fs.writeFileSync(path.join(run, 'synthetic-export.json'), result.content);
  const bytes = fs.statSync(path.join(run, 'synthetic-export.json')).size;
  const report = { checks, run, synthetic_export_bytes: bytes, browser: context.browser()?.version(), limits: ['Chrome permission prompt requires manual acceptance in the real profile; fixture grants only fake example.test sites.'] };
  fs.writeFileSync(path.join(artifact, 'extension-migration.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
} finally { await context.close(); }
