// Astrion 桌面壳入口（Electron/Chromium，替代 desktop/ 的 Tauri/WKWebView 壳）。
//
// 架构（与 Tauri 壳一致，后端契约零改动）：
//   1. 选取空闲端口 → spawn Python 后端（开发期系统 python；生产期内嵌 sidecar 解释器）
//   2. 轮询后端就绪（/api/host-mode-enabled，无需鉴权）
//   3. 创建主窗口：BaseWindow + 双 WebContentsView——
//      chrome 视图（顶部 46px 独立对话标签条，加载 /chrome，常驻不随主页面导航重载）
//      + main 视图（加载 http://127.0.0.1:<port>/，前后端同源）
//   4. 主窗口关闭 → 退出进程 → will-quit 时 kill 后端子进程
//
// 换引擎动机：WKWebView 反复出现引擎级兼容问题（折叠动画瞬消、rAF 样式延迟、
// virtua 首项不挂载、拖放被吞、上滚估值补偿可见抖动），统一 Chromium 后与
// Windows（WebView2）/ Chrome web 同一引擎，测试矩阵塌缩。

import { app } from 'electron';
import { startBackendAndCreateWindow, shutdownBackend } from './lifecycle.js';
import { installAppMenu } from './menu.js';

// 单实例锁：第二实例直接退出，焦点交给已运行窗口（Tauri 侧曾是待办项）
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    // 已有实例：聚焦主窗口（由 lifecycle 提供）
    import('./window.js').then((m) => m.focusMainWindow());
  });

  app.whenReady().then(async () => {
    installAppMenu();
    try {
      await startBackendAndCreateWindow();
    } catch (err) {
      // 后端起不来属致命错误：写日志 + 落盘临时文件（GUI 进程无控制台可见性），退出
      console.error('[astrion-desktop] 启动失败:', err);
      const fs = await import('node:fs');
      const os = await import('node:os');
      const path = await import('node:path');
      const logPath = path.join(os.tmpdir(), 'astrion-desktop-startup.log');
      try {
        fs.writeFileSync(logPath, `[astrion-desktop] 启动失败\n${err?.stack || err}\n`);
      } catch {
        /* 落盘失败不阻断退出 */
      }
      app.exit(1);
    }
  });

  app.on('window-all-closed', () => {
    // macOS 惯例是关窗不退 app，但本壳单窗口 + 内嵌后端，关窗即退出（对齐 Tauri 行为）
    app.quit();
  });

  app.on('will-quit', () => {
    shutdownBackend();
  });
}
