// Check the changed Vue source without treating the Windows checkout's existing
// CRLF line endings as newly introduced formatter errors. No rules are disabled
// and no source/config file is rewritten. Full-repository lint is logged apart.
import { ESLint } from 'eslint';
import fs from 'node:fs';
const file = 'static/src/components/browser/BrowserWindowApp.vue';
const results = await new ESLint().lintText(fs.readFileSync(file, 'utf8').replaceAll('\r\n', '\n'), { filePath: file });
const result = { file, normalization: 'CRLF to LF in memory only', errors: results[0].errorCount, warnings: results[0].warningCount, messages: results[0].messages };
fs.writeFileSync('output/windows-electron/frontend-changed-lint.json', JSON.stringify(result, null, 2));
console.log(JSON.stringify(result, null, 2));
process.exitCode = result.errors || result.warnings ? 1 : 0;
