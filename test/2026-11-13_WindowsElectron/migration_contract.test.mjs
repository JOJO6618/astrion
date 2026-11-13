// Windows 统一 Electron 迁移：平台契约 / 后端凭据传输 / guardian 启动包装 / Windows 更新器。
//
// 运行：node --experimental-vm-modules --test test/2026-11-13_WindowsElectron/*.test.mjs
// 依赖：仅 Node 20+ 内建的 vm.SourceTextModule/SyntheticModule。不需要 Electron、
//      esbuild、构建产物、网络或后台服务。
//
// 这些用例验证的是"真实模块的真实分支"，不是源码字符串：
//   - platform.js                 : 返回对象直接喂给 BaseWindow 的选项，
//   - backend-transport.js        : 实际调用 write(child)，检查写入落到 stdin 还是 fd 3，
//   - windows/backend-guardian.js : 检查拼出的 command/args 是否真的把 guardian 放在
//                                   python 之前，且不修改入参,
//   - updater-install-windows.js  : 用假 updater/app/定时器跑完整 prepare 状态机。
//
// 注意：被测模块在 vm 上下文里创建对象，其原型与测试进程不同；deepStrictEqual 会因此
// 在两个"同结构"对象上报错。所有跨 realm 的深比较都先过 plain()（JSON 归一）。
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { test } from 'node:test';
import { load, exportsOf } from './loader.mjs';
import { isUpdaterInstalling, setUpdaterInstalling } from '../../desktop-electron/src/updater-state.js';

const plain = (value) => JSON.parse(JSON.stringify(value));

// ───────────────────────────── platform.js ─────────────────────────────

test('platform: desktopSupported 只认 win32/darwin', async () => {
  const platform = exportsOf(await load('platform.js'), 'desktopSupported');
  assert.equal(platform.desktopSupported('win32'), true);
  assert.equal(platform.desktopSupported('darwin'), true);
  for (const other of ['linux', 'freebsd', 'openbsd', 'aix']) {
    assert.equal(platform.desktopSupported(other), false, `platform=${other}`);
  }
});

test('platform: 主窗口在 Windows 用无边框自绘，不携带 macOS 红绿灯参数', async () => {
  const platform = exportsOf(await load('platform.js'), 'windowChromeOptions');
  const options = platform.windowChromeOptions('win32');
  assert.deepEqual(plain(options), { frame: false, shadow: true });
  // trafficLightPosition / titleBarStyle 在 Windows 无效，或会得到没有控制按钮的窗口。
  assert.equal('trafficLightPosition' in options, false);
  assert.equal('titleBarStyle' in options, false);
  assert.equal('titleBarOverlay' in options, false);
});

test('platform: 主窗口在 macOS 保持隐藏标题栏 + 红绿灯定位', async () => {
  const platform = exportsOf(await load('platform.js'), 'windowChromeOptions');
  assert.deepEqual(plain(platform.windowChromeOptions('darwin')), {
    titleBarStyle: 'hidden',
    trafficLightPosition: { x: 15, y: 15 }
  });
});

test('platform: 独立浏览器窗口在 Windows 用 titleBarOverlay，颜色跟随主题色', async () => {
  const platform = exportsOf(await load('platform.js'), 'popoutChromeOptions');
  assert.deepEqual(plain(platform.popoutChromeOptions('win32', '#123456', '#abcdef')), {
    titleBarStyle: 'hidden',
    titleBarOverlay: { height: 46, color: '#123456', symbolColor: '#abcdef' }
  });
  // 主题尚未上报时交给系统初始化，不能用内容区底色冒充标签栏色。
  const fallback = platform.popoutChromeOptions('win32');
  assert.equal('color' in fallback.titleBarOverlay, false);
  assert.equal('symbolColor' in fallback.titleBarOverlay, false);
  assert.equal(fallback.titleBarOverlay.height, 46);
  // 红绿灯参数在 Windows 无意义，出现即等于"没有最小化/关闭按钮"。
  assert.equal('trafficLightPosition' in fallback, false);
});

test('platform: 独立浏览器窗口在 macOS 不启用 overlay（保留红绿灯）', async () => {
  const platform = exportsOf(await load('platform.js'), 'popoutChromeOptions');
  const options = platform.popoutChromeOptions('darwin', '#123456');
  assert.equal(options.titleBarStyle, 'hidden');
  assert.deepEqual(plain(options.trafficLightPosition), { x: 15, y: 15 });
  assert.equal('titleBarOverlay' in options, false);
});

