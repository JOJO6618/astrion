#!/usr/bin/env node
// Synthetic DOM/WebView2 contract tests. Run: node test/2026-11-09_WindowsBrowser/test_ui.cjs
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

// ────────────────────────── 定位仓库根 ──────────────────────────
function isRepoRoot(dir) {
  return (
    fs.existsSync(path.join(dir, 'package.json')) &&
    fs.existsSync(path.join(dir, 'static', 'src', 'components', 'browser', 'browserOcclusions.ts')) &&
    fs.existsSync(path.join(dir, 'desktop', 'src-tauri', 'src', 'browser', 'main-bridge.js'))
  );
}
function findRepoRoot(start) {
  if (process.env.ASTRION_REPO) return path.resolve(process.env.ASTRION_REPO);
  let dir = path.resolve(start);
  for (let i = 0; i < 12; i += 1) {
    if (isRepoRoot(dir)) return dir;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return null;
}

const REPO = findRepoRoot(__dirname);
if (!REPO) {
  console.error('未找到仓库根（需要 package.json + static/src/components/browser/browserOcclusions.ts + desktop/src-tauri/src/browser/main-bridge.js）');
  console.error('可显式指定：ASTRION_REPO=<仓库根> node ' + path.basename(__filename));
  process.exit(1);
}
const TS_ENTRY = path.join(REPO, 'static', 'src', 'components', 'browser', 'browserOcclusions.ts');
const BRIDGE = path.join(REPO, 'desktop', 'src-tauri', 'src', 'browser', 'main-bridge.js');

// ────────────────────────── harness ──────────────────────────
const results = [];
const asyncQueue = [];
function record(name, okFlag, error) {
  results.push({ name, ok: okFlag, error });
  if (okFlag) console.log('PASS  ' + name);
  else console.log('FAIL  ' + name + '\n        ' + (error && error.message ? error.message : error));
}
function test(name, fn) {
  try {
    const out = fn();
    if (out && typeof out.then === 'function') throw new Error('同步用例不能返回 Promise，请用 asyncTest');
    record(name, true);
  } catch (error) {
    record(name, false, error);
  }
}
/** 异步用例先入队，等同步用例跑完后串行执行（保证输出顺序与汇总时机正确） */
function asyncTest(name, fn) {
  asyncQueue.push([name, fn]);
}
function eq(actual, expected, label) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) throw new Error((label || '值不相等') + '\n        实际: ' + a + '\n        期望: ' + e);
}
function ok(value, label) {
  if (!value) throw new Error(label || '断言失败');
}

// ────────────────────────── TypeScript ──────────────────────────
let ts;
try {
  ts = require('typescript');
} catch (error) {
  console.error('未找到 typescript（仓库根 devDependencies 提供）。本脚本不安装依赖，请先 npm install。');
  process.exit(1);
}
function transpile(file) {
  const source = fs.readFileSync(file, 'utf8');
  const out = ts.transpileModule(source, {
    fileName: file,
    reportDiagnostics: true,
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, removeComments: false }
  });
  const errors = (out.diagnostics || []).filter(d => d.category === ts.DiagnosticCategory.Error);
  if (errors.length) {
    throw new Error('转译报错: ' + errors.map(d => ts.flattenDiagnosticMessageText(d.messageText, ' ')).join('; '));
  }
  return out.outputText;
}
function evaluate(code, sandbox, filename) {
  const module = { exports: {} };
  sandbox.module = module;
  sandbox.exports = module.exports;
  sandbox.globalThis = sandbox;
  vm.runInNewContext(code, sandbox, { filename });
  return module.exports;
}

