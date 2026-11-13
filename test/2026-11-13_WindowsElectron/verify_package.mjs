import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import asar from '../../desktop-electron/node_modules/@electron/asar/lib/asar.js';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const desktop = path.join(root, 'desktop-electron');
const resources = path.join(root, 'output/windows-electron/build/win-unpacked/resources');
const archive = path.join(resources, 'app.asar');
const failures = [], matched = [];
function walk(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const file = path.join(directory, entry.name);
    if (entry.isDirectory() && entry.name !== '__pycache__') walk(file);
    else if (entry.isFile() && !file.endsWith('.pyc')) {
      const relative = path.relative(desktop, file);
      try {
        if (fs.readFileSync(file).equals(asar.extractFile(archive, relative))) matched.push(relative);
        else failures.push(`different source: ${relative}`);
      } catch (error) { failures.push(`missing source: ${relative}: ${error.message}`); }
    }
  }
}
walk(path.join(desktop, 'src'));
const pairs = [
  ['native helper', path.join(desktop, '.cache/astrion-windows-helper.exe'), path.join(resources, 'astrion-windows-helper.exe')],
  ['frontend manifest', path.join(root, 'static/dist/.vite/manifest.json'), path.join(resources, 'runtime/backend/static/dist/.vite/manifest.json')],
  ['Windows import worker', path.join(root, 'desktop/src-tauri/src/browser/chrome_worker.py'), path.join(resources, 'chrome-import/windows-worker.py')]
];
for (const [name, source, target] of pairs) {
  if (!fs.existsSync(target) || !fs.readFileSync(source).equals(fs.readFileSync(target))) failures.push(`different ${name}`);
}
const metadata = JSON.parse(asar.extractFile(archive, 'package.json').toString());
const release = JSON.parse(fs.readFileSync(path.join(desktop, 'windows-release.json'), 'utf8'));
if (metadata.version !== release.version) failures.push('wrong Windows version');
if (metadata.main !== 'src/main.js') failures.push('production package uses test bootstrap');
const result = { success: failures.length === 0, sourceFilesMatched: matched.length, version: metadata.version, main: metadata.main, failures };
fs.writeFileSync(path.join(root, 'output/windows-electron/package-source-verification.json'), JSON.stringify(result, null, 2));
console.log(JSON.stringify(result, null, 2));
process.exitCode = result.success ? 0 : 1;
