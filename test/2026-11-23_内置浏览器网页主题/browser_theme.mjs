// 运行：./desktop-electron/node_modules/.bin/electron test/2026-11-23_内置浏览器网页主题/browser_theme.mjs
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { app, nativeTheme } from 'electron';
import { createMainWindow, getActiveView } from '../../desktop-electron/src/window.js';

const userData = mkdtempSync(join(tmpdir(), 'astrion-browser-theme-'));
app.setPath('userData', userData);
app.setPath('sessionData', userData);
writeFileSync(join(userData, 'theme.json'), JSON.stringify({ theme: 'dark' }));
writeFileSync(join(userData, 'window-surface.json'), JSON.stringify({ color: '#232323' }));

const csp = "default-src 'none'; style-src 'none'";
const server = createServer((request, response) => {
  const pathname = new URL(request.url, 'http://127.0.0.1').pathname;
  if (pathname === '/api/session-status') {
    response.setHeader('Content-Type', 'application/json');
    response.end(JSON.stringify({ success: true, session: { logged_in: true, username: 'browser-theme-test' } }));
    return;
  }
  if (pathname === '/json') {
    response.setHeader('Content-Type', 'application/json');
    response.setHeader('Content-Security-Policy', csp);
    response.end('{"themeTest":true}');
    return;
  }
  response.setHeader('Content-Type', 'text/html; charset=utf-8');
  const page = {
    '/chrome': '<script>window.startedDark = matchMedia("(prefers-color-scheme: dark)").matches</script>',
    '/plain': '<pre>Plain text in HTML</pre>',
    '/adaptive': '<meta name="color-scheme" content="light dark"><style>@media (prefers-color-scheme: dark) { html { --mode: dark; } }</style><p>Adaptive website</p>',
    '/styled': '<style>body { background: rgb(245, 230, 210); color: rgb(10, 20, 30); }</style><p>Website colors</p>',
  }[pathname];
  response.end(`<!doctype html><meta charset="utf-8">${page || '<p>Astrion fixture</p>'}`);
});

async function until(read, ready, label) {
  let result;
  for (let attempt = 0; attempt < 100; attempt++) {
    result = await read();
    if (ready(result)) return result;
    await delay(40);
  }
  throw new Error(`${label}: ${JSON.stringify(result)}`);
}

const inspect = contents => contents.executeJavaScript(`(() => {
  const root = getComputedStyle(document.documentElement);
  const text = getComputedStyle(document.querySelector('pre, p') || document.body);
  return {
    type: document.contentType,
    dark: matchMedia('(prefers-color-scheme: dark)').matches,
    light: matchMedia('(prefers-color-scheme: light)').matches,
    rootBackground: root.backgroundColor,
    bodyBackground: getComputedStyle(document.body).backgroundColor,
    text: text.color,
    mode: root.getPropertyValue('--mode').trim(),
    styleNodes: document.querySelectorAll('style').length,
  };
})()`);

async function pixel(contents) {
  const bitmap = (await contents.capturePage({ x: 100, y: 90, width: 1, height: 1 })).toBitmap();
  assert.ok(bitmap.length >= 4, 'Chromium returned a rendered pixel');
  return [bitmap[2], bitmap[1], bitmap[0], bitmap[3]];
}

function assertOpaquePixel(actual, expected, label) {
  // 截图位图可能经过显示器色彩空间转换，CSS 颜色另由 computed style 精确核对。
  assert.ok(actual[3] === 255 && expected.every((channel, i) => Math.abs(channel - actual[i]) <= 4),
    `${label}: expected ${expected}, got ${actual}`);
}