// ────────────────────────── fake DOM ──────────────────────────
function rectOf(left, top, right, bottom) {
  return { left, top, right, bottom, x: left, y: top, width: right - left, height: bottom - top };
}
function fakeElement(spec) {
  return {
    __name: spec.name || 'el',
    getBoundingClientRect: () => spec.rect,
    getClientRects: () => (spec.detached ? [] : [spec.rect]),
    contains: target => (spec.contains === true ? true : typeof spec.contains === 'function' ? spec.contains(target) : false),
    style: spec.style || {}
  };
}
function domSandbox(candidates) {
  const document = {
    querySelectorAll(selector) {
      if (selector !== '.stub') throw new Error('意外的选择器: ' + selector);
      return candidates;
    }
  };
  const getComputedStyle = el => {
    const style = el.style || {};
    return {
      visibility: style.visibility === undefined ? 'visible' : style.visibility,
      display: style.display === undefined ? 'block' : style.display,
      opacity: style.opacity === undefined ? '1' : style.opacity
    };
  };
  return { document, getComputedStyle, console };
}
const OCCLUSIONS_CODE = transpile(TS_ENTRY);
function runOcclusions(candidates, surface) {
  const exportsObj = evaluate(OCCLUSIONS_CODE, domSandbox(candidates), 'browserOcclusions.ts');
  ok(typeof exportsObj.browserOcclusions === 'function', 'browserOcclusions 未导出（键: ' + Object.keys(exportsObj).join(',') + '）');
  return exportsObj.browserOcclusions(surface, '.stub');
}
const surfaceAt = () => fakeElement({ name: 'surface', rect: rectOf(100, 100, 500, 400) });

// ────────────────────────── A. browserOcclusions.ts ──────────────────────────
console.log('\n# static/src/components/browser/browserOcclusions.ts');

test('相交裁剪：候选部分越出 surface 时按交集裁剪', () => {
  eq(
    runOcclusions([fakeElement({ name: 'menu', rect: rectOf(400, 300, 600, 500) })], surfaceAt()),
    [{ x: 400, y: 300, width: 100, height: 100 }],
    '交集矩形'
  );
});

test('相交裁剪：候选完全在 surface 内时原样返回', () => {
  eq(
    runOcclusions([fakeElement({ name: 'popover', rect: rectOf(150, 120, 250, 220) })], surfaceAt()),
    [{ x: 150, y: 120, width: 100, height: 100 }],
    '内部矩形'
  );
});

test('相交裁剪：候选完全在 surface 外被丢弃', () => {
  eq(runOcclusions([fakeElement({ name: 'outside', rect: rectOf(700, 600, 800, 700) })], surfaceAt()), [], '不应产生裁剪');
});

test('相交裁剪：紧贴边缘（right==x / bottom==y）零面积被丢弃', () => {
  eq(
    runOcclusions(
      [fakeElement({ name: 'touchLeft', rect: rectOf(0, 200, 100, 300) }), fakeElement({ name: 'touchTop', rect: rectOf(200, 0, 300, 100) })],
      surfaceAt()
    ),
    [],
    '零面积交集不应产生裁剪'
  );
});

test('跳过：visibility:hidden / display:none / opacity 0（"0"、"0.0"、数值 0）', () => {
  eq(
    runOcclusions(
      [
        fakeElement({ name: 'hidden', rect: rectOf(200, 200, 300, 300), style: { visibility: 'hidden' } }),
        fakeElement({ name: 'none', rect: rectOf(200, 200, 300, 300), style: { display: 'none' } }),
        fakeElement({ name: 'zero', rect: rectOf(200, 200, 300, 300), style: { opacity: '0' } }),
        fakeElement({ name: 'zeroFloat', rect: rectOf(200, 200, 300, 300), style: { opacity: '0.0' } }),
        fakeElement({ name: 'zeroNumber', rect: rectOf(200, 200, 300, 300), style: { opacity: 0 } })
      ],
      surfaceAt()
    ),
    [],
    '隐藏/全透明候选必须不产生裁剪'
  );
});

test('跳过：opacity 0.5 仍参与裁剪（只排除严格 0）', () => {
  eq(
    runOcclusions([fakeElement({ name: 'half', rect: rectOf(150, 150, 250, 250), style: { opacity: '0.5' } })], surfaceAt()),
    [{ x: 150, y: 150, width: 100, height: 100 }],
    '0.5 不透明度应照常裁剪'
  );
});

test('跳过：候选包含 surface（candidate.contains(surface)）', () => {
  eq(runOcclusions([fakeElement({ name: 'wrapper', rect: rectOf(0, 0, 1000, 1000), contains: true })], surfaceAt()), [], '包住 surface 的候选必须跳过');
});