test('platform: 选项对象不含 undefined 值（Electron 会对未知/空值报错）', async () => {
  const platform = exportsOf(await load('platform.js'), 'windowChromeOptions');
  for (const [name, value] of [
    ['windowChromeOptions(win32)', platform.windowChromeOptions('win32')],
    ['windowChromeOptions(darwin)', platform.windowChromeOptions('darwin')],
    ['popoutChromeOptions(win32)', platform.popoutChromeOptions('win32')],
    ['popoutChromeOptions(darwin)', platform.popoutChromeOptions('darwin')]
  ]) {
    for (const [key, entry] of Object.entries(value)) {
      assert.notEqual(entry, undefined, `${name}.${key}`);
    }
  }
});

// ────────────────────── backend-transport.js（凭据管道） ──────────────────────

function recordingChild() {
  const writes = [];
  return {
    writes,
    child: {
      // 生产实现会对管道调用 pipe.on('error', …)，假管道必须提供 on()。
      stdin: { on() {}, end: (value) => writes.push(['stdin', value]) },
      stdio: [null, null, null, { on() {}, end: (value) => writes.push(['fd3', value]) }]
    }
  };
}

test('凭据: Windows 用 stdin 传 token，不占用 fd 3', async () => {
  const { credentialTransport } = exportsOf(await load('backend-transport.js'), 'credentialTransport');
  const transport = credentialTransport({ platform: 'win32', bridgePort: 62001, browserToken: 'tok-win' });
  assert.equal(transport.env.ASTRION_DESKTOP_BROWSER_TOKEN_FD, '0');
  assert.deepEqual(plain(transport.stdio), ['pipe', 'pipe', 'pipe']);
  const { child, writes } = recordingChild();
  transport.write(child);
  assert.deepEqual(writes, [['stdin', 'tok-win\n']]);
});

test('凭据: macOS/Linux 仍用 fd 3，stdin 保持 ignore', async () => {
  const { credentialTransport } = exportsOf(await load('backend-transport.js'), 'credentialTransport');
  for (const platform of ['darwin', 'linux']) {
    const transport = credentialTransport({ platform, bridgePort: 62001, browserToken: 'tok-posix' });
    assert.equal(transport.env.ASTRION_DESKTOP_BROWSER_TOKEN_FD, '3', platform);
    assert.deepEqual(plain(transport.stdio), ['ignore', 'pipe', 'pipe', 'pipe'], platform);
    const { child, writes } = recordingChild();
    transport.write(child);
    assert.deepEqual(writes, [['fd3', 'tok-posix\n']], platform);
  }
});

test('凭据: 无 token 时不设 FD、stdin 忽略、write 不抛错', async () => {
  const { credentialTransport } = exportsOf(await load('backend-transport.js'), 'credentialTransport');
  for (const platform of ['win32', 'darwin']) {
    const transport = credentialTransport({ platform, bridgePort: 62001, browserToken: '' });
    assert.equal('ASTRION_DESKTOP_BROWSER_TOKEN_FD' in transport.env, false, platform);
    assert.equal(transport.env.ASTRION_DESKTOP_BROWSER_TOKEN_FD, undefined, platform);
    assert.equal(transport.stdio[0], 'ignore', platform);
    // 无 token 时 child.stdin 为 null（stdio[0]='ignore'）—— write 必须是安全 no-op。
    assert.doesNotThrow(() => transport.write({ stdin: null, stdio: [] }), platform);
  }
});

test('凭据: 缺 bridgePort 时即便有 token 也不下发（保持旧的组合条件）', async () => {
  const { credentialTransport } = exportsOf(await load('backend-transport.js'), 'credentialTransport');
  for (const bridgePort of [null, undefined, 0]) {
    const transport = credentialTransport({ platform: 'win32', bridgePort, browserToken: 'tok-orphan' });
    assert.equal('ASTRION_DESKTOP_BROWSER_TOKEN_FD' in transport.env, false, `bridgePort=${bridgePort}`);
    assert.equal(transport.stdio[0], 'ignore', `bridgePort=${bridgePort}`);
  }
});

