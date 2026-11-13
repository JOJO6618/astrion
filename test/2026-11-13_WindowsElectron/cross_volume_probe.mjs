import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const source = path.join(root, 'output/windows-electron', `rename-probe-${process.pid}`);
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'astrion-rename-probe-'));
const destination = path.join(temporary, 'probe');
fs.writeFileSync(source, 'synthetic cross-volume probe');
let result;
try {
  fs.renameSync(source, destination);
  result = { sameVolume: path.parse(source).root.toLowerCase() === path.parse(destination).root.toLowerCase(), moved: true };
} catch (error) {
  result = { sourceVolume: path.parse(source).root, destinationVolume: path.parse(destination).root, moved: false, code: error.code };
} finally {
  fs.rmSync(source, { force: true }); fs.rmSync(destination, { force: true }); fs.rmdirSync(temporary);
}
fs.writeFileSync(path.join(root, 'output/windows-electron/cross-volume-probe.json'), JSON.stringify(result, null, 2));
console.log(JSON.stringify(result, null, 2));