test('跳过：surface 自身 + 脱离文档的候选（getClientRects 为空）', () => {
  const surface = surfaceAt();
  const detached = fakeElement({ name: 'detached', rect: rectOf(120, 120, 220, 220), detached: true });
  const exportsObj = evaluate(OCCLUSIONS_CODE, domSandbox([surface, detached]), 'browserOcclusions.ts');
  eq(exportsObj.browserOcclusions(surface, '.stub'), [], '自身与脱离文档的候选都应跳过');
});

function manyCandidates(count, hiddenBelow) {
  const list = [];
  for (let i = 0; i < count; i += 1) {
    const left = 100 + (i % 20) * 5;
    const top = 100 + Math.floor(i / 20) * 5;
    const hidden = hiddenBelow !== undefined && i < hiddenBelow;
    list.push(fakeElement({ name: 'cut' + i, rect: rectOf(left, top, left + 4, top + 4), style: hidden ? { display: 'none' } : {} }));
  }
  return list;
}

test('128 上限：127 个遮挡时正常返回 127 条', () => {
  const cuts = runOcclusions(manyCandidates(127), surfaceAt());
  eq(cuts.length, 127, '应返回 127 条裁剪');
  ok(cuts.every(c => c.width > 0 && c.height > 0), '每条裁剪必须有正面积');
});

test('128 上限：第 128 个遮挡触发整块遮挡（单条 == surface 全矩形）', () => {
  eq(runOcclusions(manyCandidates(200), surfaceAt()), [{ x: 100, y: 100, width: 400, height: 300 }], '第 128 条必须回退成整块遮挡');
});

test('128 上限：隐藏候选不占名额（190 隐藏 + 10 可见 → 10 条）', () => {
  eq(runOcclusions(manyCandidates(200, 190), surfaceAt()).length, 10, '隐藏候选不得占用 128 名额');
});

// ────────────────────────── B. main-bridge.js ──────────────────────────
console.log('\n# desktop/src-tauri/src/browser/main-bridge.js');

const BRIDGE_SOURCE = fs.readFileSync(BRIDGE, 'utf8');

function makeTimers() {
  let seq = 0;
  const timers = new Map();
  return {
    timers,
    setTimeout(fn, ms) {
      seq += 1;
      timers.set(seq, { fn, ms });
      return seq;
    },
    clearTimeout(id) {
      timers.delete(id);
    }
  };
}

/**
 * 构造最小 WebView2/window 环境并执行 main-bridge.js。
 * topLevel:false → window !== window.top；withWebview:false → 无 chrome.webview。
 */
function installBridge(options) {
  const opts = options || {};
  const webviewHandlers = [];
  const posted = [];
  const windowHandlers = new Map();
  const timers = makeTimers();
  let uuid = 0;

  const chromeWebview = {
    addEventListener(type, handler) {
      if (type === 'message') webviewHandlers.push(handler);
    },
    postMessage(message) {
      posted.push(message);
    }
  };

  const win = {
    chrome: opts.withWebview === false ? {} : { webview: chromeWebview },
    addEventListener(type, handler) {
      if (!windowHandlers.has(type)) windowHandlers.set(type, []);
      windowHandlers.get(type).push(handler);
    }
  };
  win.top = opts.topLevel === false ? {} : win;

  const sandbox = {
    window: win,
    chrome: win.chrome,
    crypto: { randomUUID: () => 'doc-' + String(++uuid) },
    setTimeout: timers.setTimeout,
    clearTimeout: timers.clearTimeout,
    Promise,
    Error,
    JSON,
    Set,
    Map,
    Object,
    Reflect,
    String,
    Number,
    console,
    addEventListener: win.addEventListener
  };
  vm.runInNewContext(BRIDGE_SOURCE, sandbox, { filename: 'main-bridge.js' });

  return {
    window: win,
    posted,
    timers,
    emitWebview(data) {
      for (const handler of webviewHandlers) handler({ data });
    },
    emitWindow(type) {
      for (const handler of (windowHandlers.get(type) || []).slice()) handler({ type });
    }
  };
}

const flush = () => new Promise(resolve => setImmediate(resolve));
const lastPosted = env => JSON.parse(env.posted[env.posted.length - 1]);

asyncTest('只顶层安装：window !== window.top 时不暴露 astrionBrowser', async () => {
  const env = installBridge({ topLevel: false });
  ok(env.window.astrionBrowser === undefined, '子框架不应安装桥');
  eq(env.posted, [], '子框架不应产生任何 postMessage');
  eq(env.timers.timers.size, 0, '子框架不应注册定时器');
});

