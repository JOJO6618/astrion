# 内置浏览器技术路线实验（2026-11-07）

这是隔离可行性实验，不是生产集成，不修改 Playwright 源码。

结论与边界见根目录 `output/native-browser-feasibility-20261107.md`。实测数据在 `_experiments/native-browser-feasibility-20261107/`；该目录含本地运行数据，不纳入 Git。

## 已使用环境

- macOS / Apple Silicon。
- 仓库根 `node_modules/playwright-core` 1.61.1。
- `desktop-electron/node_modules/electron` 35.7.5。
- Docker Desktop / Docker Engine 27.5.1；已有 `python:3.11-slim` 镜像。
- CEF 156.0.15 macOS ARM64 官方 client，固定版本元数据见本目录 `cef-distribution.json`。

需能启动独立 GUI 进程、监听 localhost、访问 Docker socket。Astrion 宿主沙箱曾拒绝这些操作，本轮通过用户授权的单次完全访问执行。不要通过修改构建配置绕过沙箱。

## 从仓库根运行

```bash
node test/2026-11-07_内置浏览器可行性/run-lab.cjs
node test/2026-11-07_内置浏览器可行性/run-docker-lab.cjs
```

第一条创建隐藏的独立 Electron 窗口、原生子视图和本地测试页；第二条使用一次性只读 Docker 夹具，测试容器指令与本机原生页面之间的轮询/结果链路。两者的用户资料和日志都在独立实验目录，不启动真实 Astrion 后端。

需要真实 CEF 样例时：

```bash
node test/2026-11-07_内置浏览器可行性/prepare-cef.cjs
swiftc -module-cache-path _experiments/native-browser-feasibility-20261107/swift-module-cache test/2026-11-07_内置浏览器可行性/window-probe.swift -o _experiments/native-browser-feasibility-20261107/window-probe
node test/2026-11-07_内置浏览器可行性/run-cef-lab.cjs
node test/2026-11-07_内置浏览器可行性/fetch-cef-source.cjs
```

`prepare-cef` 会下载约 129.32 MiB 的固定版本官方包、核对 SHA1、解压到实验目录；不会安装系统软件。CEF 运行测试会短暂创建真实样例窗口，官方样例未采纳传入的窗口位置参数，不能保证不激活窗口。它不截取屏幕；`window-probe` 只查询指定实验 PID 的窗口元数据，不申请辅助功能权限。

## 脚本职责

- `electron-harness.cjs`：独立 BaseWindow 与原生 WebContentsView，无 Astrion preload。
- `run-lab.cjs`：页面操作、Cookie/存储、目标越界能力、精确 targetId 选择、上传下载与按需截图。
- `scoped-page-service.cjs`：只登记指定 Page 的有限工具适配演示，不是完整权限系统。
- `docker-backend.py` / `run-docker-lab.cjs`：真实容器与本地原生视图的控制链路；只验证 Docker Desktop 端口映射场景。
- `run-cef-lab.cjs`：真实 CEF Chrome/Views、请求 native、Alloy/native 三模式；保留运行样式日志。
- `prepare-cef.cjs` / `fetch-cef-source.cjs`：官方二进制校验与同版本源码存档。
- `fixture.html` / `fixture.js` / `fixture.css`：功能测试夹具，不是拟议产品 UI。

## 不得超出证据的结论

通过这些实验，不代表已经实现完整 Chrome UI 外部宿主内嵌、Windows 支持、公网 Docker 接入、真实人工 IME/视觉验收或生产权限隔离。

原始 Electron/CDP 连接对多个目标有控制能力，不能作为面向模型的生产接口；实验只读取合成标记。Cookie partition 与 Playwright context 的差异是实测结果，不要直接把两者视为等价。