test('凭据: token 不得以明文形式留在任何环境变量里', async () => {
  const { credentialTransport } = exportsOf(await load('backend-transport.js'), 'credentialTransport');
  for (const platform of ['win32', 'darwin']) {
    const token = 'plaintext-browser-token-should-not-leak';
    const transport = credentialTransport({ platform, bridgePort: 62100, browserToken: token });
    assert.equal(JSON.stringify(transport.env).includes(token), false, platform);
    assert.equal(transport.env.ASTRION_DESKTOP_BROWSER_TOKEN, undefined, platform);
    assert.equal(transport.env.ASTRION_DESKTOP_BROWSER_TOKEN_FD, platform === 'win32' ? '0' : '3');
  }
});

// ──────────────── windows/backend-guardian.js（Windows 启动包装） ────────────────

const PYTHON = 'C:\\Program Files\\Astrion\\runtime\\python\\astrion-backend.exe';
const HELPER = 'C:\\Program Files\\Astrion\\runtime\\python\\backend_guardian.py';
const BACKEND_ARGS = ['-m', 'server.app', '--port', '8123', '--path', 'C:\\Users\\me'];

test('guardian: Windows 下 guardian 必须排在真实 python 之前', async () => {
  const { backendLaunch } = exportsOf(await load('windows/backend-guardian.js'), 'backendLaunch');
  const launch = backendLaunch({
    platform: 'win32', python: PYTHON, args: BACKEND_ARGS, shellPid: 4321, helperPath: HELPER
  });
  assert.equal(launch.command, PYTHON);
  assert.deepEqual(plain(launch.args), ['-u', '-B', HELPER, '4321', '--', PYTHON, ...BACKEND_ARGS]);
  // 关键不变量：helper 在 python 之前，且 '--' 把原后端参数完整隔开。
  assert.ok(launch.args.indexOf(HELPER) < launch.args.indexOf(PYTHON));
  assert.equal(launch.args[launch.args.indexOf(HELPER) + 2], '--');
  assert.equal(launch.args.at(-1), BACKEND_ARGS.at(-1));
});

test('guardian: Windows 下 shellPid 必须是安全正整数', async () => {
  const { backendLaunch } = exportsOf(await load('windows/backend-guardian.js'), 'backendLaunch');
  const launch = backendLaunch({ platform: 'win32', python: PYTHON, args: [], shellPid: 4321, helperPath: HELPER });
  assert.equal(launch.args[3], '4321');
  assert.equal(launch.args[0], '-u');
  assert.equal(launch.args[1], '-B');
  // 0 会被 Python 侧当成“当前进程组”语义，字符串 pid 无法比较——都必须在壳侧拒绝。
  for (const shellPid of [0, '4321']) {
    assert.throws(
      () => backendLaunch({ platform: 'win32', python: PYTHON, args: [], shellPid, helperPath: HELPER }),
      /invalid_backend_guardian/,
      `shellPid=${String(shellPid)} 应被拒绝`
    );
  }
});

test('guardian: 非 Windows 原样启动 python，不插入 helper', async () => {
  const { backendLaunch } = exportsOf(await load('windows/backend-guardian.js'), 'backendLaunch');
  for (const platform of ['darwin', 'linux']) {
    const launch = backendLaunch({ platform, python: '/usr/bin/python3', args: BACKEND_ARGS, shellPid: 99, helperPath: '/tmp/guardian.py' });
    assert.equal(launch.command, '/usr/bin/python3', platform);
    assert.deepEqual(plain(launch.args), BACKEND_ARGS, platform);
    assert.equal(launch.args.includes('/tmp/guardian.py'), false, platform);
  }
});

test('guardian: 不修改调用方传入的 args 数组', async () => {
  const { backendLaunch } = exportsOf(await load('windows/backend-guardian.js'), 'backendLaunch');
  const args = Object.freeze([...BACKEND_ARGS]);
  const launch = backendLaunch({ platform: 'win32', python: PYTHON, args, shellPid: 1, helperPath: HELPER });
  assert.deepEqual([...args], BACKEND_ARGS);
  assert.notEqual(launch.args, args);
});

test('guardian: Windows 参数中的空格与内嵌连字符原样透传', async () => {
  const { backendLaunch } = exportsOf(await load('windows/backend-guardian.js'), 'backendLaunch');
  const args = ['-m', 'server.app', '--path', 'D:\\Astrion Data\\工作区 --x'];
  const launch = backendLaunch({ platform: 'win32', python: PYTHON, args, shellPid: 7, helperPath: HELPER });
  const tail = launch.args.slice(launch.args.indexOf('--') + 1);
  assert.deepEqual(plain(tail), [PYTHON, ...args]);
});