asyncTest('只顶层安装：缺少 chrome.webview 时不暴露 astrionBrowser', async () => {
  const env = installBridge({ withWebview: false });
  ok(env.window.astrionBrowser === undefined, '无 WebView2 环境不应安装桥');
});

asyncTest('顶层安装：对象存在、被冻结、属性不可替换', async () => {
  const env = installBridge({});
  const bridge = env.window.astrionBrowser;
  ok(bridge && typeof bridge === 'object', '应暴露 astrionBrowser');
  ok(Object.isFrozen(bridge), 'astrionBrowser 必须 Object.freeze');
  for (const key of ['connect', 'theme', 'command', 'credentials', 'layout', 'input', 'onState', 'onShortcut', 'onContextMenu', 'onMessage', 'onGeometryRequest', 'onCredentialProgress']) {
    ok(typeof bridge[key] === 'function', '缺少方法 ' + key);
  }
  eq(bridge.surfaceMode, 'regions', 'surfaceMode（native layout 依赖 regions 契约）');
  // 本文件是 strict 模式，直接赋值会抛 TypeError，故用 Reflect.set 观察「写入被拒」
  eq(Reflect.set(env.window, 'astrionBrowser', null), false, '属性不可写：Reflect.set 应返回 false');
  ok(env.window.astrionBrowser === bridge, '赋值后必须仍是原对象');
});

asyncTest('post 字符串：只发 JSON 字符串，字段与请求对应', async () => {
  const env = installBridge({});
  const done = env.window.astrionBrowser.command({ action: 'reload' });
  eq(env.posted.length, 1, '一次调用应只 post 一条');
  ok(typeof env.posted[0] === 'string', 'postMessage 必须发字符串');
  const message = JSON.parse(env.posted[0]);
  eq(message.bridge, 'astrion-browser-v1', 'bridge 标记');
  eq(message.type, 'command', 'type');
  eq(message.data, { action: 'reload' }, 'data');
  ok(typeof message.document === 'string' && message.document.length > 0, 'document 非空');
  ok(message.id.startsWith(message.document + ':'), 'id 应以 document 为前缀');
  env.emitWebview({ bridge: 'astrion-browser-v1', document: message.document, id: message.id, value: { ok: 1 } });
  eq(await done, { ok: 1 }, '应 resolve 为 value');
});

asyncTest('正确 document 回包 → resolve value', async () => {
  const env = installBridge({});
  const done = env.window.astrionBrowser.connect({ mode: 'agent', workspace: 'w', conversation: 'c' });
  const sent = lastPosted(env);
  eq(sent.type, 'connect', 'connect 的 type');
  env.emitWebview({ bridge: 'astrion-browser-v1', document: sent.document, id: sent.id, value: { lease: 'L1', revision: 1, tabs: [] } });
  eq((await done).lease, 'L1', 'value 应原样作为结果');
});

asyncTest('错误 document 回包不解析（pending 维持到正确回包）', async () => {
  const env = installBridge({});
  let state = 'pending';
  const done = env.window.astrionBrowser.command({ action: 'reload' }).then(
    value => {
      state = 'resolved';
      return value;
    },
    error => {
      state = 'rejected';
      throw error;
    }
  );
  const sent = lastPosted(env);
  env.emitWebview({ bridge: 'astrion-browser-v1', document: 'some-other-document', id: sent.id, value: { hijacked: true } });
  await flush();
  eq(state, 'pending', '错误 document 必须被忽略');
  env.emitWebview({ bridge: 'not-astrion', document: sent.document, id: sent.id, value: { hijacked: true } });
  await flush();
  eq(state, 'pending', '错误 bridge 标记必须被忽略');
  env.emitWebview({ bridge: 'astrion-browser-v1', document: sent.document, id: sent.id, value: { ok: true } });
  eq(await done, { ok: true }, '正确回包应 resolve');
  eq(state, 'resolved', '最终状态');
});

asyncTest('错误回包（error 字段）→ reject 且保留 native 错误码', async () => {
  const env = installBridge({});
  const done = env.window.astrionBrowser.command({ action: 'clear' });
  const sent = lastPosted(env);
  env.emitWebview({ bridge: 'astrion-browser-v1', document: sent.document, id: sent.id, error: 'stale_browser_view' });
  let message = '';
  try {
    await done;
  } catch (error) {
    message = error && error.message;
  }
  eq(message, 'stale_browser_view', 'error 字段应成为 Error.message');
});

