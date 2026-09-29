// 启动编排：选端口 → 解析运行时 → 启动控制桥 → spawn 后端 → 等就绪 → 建窗。
// （对齐 Tauri 壳 backend.rs::start_backend_and_create_window 的步骤与失败语义）

import { app } from 'electron';
import {
  pickFreePort,
  spawnBackend,
  waitBackendReady,
  shutdownBackend,
  resolveRuntime
} from './backend.js';
import { startBridge } from './bridge.js';
import { resolveRunDataRoot } from './rundata.js';
import { loadLoginShellPath } from './shell_env.js';
import { createMainWindow, getMainView } from './window.js';

export { shutdownBackend };

export async function startBackendAndCreateWindow() {
  app.setName('Astrion');

  const port = await pickFreePort();
  // 生产优先：.app 内嵌运行时；不存在则回退开发模式（系统 python + 源码树）
  const { python, backendDir } = resolveRuntime();

  // 控制桥（自动更新/chrome 派发）：失败不致命——应用照常运行，仅更新功能不可用
  let bridgePort = null;
  try {
    bridgePort = await startBridge({
      version: app.getVersion(),
      isPackaged: app.isPackaged,
      getMainView
    });
  } catch (err) {
    console.error('[astrion-desktop] 控制桥启动失败（更新功能不可用）:', err);
  }

  const desktopDataRoot = await resolveRunDataRoot();
  const shellEnvironment = await loadLoginShellPath();
  if (shellEnvironment.status === 'loaded') {
    console.info('[astrion-desktop] 已从用户登录 shell 加载 PATH');
  } else if (shellEnvironment.status !== 'unsupported-platform') {
    console.warn(`[astrion-desktop] 登录 shell PATH 加载失败 (${shellEnvironment.status})，使用原始 PATH`);
  }

  spawnBackend({
    python,
    backendDir,
    port,
    bridgePort,
    version: app.getVersion(),
    shellPath: shellEnvironment.path,
    desktopDataRoot
  });

  await waitBackendReady(port);
  createMainWindow(port);
}