// ─────────────── updater-install-windows.js（Windows 更新安装状态机） ───────────────

let installerFactoryPromise;
function loadInstallerFactory() {
  installerFactoryPromise ||= load('updater-install-windows.js')
    .then((module) => exportsOf(module, 'createWindowsUpdateInstaller').createWindowsUpdateInstaller);
  return installerFactoryPromise;
}

function updaterHarness(createWindowsUpdateInstaller, { throwOnInstall = false } = {}) {
  const calls = [];
  const states = [];
  const errors = [];
  const timers = new Map();
  let now = 0;
  let nextTimer = 0;

  const app = {
    on() {}, once() {}, removeListener() {},
    quit: () => calls.push({ event: 'app-quit' }),
    exit: (code) => calls.push({ event: `app-exit-${code}` })
  };
  const updater = new EventEmitter();
  // 代表 bridge 的既有 error 订阅：EventEmitter 在无 listener 时 emit('error') 会直接抛出，
  // 那条抛出属于测试脚手架噪声，不代表被测模块行为。
  updater.on('error', () => { calls.push({ event: 'bridge-error-listener' }); });
  updater.quitAndInstall = (...args) => {
    calls.push({ event: 'quit-and-install', args });
    if (throwOnInstall) throw new Error('installer spawn failed');
  };
  updater.checkForUpdates = async () => { calls.push({ event: 'check' }); };

  const installer = createWindowsUpdateInstaller({
    updater,
    app,
    stopBackend: () => calls.push({ event: 'stop-backend' }),
    onState: (state) => { states.push(state); calls.push({ event: `state:${state}` }); },
    onError: (error) => { errors.push(String(error?.message || error)); calls.push({ event: 'on-error' }); },
    log: () => {},
    setTimer: (callback, delay) => {
      const id = ++nextTimer;
      timers.set(id, { callback, at: now + delay });
      return id;
    },
    clearTimer: (id) => timers.delete(id)
  });

  return {
    installer, updater, app, calls, states, errors, timers,
    index: (event) => calls.findIndex((call) => call.event === event),
    count: (event) => calls.filter((call) => call.event === event).length,
    tick(milliseconds) {
      const end = now + milliseconds;
      for (;;) {
        const next = [...timers.entries()]
          .filter(([, timer]) => timer.at <= end)
          .sort((a, b) => a[1].at - b[1].at)[0];
        if (!next) break;
        timers.delete(next[0]);
        now = next[1].at;
        next[1].callback();
      }
      now = end;
    }
  };
}

test('更新器(Windows): prepare 停后端并静默更新，保留安装目录且自动重启', async () => {
  const f = updaterHarness(await loadInstallerFactory());
  f.installer.prepare();
  f.tick(60_000);
  assert.deepEqual(f.states, ['installing', 'restarting']);
  assert.equal(f.count('quit-and-install'), 1);
  assert.deepEqual(plain(f.calls.find((call) => call.event === 'quit-and-install').args), [true, true]);
  // 顺序不变量：后端已停 → restarting 已上报 → 才允许退出安装。
  assert.ok(f.index('stop-backend') >= 0);
  assert.ok(f.index('stop-backend') < f.index('state:restarting'));
  assert.ok(f.index('state:restarting') < f.index('quit-and-install'));
});

test('更新器(Windows): prepare 前后与安装期共享 updater-state 闸门正确切换', async () => {
  // 关键：安装器在 vm 里执行，这里用 SyntheticModule 把测试进程里的真实
  // updater-state 实例注入进去，两侧看到同一份模块级状态；否则两个 realm 各持一份，
  // 闸门永远读不到（上一版就是因此失败）。
  const stateModule = await import('../../desktop-electron/src/updater-state.js');
  const factory = exportsOf(
    await load('updater-install-windows.js', { mocks: { './updater-state.js': { synthetic: stateModule } } }),
    'createWindowsUpdateInstaller'
  ).createWindowsUpdateInstaller;
  setUpdaterInstalling(false);
  try {
    const f = updaterHarness(factory);
    assert.equal(isUpdaterInstalling(), false, 'prepare 之前闸门必须是关闭的');
    f.installer.prepare();
    assert.equal(isUpdaterInstalling(), true, 'prepare 后必须立刻关闭窗口/激活闸门');
    f.tick(60_000);
    // 安装期（quitAndInstall 已调用、进程尚未退出）闸门必须保持关闭，
    // 否则 ShipIt/NSIS 等待"无运行实例"期间会被二次启动或窗口重建打断。
    assert.equal(isUpdaterInstalling(), true, '安装期闸门必须保持关闭');
    assert.equal(f.count('quit-and-install'), 1);
    f.installer.fail(new Error('install aborted'));
    assert.equal(isUpdaterInstalling(), false, '失败后必须恢复闸门，应用继续可用');
  } finally {
    setUpdaterInstalling(false);
  }
});

