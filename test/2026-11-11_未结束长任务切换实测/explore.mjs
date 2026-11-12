// 探路：登录副本，进入对话 A，列出侧栏对话项与输入框，截一张图用于确认结构。
import { chromium } from 'playwright';
const [base, convA, convB, out] = process.argv.slice(2);
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
page.on('pageerror', (e) => console.log('pageerror', e.message));
await page.goto(`${base}/login`);
await page.getByText('宿主机模式').click();
await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 30000 });
await page.goto(`${base}/${convA.replace(/^conv_/, '')}`);
await page.waitForTimeout(6000);
const info = await page.evaluate(([a, b]) => ({
  path: location.pathname,
  items: [...document.querySelectorAll('.conversation-item')].slice(0, 8).map((el) => ({
    text: el.textContent.trim().slice(0, 30),
    trigger: el.querySelector('[data-action-trigger]')?.getAttribute('data-action-trigger')
  })),
  hasA: !!document.querySelector(`[data-action-trigger="${a}"]`),
  hasB: !!document.querySelector(`[data-action-trigger="${b}"]`),
  editors: [...document.querySelectorAll('.input-area textarea, .input-area [contenteditable="true"]')].map(
    (el) => el.tagName + '.' + el.className
  ),
  loading: !!document.querySelector('.is-history-loading')
}), [convA, convB]);
console.log(JSON.stringify(info, null, 1));
await page.screenshot({ path: out });
await browser.close();
