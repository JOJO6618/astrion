import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const output = path.join(root, 'output/windows-update-real-20261114');
const endpoint = process.argv[2] || 'http://127.0.0.1:19973';
if (!/^http:\/\/127\.0\.0\.1:1997[3-5]$/.test(endpoint)) throw new Error('Unexpected test CDP endpoint');
const browser = await chromium.connectOverCDP(endpoint, { timeout: 15000 });
const pages = [];
for (const context of browser.contexts()) {
  for (const page of context.pages()) {
    pages.push({
      url: page.url(),
      title: await page.title().catch(() => ''),
      body: (await page.locator('body').innerText({ timeout: 8000 }).catch(() => '')).slice(0, 9000),
      geometry: await page.evaluate(() => ({ innerWidth, innerHeight, visibility: document.visibilityState, menu: [...document.querySelectorAll('.personal-menu,.personal-page-btn')].map(node => ({ class: node.className, style: node.getAttribute('style'), rect: node.getBoundingClientRect().toJSON() })) })).catch(() => null),
      buttons: await page.locator('button').evaluateAll(nodes => nodes.map(node => ({
        text: node.innerText, label: node.getAttribute('aria-label'), title: node.getAttribute('title'), class: node.className,
      }))).catch(() => []),
    });
  }
}
fs.writeFileSync(path.join(output, 'ui-inspection.json'), JSON.stringify({ endpoint, time: new Date().toISOString(), pages }, null, 2));
console.log(JSON.stringify(pages, null, 2));
// Do not call Browser.close on the attached real application. Exiting this
// inspection process drops only its CDP connection.
process.exit(0);
