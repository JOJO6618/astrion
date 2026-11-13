import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';
import ts from 'typescript';
import { load } from '../2026-11-13_WindowsElectron/loader.mjs';

const plain = value => JSON.parse(JSON.stringify(value));
const themes = [
  { surfaceColor: '#faf9f5', textColor: '#1f1e1d', chromeColor: '#e8e0d2' },
  { surfaceColor: '#ffffff', textColor: '#171717', chromeColor: '#ebebeb' },
  { surfaceColor: '#212121', textColor: '#eeeeee', chromeColor: '#2b2b2b' }
];
function ownWindow(destroyed = false) {
  return {
    backgrounds: [], overlays: [], rootBackgrounds: [],
    isDestroyed: () => destroyed,
    setBackgroundColor(value) { this.backgrounds.push(value); },
    setTitleBarOverlay(value) { this.overlays.push(plain(value)); },
    contentView: { setBackgroundColor() {} }
  };
}
async function fixture() {
  const platform = await load('platform.js');
  const { setBrowserSurfaceColor } = await load('browser/geometry.js', {
    mocks: {
      '../platform.js': { synthetic: {
        applyPopoutChromeTheme: (window, chrome, text) => platform.applyPopoutChromeTheme(window, chrome, text, 'win32')
      } },
      './text-documents.js': 'export function refreshTextDocuments() {}'
    }
  });
  const windows = [ownWindow(), ownWindow(), ownWindow(true)];
  const manager = {
    window: ownWindow(), surfaceColor: themes[0].surfaceColor,
    textColor: '', chromeColor: '', tabs: new Map(),
    popouts: new Map(windows.map((window, index) => [String(index), { window }]))
  };
  return { setBrowserSurfaceColor, manager, windows, platform };
}

test('classic/light/dark use tabbar and symbol colors in every live popout', async () => {
  const { setBrowserSurfaceColor, manager, windows } = await fixture();
  for (const theme of themes) {
    setBrowserSurfaceColor(manager, theme);
    for (const window of windows.slice(0, 2)) {
      assert.deepEqual(window.overlays.at(-1), { height: 46, color: theme.chromeColor, symbolColor: theme.textColor });
      assert.notEqual(window.overlays.at(-1).color, theme.surfaceColor);
    }
    assert.equal(manager.window.backgrounds.at(-1), theme.surfaceColor);
    assert.equal(manager.window.overlays.length, 0, 'main window controls remain unchanged');
  }
  assert.equal(windows[2].overlays.length, 0, 'closed popouts are skipped');
});

test('chrome-only and text-only changes update native controls even when surface is unchanged', async () => {
  const { setBrowserSurfaceColor, manager, windows } = await fixture();
  setBrowserSurfaceColor(manager, themes[0]);
  setBrowserSurfaceColor(manager, { ...themes[0], chromeColor: '#123456' });
  assert.equal(windows[0].overlays.at(-1).color, '#123456');
  setBrowserSurfaceColor(manager, { ...themes[0], chromeColor: '#123456', textColor: '#654321' });
  assert.equal(windows[0].overlays.at(-1).symbolColor, '#654321');
  assert.equal(windows[0].overlays.length, 3);
  setBrowserSurfaceColor(manager, { ...themes[0], chromeColor: '#123456', textColor: '#654321' });
  assert.equal(windows[0].overlays.length, 3, 'equal themes do not repeat native updates');
});

test('new popouts inherit the cached tabbar color, not the content surface', async () => {
  const { setBrowserSurfaceColor, manager, platform } = await fixture();
  setBrowserSurfaceColor(manager, themes[2]);
  const options = plain(platform.popoutChromeOptions('win32', manager.chromeColor, manager.textColor));
  assert.equal(options.titleBarOverlay.color, themes[2].chromeColor);
  assert.equal(options.titleBarOverlay.symbolColor, themes[2].textColor);
});

test('invalid color fields cannot replace the cached native theme', async () => {
  const { setBrowserSurfaceColor, manager, windows } = await fixture();
  setBrowserSurfaceColor(manager, themes[0]);
  setBrowserSurfaceColor(manager, { ...themes[0], chromeColor: 'url(file:///invalid)', textColor: 'transparent' });
  assert.equal(manager.chromeColor, themes[0].chromeColor);
  assert.equal(manager.textColor, themes[0].textColor);
  assert.equal(windows[0].overlays.length, 1);
});

test('macOS keeps traffic lights and does not call the Windows overlay API', async () => {
  const platform = await load('platform.js');
  const window = ownWindow();
  platform.applyPopoutChromeTheme(window, themes[2].chromeColor, themes[2].textColor, 'darwin');
  assert.equal(window.overlays.length, 0);
  assert.deepEqual(plain(platform.popoutChromeOptions('darwin', themes[2].chromeColor, themes[2].textColor)), {
    titleBarStyle: 'hidden', trafficLightPosition: { x: 15, y: 15 }
  });
});

test('actual renderer composable reports tabbar-only changes through the theme bridge', () => {
  const filename = new URL('../../static/src/components/browser/useBrowserSurface.ts', import.meta.url);
  const source = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
  }).outputText;
  const sent = [];
  let mounted, pending, changed;
  const values = { '--surface-base': themes[0].surfaceColor, '--text-primary': themes[0].textColor, '--chrome-bar-bg': themes[0].chromeColor };
  const dependencies = {
    vue: { onMounted: callback => { mounted = callback; }, onBeforeUnmount() {}, watch() {} },
    '@/stores/nativeBrowser': { useNativeBrowserStore: () => ({ snapshot: null }) },
    './types': { browserBridge: () => ({ theme: value => sent.push(plain(value)), onGeometryRequest() {} }) },
    './useBrowserLayer': { useBrowserLayer: () => ({ set() {}, start() {} }) },
    './browserOcclusions': { browserOcclusions() {} }
  };
  const context = {
    exports: {}, require: name => { assert.ok(dependencies[name], name); return dependencies[name]; },
    getComputedStyle: () => ({ getPropertyValue: name => values[name] || '' }),
    document: { documentElement: {}, body: {}, addEventListener() {} },
    window: { addEventListener() {} },
    requestAnimationFrame: callback => { pending = callback; return 1; },
    ResizeObserver: class { observe() {} },
    MutationObserver: class { constructor(callback) { changed = callback; } observe() {} }
  };
  vm.runInNewContext(source, context);
  context.exports.useBrowserSurface({ value: null }, () => false, () => false);
  mounted(); pending();
  assert.deepEqual(sent.at(-1), themes[0]);
  values['--chrome-bar-bg'] = themes[1].chromeColor;
  changed(); pending();
  assert.equal(sent.length, 2);
  assert.equal(sent[1].chromeColor, themes[1].chromeColor);
  assert.equal(sent[1].surfaceColor, themes[0].surfaceColor);
});