async function run() {
  await new Promise((resolve, reject) => server.listen(0, '127.0.0.1', resolve).once('error', reject));
  const port = server.address().port;
  const origin = `http://127.0.0.1:${port}`;
  const window = createMainWindow(port);
  const chrome = window.contentView.children[0].webContents;
  assert.equal(await chrome.executeJavaScript('window.startedDark'), true, 'saved dark preference applied to the first renderer');
  const key = 'new:browser-theme-test';
  const snapshot = { revision: 0, active_key: key, tabs: [{ key, kind: 'new', workspace_id: 'browser-theme-test' }] };
  await chrome.executeJavaScript(`astrionShell.forwardTabsState(${JSON.stringify(snapshot)})`);
  const host = await until(() => getActiveView()?.webContents, Boolean, 'active content view');
  const scope = { mode: 'agent', workspace: 'browser-theme-test', conversation: 'conv_20261123_170402_305' };
  const { lease } = await host.executeJavaScript(`astrionBrowser.connect(${JSON.stringify(scope)})`);
  let sequence = 0;
  const layout = () => host.executeJavaScript(`astrionBrowser.layout(${JSON.stringify({ lease, sequence: ++sequence, visible: true, rect: { x: 420, y: 80, width: 600, height: 500 } })})`);
  const command = (action, value, id) => host.executeJavaScript(`astrionBrowser.command(${JSON.stringify({ lease, action, value, id })})`);
  const open = async pathname => {
    await command('new', origin + pathname);
    await layout();
    return until(() => window.contentView.children.find(view => view.webContents?.getURL() === origin + pathname && !view.webContents.isLoading())?.webContents, Boolean, pathname);
  };
  const setTheme = (key, surfaceColor, textColor) => host.executeJavaScript(`
    astrionWindow.theme(${JSON.stringify(key)});
    astrionWindow.surface(${JSON.stringify(surfaceColor)});
    astrionBrowser.theme(${JSON.stringify({ surfaceColor, textColor })});
  `);

  await setTheme('dark', '#232323', '#ffffff');
  const plain = await open('/plain');
  assert.deepEqual(await inspect(plain), {
    type: 'text/html', dark: true, light: false, rootBackground: 'rgba(0, 0, 0, 0)',
    bodyBackground: 'rgba(0, 0, 0, 0)', text: 'rgb(0, 0, 0)', mode: '', styleNodes: 0,
  });
  assert.deepEqual(await pixel(plain), [255, 255, 255, 255], 'unstyled HTML keeps Chromium’s light canvas');
  console.log('PASS saved dark preference and legible default canvas for unstyled HTML');

  const adaptive = await open('/adaptive');
  assert.equal((await inspect(adaptive)).mode, 'dark', 'site media query receives dark');
  const adaptivePixel = await pixel(adaptive);
  assert.ok(adaptivePixel.slice(0, 3).every(value => value < 80) && adaptivePixel[3] === 255, `opted-in dark canvas: ${adaptivePixel}`);
  const styled = await open('/styled');
  const styledColors = await inspect(styled);
  assert.equal(styledColors.text, 'rgb(10, 20, 30)');
  assert.equal(styledColors.bodyBackground, 'rgb(245, 230, 210)');
  assertOpaquePixel(await pixel(styled), [245, 230, 210], 'website background is preserved');
  console.log('PASS adaptive canvas and website-authored colors');

  const json = await open('/json');
  const jsonDark = await until(() => inspect(json), styles => styles.text === 'rgb(255, 255, 255)', 'strict-CSP JSON in dark');
  assert.equal(jsonDark.type, 'application/json');
  assert.equal(jsonDark.rootBackground, 'rgb(35, 35, 35)');
  assert.equal(jsonDark.styleNodes, 0);
  assertOpaquePixel(await pixel(json), [35, 35, 35], 'JSON theme background');
  const violation = await json.executeJavaScript(`new Promise(resolve => {
    const timeout = setTimeout(() => resolve(''), 1000);
    document.addEventListener('securitypolicyviolation', event => {
      clearTimeout(timeout);
      resolve(event.effectiveDirective);
    }, { once: true });
    const style = document.createElement('style');
    style.textContent = 'body { color: red !important }';
    document.head.appendChild(style);
  })`);
  assert.equal(violation, 'style-src-elem', 'the response can still block style tags');
  console.log('PASS strict-CSP JSON is readable in dark with CSP still enforced');

  for (const [key, surface, foreground, background, text] of [
    ['classic', '#faf9f5', '#141413', 'rgb(250, 249, 245)', 'rgb(20, 20, 19)'],
    ['light', '#ffffff', '#171717', 'rgb(255, 255, 255)', 'rgb(23, 23, 23)'],
    ['dark', '#232323', '#ffffff', 'rgb(35, 35, 35)', 'rgb(255, 255, 255)'],
  ]) {
    await setTheme(key, surface, foreground);
    const dark = key === 'dark';
    await until(() => inspect(json), value => value.rootBackground === background && value.text === text && value.dark === dark, `${key} JSON palette and preference`);
    assert.equal(nativeTheme.themeSource, dark ? 'dark' : 'light');
    await until(() => inspect(adaptive), value => value.dark === dark && value.mode === (dark ? 'dark' : ''), `${key} on open website`);
    assert.equal((await inspect(plain)).text, 'rgb(0, 0, 0)');
    console.log(`PASS ${key} propagates live to existing website and JSON tabs`);
  }
  await command('navigate', origin + '/plain');
  await until(() => inspect(json), value => value.type === 'text/html' && value.rootBackground === 'rgba(0, 0, 0, 0)', 'navigated from JSON to HTML');
  assert.deepEqual(await pixel(json), [255, 255, 255, 255]);
  console.log('PASS navigation from JSON restores the normal canvas');
}

app.whenReady().then(run).then(() => {
  server.close();
  app.exit(0);
}).catch(error => {
  console.error(error);
  server.close();
  app.exit(1);
});
