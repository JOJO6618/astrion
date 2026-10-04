import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { entryStyles, quickDocument } from '../../desktop-electron/src/quick/entry-styles.js';
import { QuickVisibility } from '../../desktop-electron/src/quick/visibility.js';

test('shared component CSS is loaded before entry overrides and deduplicated', () => {
  const manifest = { 'static/src/quick.ts': { imports: ['shared', 'avatar'], css: ['assets/quick.css'] },
    shared: { imports: ['avatar'], css: ['assets/shared.css'] }, avatar: { css: ['assets/avatar.css'] } };
  assert.deepEqual(entryStyles(manifest), ['assets/avatar.css', 'assets/shared.css', 'assets/quick.css']);
  assert.throws(() => entryStyles({}), /Missing build manifest chunk/);
});

test('the actual quick document loads background, eye and icon styles from the Vite dependency graph', async () => {
  const manifest = JSON.parse(await fs.readFile('static/dist/.vite/manifest.json', 'utf8'));
  const styles = entryStyles(manifest);
  const html = quickDocument(await fs.readFile('static/quick.html', 'utf8'), manifest);
  for (const css of styles) assert.ok(html.includes(`href="/static/dist/${css}"`));
  const loadedCSS = (await Promise.all(styles.map(css => fs.readFile(`static/dist/${css}`, 'utf8')))).join('\n');
  for (const selector of ['.sa-bg', '.sa-eye', '.sa-icon']) assert.ok(loadedCSS.includes(selector), selector);
});

function fixture(context) {
  const events = [];
  const window = { show: () => events.push('show'), focus: () => events.push('focus'), hide: () => events.push('hide'),
    isDestroyed: () => false, webContents: { send: type => events.push(type) } };
  const visibility = new QuickVisibility(window, () => events.push('capture-close'));
  context.after(() => visibility.dispose());
  return { events, visibility };
}

test('closing starts a renderer animation and hides only after it finishes', context => {
  const { events, visibility } = fixture(context);
  visibility.show(); visibility.hide();
  assert.ok(events.includes('quick:will-hide'));
  assert.ok(events.includes('capture-close'));
  assert.ok(!events.includes('hide'));
  visibility.finish();
  assert.equal(events.at(-1), 'hide');
});

test('a stale close-animation acknowledgement cannot hide a re-summoned window', context => {
  const { events, visibility } = fixture(context);
  visibility.show(); visibility.hide(); visibility.show(); visibility.finish();
  assert.equal(visibility.requested, true);
  assert.ok(!events.includes('hide'));
});
