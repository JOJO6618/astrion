// 回归：内置浏览器在深色主题下打开纯文本类响应（text/plain、json、脚本源码）时，
// 文档背景透明 + 默认黑字 → 透出深色视图底色，变成「深底黑字」不可读。
// 修复：dom-ready 后判定文本渲染文档并注入跟随主题的样式；普通 HTML 页面不受影响。
// 运行：cd <仓库根> && ./desktop-electron/node_modules/.bin/electron test/2026-11-10_内置浏览器纯文本主题/text_document_theme.mjs

import { app, BaseWindow, WebContentsView } from 'electron';
import {
  textDocumentScript,
  applyTextDocumentTheme
} from '../../desktop-electron/src/browser/text-documents.js';

const BG = '#1a1a1a';
const results = [];
const check = (name, ok, detail) => {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name} ${detail}`);
};

app.whenReady().then(async () => {
  const win = new BaseWindow({ width: 640, height: 400, show: false, backgroundColor: BG });
  const view = new WebContentsView({ webPreferences: { sandbox: true, contextIsolation: true } });
  view.setBounds({ x: 0, y: 0, width: 640, height: 400 });
  view.setBackgroundColor(BG);
  win.contentView.addChildView(view);
  const wc = view.webContents;
  const manager = { surfaceColor: '#1a1a1a', textColor: '#ffffff', tabs: new Map() };
  const tab = { view, textDocument: false };
  manager.tabs.set('t', tab);

  const style = () => wc.executeJavaScript(`(() => {
    const html = getComputedStyle(document.documentElement), body = getComputedStyle(document.body);
    return { bg: html.backgroundColor, color: body.color, scheme: html.colorScheme,
             styleNodes: document.querySelectorAll('style').length };
  })()`);

  const cases = [
    ['text/plain', 'data:text/plain;charset=utf-8,' + encodeURIComponent('const a = 1;\n// 中文注释\n' + 'x'.repeat(40)), true],
    ['application/javascript', 'data:application/javascript,' + encodeURIComponent('const a = 1;\n' + 'y'.repeat(40)), true],
    ['application/json', 'data:application/json,' + encodeURIComponent('{"a":1,"b":[1,2,3]}'), true],
    ['text/html', 'data:text/html,<html><body><pre>html pre</pre></body></html>', false]
  ];

  for (const [label, url, expected] of cases) {
    await wc.loadURL(url);
    await new Promise(resolve => setTimeout(resolve, 250));
    await applyTextDocumentTheme(manager, tab);
    const s = await style();
    if (expected) {
      check(`${label} 命中并注入`,
        tab.textDocument === true && s.bg === 'rgb(26, 26, 26)' && s.color === 'rgb(255, 255, 255)' && s.scheme === 'dark',
        JSON.stringify(s));
    } else {
      check(`${label} 不受影响`,
        tab.textDocument === false && s.bg === 'rgba(0, 0, 0, 0)' && s.styleNodes === 0,
        JSON.stringify(s));
    }
  }

  // 主题切到浅色：同一文本页必须跟随更新
  await wc.loadURL(cases[0][1]);
  await new Promise(resolve => setTimeout(resolve, 250));
  await applyTextDocumentTheme(manager, tab);
  manager.surfaceColor = '#faf9f5';
  manager.textColor = '#141413';
  await applyTextDocumentTheme(manager, tab);
  const light = await style();
  check('浅色主题跟随',
    light.bg === 'rgb(250, 249, 245)' && light.color === 'rgb(20, 20, 19)' && light.scheme === 'light',
    JSON.stringify(light));

  // 前端没上报文字色时按背景明暗兜底
  manager.surfaceColor = '#1a1a1a';
  manager.textColor = '';
  await applyTextDocumentTheme(manager, tab);
  const fallback = await style();
  check('缺文字色兜底', fallback.color === 'rgb(255, 255, 255)', JSON.stringify(fallback));

  // 生成脚本必须是可解析的 JS，且不夹带未替换的模板残留
  const script = textDocumentScript(manager);
  check('脚本模板完整', script.includes('__astrion_text_document_theme') && !script.includes('undefined'), '');

  const failed = results.filter(row => !row.ok);
  console.log(`SUMMARY ${results.length - failed.length}/${results.length} passed`);
  win.destroy();
  app.quit();
  process.exitCode = failed.length ? 1 : 0;
});
