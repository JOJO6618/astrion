import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { test } from 'node:test';

const root = fileURLToPath(new URL('../../', import.meta.url));
const require = createRequire(path.join(root, 'desktop-electron/package.json'));
const asar = require('@electron/asar');
const archive = path.join(root, '_experiments/macos-browser-20261109-ai/mac-arm64/Astrion.app/Contents/Resources/app.asar');
const extracted = fs.mkdtempSync(path.join(root, '_experiments/browser-theme-switch-'));
fs.writeFileSync(path.join(extracted, 'package.json'), '{"type":"module"}');
for (const file of ['geometry.js', 'policy.js']) {
  fs.writeFileSync(path.join(extracted, file), asar.extractFile(archive, `src/browser/${file}`));
}
const { setBrowserSurfaceColor } = await import(pathToFileURL(path.join(extracted, 'geometry.js')).href);
console.log(`Testing packaged geometry: ${archive}`);

function manager(tabs) {
  const paints = [];
  return {
    surfaceColor: '#faf9f5', paints,
    window: {
      setBackgroundColor: color => paints.push(['window', color]),
      contentView: { setBackgroundColor: color => paints.push(['root', color]) }
    },
    tabs: new Map(tabs.map((tab, index) => [String(index), tab]))
  };
}

for (const [name, tab] of [
  ['internal browser settings page without a view', { section: 'settings' }],
  ['suspended webpage with a null view', { suspended: true, view: null }]
]) {
  test(`packaged theme switching tolerates ${name}`, () => {
    const state = manager([tab]);
    for (const color of ['#1a1a1a', '#ffffff', '#faf9f5', '#1a1a1a']) {
      assert.doesNotThrow(() => setBrowserSurfaceColor(state, color));
      assert.equal(state.surfaceColor, color);
      assert.deepEqual(state.paints.slice(-2), [['window', color], ['root', color]]);
    }
  });
}

test('packaged theme switching still paints live webpages and skips destroyed views', () => {
  const paints = [];
  const state = manager([
    { section: 'settings' }, { suspended: true, view: null },
    { view: { webContents: { isDestroyed: () => true }, setBackgroundColor: () => assert.fail('Destroyed view painted') } },
    { view: { webContents: { isDestroyed: () => false }, setBackgroundColor: color => paints.push(color) } }
  ]);
  setBrowserSurfaceColor(state, '#1A1A1A');
  assert.deepEqual(paints, ['#1a1a1a']);
});

test('packaged window records the theme before browser view updates', () => {
  const source = asar.extractFile(archive, 'src/window.js').toString();
  const start = source.indexOf('function applySurfaceColor(');
  const end = source.indexOf("ipcMain.on('window:surface'", start);
  const apply = source.slice(start, end);
  assert.ok(start >= 0 && end > start);
  const save = apply.indexOf('fs.promises.writeFile(');
  const views = apply.indexOf('setBrowserSurfaceColor(');
  assert.ok(save >= 0 && views > save, 'Persist theme before updating views');
});