test('更新器(Windows): 重复 prepare 不重复安装、不重复停后端', async () => {
  const f = updaterHarness(await loadInstallerFactory());
  f.installer.prepare();
  assert.doesNotThrow(() => f.installer.prepare());
  f.tick(60_000);
  assert.doesNotThrow(() => f.installer.prepare());
  f.tick(60_000);
  assert.equal(f.count('quit-and-install'), 1);
  assert.equal(f.count('stop-backend'), 1);
  assert.deepEqual(f.states, ['installing', 'restarting']);
});

test('更新器(Windows): quitAndInstall 抛错时上报并释放，可再次 prepare', async () => {
  const f = updaterHarness(await loadInstallerFactory(), { throwOnInstall: true });
  f.installer.prepare();
  f.tick(60_000);
  assert.equal(f.errors.length, 1);
  assert.match(f.errors[0], /installer spawn failed/);
  assert.doesNotThrow(() => f.installer.assertCanStart());
  f.installer.prepare();
  f.tick(60_000);
  assert.equal(f.count('quit-and-install'), 2);
});

test('更新器(Windows): fail() 上报错误并允许重新开始', async () => {
  const f = updaterHarness(await loadInstallerFactory());
  f.installer.fail(new Error('download failed'));
  assert.deepEqual(f.errors, ['download failed']);
  assert.doesNotThrow(() => f.installer.assertCanStart());
  f.installer.prepare();
  f.tick(60_000);
  assert.equal(f.count('quit-and-install'), 1);
});

test('更新器(Windows): dispose 释放已激活的关闭闸门且不再安装', async () => {
  const stateModule = await import('../../desktop-electron/src/updater-state.js');
  const factory = exportsOf(
    await load('updater-install-windows.js', { mocks: { './updater-state.js': { synthetic: stateModule } } }),
    'createWindowsUpdateInstaller'
  ).createWindowsUpdateInstaller;
  const f = updaterHarness(factory);
  f.installer.prepare();
  assert.equal(isUpdaterInstalling(), true);
  f.installer.dispose();
  assert.equal(isUpdaterInstalling(), false);
  f.installer.prepare();
  f.tick(60_000);
  assert.equal(f.count('quit-and-install'), 0);
  assert.equal(f.count('stop-backend'), 0);
  assert.equal(f.timers.size, 0);
});

test('更新器(Windows): 不重复订阅 updater 事件（bridge 已负责下载态）', async () => {
  const f = updaterHarness(await loadInstallerFactory());
  const baseline = {
    error: f.updater.listenerCount('error'),
    downloaded: f.updater.listenerCount('update-downloaded'),
    progress: f.updater.listenerCount('download-progress')
  };
  assert.equal(baseline.error, 1, '脚手架应只有 bridge 的一个 error 订阅');
  // 模块按契约不监听下载/错误事件；误加监听会让 Windows 在 ZIP 下载完成时提前安装，
  // 或让一次下载失败被两处分别处理。
  assert.equal(baseline.downloaded, 0);
  assert.equal(baseline.progress, 0);
  f.updater.emit('update-downloaded', { version: '9.9.9' });
  f.updater.emit('download-progress', { transferred: 1, total: 2 });
  f.updater.emit('error', new Error('late error'));
  f.tick(60_000);
  assert.equal(f.updater.listenerCount('error'), 1, '模块不得额外订阅 error');
  assert.equal(f.updater.listenerCount('update-downloaded'), 0);
  assert.equal(f.updater.listenerCount('download-progress'), 0);
  assert.equal(f.count('quit-and-install'), 0);
  assert.deepEqual(f.states, []);
  assert.deepEqual(f.errors, []);
});
