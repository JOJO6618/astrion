// 从仓库根 node_modules/playwright-core 提取无障碍快照注入脚本（Apache-2.0），
// 生成 src/browser/ai/vendor/playwright-injected.js，并复制许可证与 NOTICE。
// 用法：node desktop-electron/scripts/vendor_playwright_injected.mjs
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const core = path.resolve(here, '../../node_modules/playwright-core');
const target = path.resolve(here, '../src/browser/ai/vendor');
const version = JSON.parse(fs.readFileSync(path.join(core, 'package.json'), 'utf8')).version;
const bundle = fs.readFileSync(path.join(core, 'lib/coreBundle.js'), 'utf8');
const marker = bundle.indexOf('packages/playwright-core/src/generated/injectedScriptSource.ts');
const start = bundle.indexOf("= '", marker) + 2;
if (marker < 0 || start < 2) throw new Error('injected script source not found');
let end = start + 1;
while (bundle[end] !== "'") end += bundle[end] === '\\' ? 2 : 1;
const source = Function(`return ${bundle.slice(start, end + 1)}`)();
if (!source.includes('incrementalAriaSnapshot') || !source.includes('InjectedScript')) throw new Error('unexpected injected script shape');
fs.mkdirSync(target, { recursive: true });
fs.writeFileSync(path.join(target, 'playwright-injected.js'),
  `// Generated from playwright-core ${version} (Apache-2.0). Do not edit; see scripts/vendor_playwright_injected.mjs.\n` +
  `export const PLAYWRIGHT_VERSION = ${JSON.stringify(version)};\nexport default ${JSON.stringify(source)};\n`);
fs.copyFileSync(path.join(core, 'LICENSE'), path.join(target, 'PLAYWRIGHT_LICENSE'));
fs.copyFileSync(path.join(core, 'NOTICE'), path.join(target, 'PLAYWRIGHT_NOTICE'));
console.log(JSON.stringify({ version, chars: source.length }));
