const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
(async () => {
  const browser = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://127.0.0.1:8192/schedules', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1500);
    if (page.url().includes('/login')) {
      await page.getByRole('button', { name: '宿主机模式（免登录）' }).click();
      await page.waitForURL(url => !url.pathname.includes('/login'));
      await page.goto('http://127.0.0.1:8192/schedules');
    }
    await page.getByRole('button', { name: '新建计划', exact: true }).first().waitFor();
    console.log('URL', page.url());
    console.log((await page.locator('body').innerText()).slice(0, 15000));
    console.log('ERRORS', JSON.stringify(errors));
    const out = path.join(__dirname, 'browser-evidence');
    fs.mkdirSync(out, { recursive: true });
    await page.screenshot({ path: path.join(out, 'initial.png'), fullPage: true });
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
