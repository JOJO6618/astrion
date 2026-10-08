import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fixture } from './fixture.mjs';
import { isUpdaterInstalling } from '../../desktop-electron/src/updater-state.js';

function setup(t, options) {
  const value = fixture(options);
  t.after(value.close);
  return value;
}

test('原生准备超过旧版 8/11 秒阈值：不退出，不提前开启关闭闸门', async (t) => {
  const f = setup(t);
  await f.download();
  f.tick(30_000);
  assert.equal(f.count('native-check'), 1);
  assert.equal(f.count('native-install'), 0);
  assert.equal(f.count('app-quit'), 0);
  assert.equal(f.count('app-exit-0'), 0);
  assert.equal(isUpdaterInstalling(), false);
  assert.deepEqual(f.states, ['installing']);
  f.nativeUpdater.emit('update-downloaded');
  assert.equal(f.count('native-install'), 1);
  assert.deepEqual(f.states, ['installing', 'restarting']);
  assert.equal(f.calls.find((call) => call.event === 'gate-at-install').open, true);
  assert.equal(f.pending(), 0);
});

test('准备 5 秒后正常安装：原生退出完成时清除所有兜底', async (t) => {
  const f = setup(t);
  await f.download();
  f.tick(5000);
  f.nativeUpdater.emit('update-downloaded');
  f.tick(120_000);
  assert.equal(f.count('native-install'), 1);
  assert.equal(f.count('app-quit'), 1);
  assert.equal(f.count('backend-cleanup'), 0);
  assert.equal(f.count('app-exit-0'), 0);
});

test('原生退出被拦截：8/11 秒从真实安装退出事件开始计时', async (t) => {
  const f = setup(t, { blockQuit: true });
  await f.download();
  f.tick(30_000);
  f.nativeUpdater.emit('update-downloaded');
  f.tick(7999);
  assert.equal(f.count('app-quit'), 1);
  assert.equal(f.count('app-exit-0'), 0);
  f.tick(1);
  assert.equal(f.count('app-quit'), 2);
  f.tick(2999);
  assert.equal(f.count('backend-cleanup'), 0);
  f.tick(1);
  assert.equal(f.count('backend-cleanup'), 1);
  assert.equal(f.count('app-exit-0'), 1);
  assert.equal(f.calls.findIndex((call) => call.event === 'backend-cleanup')
    < f.calls.findIndex((call) => call.event === 'app-exit-0'), true);
});

test('后端清理抛错也执行安装期强制退出，并记录清理失败', async (t) => {
  const f = setup(t, { blockQuit: true, cleanupError: true });
  await f.download();
  f.tick(300);
  f.nativeUpdater.emit('update-downloaded');
  f.tick(11_000);
  assert.equal(f.count('app-exit-0'), 1);
  assert.equal(f.logs.some((item) => item.event === 'backend-cleanup-failed'), true);
});

test('准备超时：显示错误、保留应用、忽略迟到的原生就绪', async (t) => {
  const f = setup(t);
  await f.download();
  f.tick(120_000);
  assert.equal(f.errors.length, 1);
  assert.match(f.errors[0], /120 秒/);
  assert.equal(isUpdaterInstalling(), false);
  assert.throws(() => f.installer.assertCanStart(), /上一轮更新准备/);
  f.nativeUpdater.emit('update-downloaded');
  f.tick(120_000);
  assert.equal(f.count('native-install'), 0);
  assert.equal(f.count('app-quit'), 0);
  assert.equal(f.count('app-exit-0'), 0);
  assert.equal(f.logs.some((item) => item.event === 'native-ready-ignored'), true);
  assert.doesNotThrow(() => f.installer.assertCanStart());
});

test('准备出错：转发和原生错误不重复上报，不残留退出计时', async (t) => {
  const f = setup(t);
  await f.download();
  f.tick(300);
  f.nativeUpdater.emit('error', new Error('signature invalid'));
  f.tick(120_000);
  assert.deepEqual(f.errors, ['signature invalid']);
  assert.equal(f.pending(), 0);
  assert.equal(isUpdaterInstalling(), false);
  assert.equal(f.count('app-quit'), 0);
  assert.doesNotThrow(() => f.installer.assertCanStart());
});

test('在 300ms 延迟内失败：不会再启动原生准备', async (t) => {
  const f = setup(t);
  await f.download();
  f.updater.emit('error', new Error('cancelled'));
  f.tick(120_000);
  assert.equal(f.count('native-check'), 0);
  assert.equal(f.count('native-install'), 0);
  assert.deepEqual(f.errors, ['cancelled']);
});

test('原生准备同步抛错：不退出并允许下一次重试', async (t) => {
  const f = setup(t);
  f.nativeUpdater.checkForUpdates = () => { throw new Error('cannot start'); };
  await f.download();
  f.tick(300);
  assert.deepEqual(f.errors, ['cannot start']);
  assert.equal(f.pending(), 0);
  assert.doesNotThrow(() => f.installer.assertCanStart());
  assert.equal(f.count('app-quit'), 0);
});

test('退出已开始后出现错误：取消 8/11 秒兜底并关闭安装闸门', async (t) => {
  const f = setup(t, { blockQuit: true });
  await f.download();
  f.tick(300);
  f.nativeUpdater.emit('update-downloaded');
  f.nativeUpdater.emit('error', new Error('installation rejected'));
  f.tick(120_000);
  assert.equal(f.count('app-quit'), 1);
  assert.equal(f.count('app-exit-0'), 0);
  assert.equal(f.pending(), 0);
  assert.equal(isUpdaterInstalling(), false);
});

test('重复 ZIP / 原生事件不会重复准备或安装，也不会重设兜底', async (t) => {
  const f = setup(t, { blockQuit: true });
  await f.download();
  f.updater.emit('update-downloaded');
  f.tick(300);
  assert.equal(f.count('native-check'), 1);
  f.nativeUpdater.emit('update-downloaded');
  f.tick(4000);
  f.nativeUpdater.emit('update-downloaded');
  f.nativeUpdater.emit('before-quit-for-update');
  f.tick(4000);
  assert.equal(f.count('native-install'), 1);
  assert.equal(f.count('app-quit'), 2);
});

test('普通退出发生在准备阶段：清除定时器，迟到就绪不会请求安装', async (t) => {
  const f = setup(t);
  await f.download();
  f.tick(300);
  f.app.emit('will-quit');
  f.nativeUpdater.emit('update-downloaded');
  f.tick(120_000);
  assert.equal(f.pending(), 0);
  assert.equal(f.count('native-install'), 0);
  assert.equal(f.errors.length, 0);
});

test('原生准备成功但没有安装退出事件：不启动兜底，最终报错', async (t) => {
  const f = setup(t);
  f.nativeUpdater.quitAndInstall = () => {};
  await f.download();
  f.tick(300);
  f.nativeUpdater.emit('update-downloaded');
  f.tick(119_700);
  assert.equal(f.errors.length, 1);
  assert.equal(f.count('app-quit'), 0);
  assert.equal(f.count('app-exit-0'), 0);
  assert.equal(isUpdaterInstalling(), false);
});

test('错误后重试：旧定时器取消，新准备流程仍可完成安装', async (t) => {
  const f = setup(t);
  await f.download();
  f.tick(300);
  f.nativeUpdater.emit('error', new Error('temporary error'));
  f.installer.assertCanStart();
  await f.download();
  f.tick(30_000);
  assert.equal(f.count('native-check'), 2);
  assert.equal(f.count('app-quit'), 0);
  f.nativeUpdater.emit('update-downloaded');
  assert.equal(f.count('native-install'), 1);
  assert.equal(f.pending(), 0);
});
