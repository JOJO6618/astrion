import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const output = path.join(root, 'output/windows-update-real-20261114');
const install = process.argv.includes('--install');
const log = (event, details = {}) => {
  const row = { time: new Date().toISOString(), event, ...details };
  fs.appendFileSync(path.join(output, 'ui-actions.jsonl'), JSON.stringify(row) + '\n');
  console.log(JSON.stringify(row));
};
const browser = await chromium.connectOverCDP('http://127.0.0.1:19973', { timeout: 15000 });
const page = browser.contexts().flatMap(context => context.pages()).find(candidate =>
  /^http:\/\/127\.0\.0\.1:\d+\/(?:new|conversation|$)/.test(candidate.url()));
if (!page) throw new Error('Real legacy application page was not found');
page.on('response', async response => {
  const url = new URL(response.url());
  if (!url.pathname.startsWith('/api/desktop/update/')) return;
  log('real-update-response', { route: url.pathname, status: response.status(), body: await response.json().catch(() => null) });
});
page.on('close', () => log('legacy-page-closed'));
browser.on('disconnected', () => log('legacy-cdp-disconnected'));
log('attached-real-legacy-page', { url: page.url() });
const skipSandbox = page.getByRole('button', { name: '暂不安装', exact: true });
if (await skipSandbox.isVisible().catch(() => false)) {
  await skipSandbox.click();
  log('clicked-skip-sandbox-setup');
}
const expandSidebar = page.getByRole('button', { name: '展开侧边栏', exact: true });
if (await expandSidebar.isVisible().catch(() => false)) {
  if (await page.locator('.personal-page-btn').getAttribute('aria-expanded') === 'true') {
    await page.locator('.personal-page-btn').click();
    await page.locator('.personal-menu').waitFor({ state: 'hidden' });
  }
  await expandSidebar.click();
  await page.waitForFunction(() => (document.querySelector('.personal-page-btn')?.getBoundingClientRect().left ?? -1) >= 0);
  log('clicked-expand-sidebar');
}
const dialog = page.locator('.update-dialog');
if (!await dialog.isVisible().catch(() => false)) {
  const menuItem = page.locator('button.personal-menu-item').filter({ hasText: /^软件更新$/ });
  if (await page.locator('.personal-page-btn').getAttribute('aria-expanded') !== 'true') await page.locator('.personal-page-btn').click();
  await menuItem.click();
  log('clicked-software-update-menu');
}
await dialog.waitFor({ state: 'visible', timeout: 30000 });
const updateButton = dialog.getByRole('button', { name: '立即更新并重启', exact: true });
await updateButton.waitFor({ state: 'visible', timeout: 90000 });
const text = await dialog.innerText();
if (!text.includes('0.9.3') || !text.includes('0.10.0')) throw new Error('The real dialog does not offer 0.9.3 -> 0.10.0: ' + text);
fs.writeFileSync(path.join(output, 'legacy-update-dialog.txt'), text);
await dialog.screenshot({ path: path.join(output, 'legacy-update-dialog.png') });
log('real-update-offer-verified', { currentVersion: '0.9.3', targetVersion: '0.10.0' });
if (install) {
  await updateButton.click();
  log('clicked-update-and-restart-button');
  const deadline = Date.now() + 180000;
  while (Date.now() < deadline && browser.isConnected() && !page.isClosed()) {
    await new Promise(resolve => setTimeout(resolve, 500));
    const error = await page.locator('.update-error-detail').innerText({ timeout: 500 }).catch(() => '');
    if (error) throw new Error('Real updater reported: ' + error);
  }
  if (browser.isConnected() && !page.isClosed()) throw new Error('Old app did not exit within 180 seconds');
  log('legacy-exited-after-real-ui-update');
}
// Disconnect only this observer. Never send Browser.close to a real application.
process.exit(0);