asyncTest('未知 id 回包被忽略且不影响其它 pending', async () => {
  const env = installBridge({});
  let settled = false;
  const done = env.window.astrionBrowser.command({ action: 'reload' }).then(() => {
    settled = true;
  });
  const sent = lastPosted(env);
  env.emitWebview({ bridge: 'astrion-browser-v1', document: sent.document, id: sent.document + ':999', value: {} });
  await flush();
  eq(settled, false, '未知 id 不应 settle 任何 pending');
  env.emitWebview({ bridge: 'astrion-browser-v1', document: sent.document, id: sent.id, value: {} });
  await done;
  ok(settled, '正确 id 仍应 resolve');
});

asyncTest('pagehide → 所有 pending 以 stale_browser_view 拒绝，定时器清空', async () => {
  const env = installBridge({});
  const bridge = env.window.astrionBrowser;
  const a = bridge.command({ action: 'reload' });
  const b = bridge.connect({ mode: 'chat', workspace: 'w', conversation: 'c' });
  eq(env.timers.timers.size, 2, '两个 pending 应各有一个超时定时器');
  ok(JSON.parse(env.posted[0]).id !== lastPosted(env).id, '两个请求应有不同 id');
  env.emitWindow('pagehide');
  const errors = [];
  for (const p of [a, b]) {
    try {
      await p;
    } catch (error) {
      errors.push(error && error.message);
    }
  }
  eq(errors, ['stale_browser_view', 'stale_browser_view'], '两个 pending 都必须被拒绝');
  eq(env.timers.timers.size, 0, 'pagehide 应清除全部超时定时器');
});

asyncTest('pagehide 后监听清理：已订阅回调不再被调用', async () => {
  const env = installBridge({});
  const bridge = env.window.astrionBrowser;
  let calls = 0;
  bridge.onState(() => {
    calls += 1;
  });
  const probe = bridge.command({ action: 'reload' });
  const sent = lastPosted(env);
  env.emitWebview({ bridge: 'astrion-browser-v1', document: sent.document, id: sent.id, value: {} });
  await probe;
  env.emitWebview({ bridge: 'astrion-browser-v1', document: sent.document, event: 'state', value: { revision: 1 } });
  eq(calls, 1, '未清理时应收到 state');
  env.emitWindow('pagehide');
  env.emitWebview({ bridge: 'astrion-browser-v1', document: sent.document, event: 'state', value: { revision: 2 } });
  eq(calls, 1, 'pagehide 后订阅必须清空，不再回调');
});

asyncTest('取消订阅：unsubscribe 后不再回调', async () => {
  const env = installBridge({});
  const bridge = env.window.astrionBrowser;
  let calls = 0;
  const off = bridge.onMessage(() => {
    calls += 1;
  });
  ok(typeof off === 'function', 'onMessage 应返回取消函数');
  const probe = bridge.command({ action: 'reload' });
  const sent = lastPosted(env);
  env.emitWebview({ bridge: 'astrion-browser-v1', document: sent.document, id: sent.id, value: {} });
  await probe;
  env.emitWebview({ bridge: 'astrion-browser-v1', document: sent.document, event: 'message', value: { message: 'x' } });
  eq(calls, 1, '订阅中应回调');
  off();
  env.emitWebview({ bridge: 'astrion-browser-v1', document: sent.document, event: 'message', value: { message: 'y' } });
  eq(calls, 1, 'unsubscribe 后不应再回调');
});

// ────────────────────────── 串行执行异步用例 + 汇总 ──────────────────────────
(async () => {
  for (const [name, fn] of asyncQueue) {
    try {
      await fn();
      record(name, true);
    } catch (error) {
      record(name, false, error);
    }
  }
  const failed = results.filter(r => !r.ok);
  console.log('\nrepo: ' + REPO);
  console.log((results.length - failed.length) + '/' + results.length + ' passed');
  if (failed.length) {
    console.log('失败用例：\n  ' + failed.map(r => r.name).join('\n  '));
    process.exit(1);
  }
  process.exit(0);
})();
