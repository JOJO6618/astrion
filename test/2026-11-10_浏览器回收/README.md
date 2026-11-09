# 双平台浏览器回收 · 2026-11-10

契约见 `docs/native_browser.md` 的网页实例回收章节。共享判定在 `modules/native_browser/retention.py`，Electron / WebView2 各自释放和恢复原生实例。

## 验证

```bash
ASTRION_IGNORE_DOTENV=1 /opt/homebrew/bin/python3.11 -B test/2026-11-10_浏览器回收/test_retention.py
ASTRION_IGNORE_DOTENV=1 /opt/homebrew/bin/python3.11 -B test/2026-11-10_浏览器回收/test_http.py
ASTRION_IGNORE_DOTENV=1 /opt/homebrew/bin/python3.11 -B test/2026-11-09_AI浏览器工具/backend_regression.py
desktop-electron/node_modules/.bin/electron test/2026-11-10_浏览器回收/electron.mjs
```

- 策略 10 项：多个前台＋三个后台、超额覆盖选中及一小时保护、30 分钟边界、一小时结束、运行结束进入后台、查询异常保留、删除覆盖保护、忙碌页面、纯恢复记录不占名额、两次轮询之间快速切换。
- 真实响应构建器 4 项：关闭桌面标签但仍运行、只 stat 不读历史、确认删除、访问拒绝不等于删除、非法请求拒绝；测试依赖使用合成身份和临时替身，不启动真实后端或读取用户对话。
- 原有后端浏览器权限回归 10 项。
- Electron 原生 14 项：真实隐藏窗口、生产 NativeBrowser、localhost 合成页面、真实原生实例释放和重建、保留标签顺序和 AI 短 ID、恢复后完整快照与状态丢失提示、AI 恢复不切换用户标签、删除恢复记录。临时独立 userData 和资料目录，测试结束清理。

Electron ESM 测试入口用 `app.whenReady().then(...)`，不能在入口顶层 await ready，否则 Electron 可能等待模块装载而无法发出 ready。macOS OS 沙箱无法启动本测试的 GUI 进程时，使用已批准的单次宿主执行，不更改 Electron 安全选项。

前端：`npm run build --silent 2>&1 | tail -n 5`（完整命令启用 pipefail）。Windows Rust 代码经 rustfmt 解析与格式检查；当前 Mac 仅安装 aarch64-apple-darwin 目标，未做 Windows 编译或原生运行，不将这些结果当成 Windows 实机验收。

## 启动源码最新版

先完全退出正在运行的 Astrion，避免单实例锁激活旧窗口。

macOS（前端已构建）：

```bash
npm --prefix desktop-electron run dev
```

Windows 机器在同版本源码根目录：

```powershell
npm run build
npm --prefix desktop run dev
```

本轮没有打包、签名、安装或发布新安装包。普通回收保留 Cookie、密码、历史和标签信息；重载不恢复网页运行状态、表单和前后退栈。正常同步间隔约两秒，状态查询失败不执行回收。
