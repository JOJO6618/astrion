import { readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { runInThisContext } from 'node:vm';
import { chromium } from 'playwright';

const directory = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(directory, '../..');
const target = process.argv[2] || 'http://127.0.0.1:5173/';
if (!['127.0.0.1', 'localhost'].includes(new URL(target).hostname)) {
  throw new Error('此回归仅允许测试本机原型');
}
process.chdir(projectRoot);
const source = await readFile(resolve(directory, 'browser-checks.js'), 'utf8');
// The trusted local test file is also directly executable by Playwright MCP.
const verify = runInThisContext(`(${source.trim()})`, { filename: 'browser-checks.js' });
let browser;
try {
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto(target);
  const report = await verify(page);
  await writeFile(resolve(directory, 'browser-results.json'), `${JSON.stringify(report, null, 2)}\n`);
  console.log(JSON.stringify(report, null, 2));
} catch (error) {
  console.error(error);
  process.exitCode = 1;
} finally {
  await browser?.close();
}
