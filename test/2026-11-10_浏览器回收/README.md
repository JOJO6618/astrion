# 桌面浏览器回收回归

契约见 `docs/native_browser.md` 的网页实例回收章节。测试于 2026-11-23 随 macOS 和 Windows 共用的 Electron 整组回收更新。本页后半部分保留旧版 WebView2 的历史验收记录。

## 验证

```bash
ASTRION_IGNORE_DOTENV=1 /opt/homebrew/bin/python3.11 -B test/2026-11-10_浏览器回收/test_retention.py
ASTRION_IGNORE_DOTENV=1 /opt/homebrew/bin/python3.11 -B test/2026-11-10_浏览器回收/test_http.py
ASTRION_IGNORE_DOTENV=1 /opt/homebrew/bin/python3.11 -B test/2026-11-09_AI浏览器工具/backend_regression.py
desktop-electron/node_modules/.bin/electron test/2026-11-10_浏览器回收/electron.mjs
```

- 策略 6 项覆盖 1 至 8 的示例、访问顺序、关闭与重开、三组名额、查询异常和删除。
- 接口 6 项覆盖主任务状态、工作区隔离、已重开标签、文件确认删除、读失败和参数校验。依赖为合成身份与临时替身。
- 原有后端浏览器权限回归 10 项。
- Electron 原生 14 项覆盖同一对话的网页一起销毁、按需重建、选中状态与 AI 标签身份、已认证的立即释放及删除恢复记录。使用独立临时资料目录和回环合成网页。
- 关闭竞态和工具开关的回归位于 `test/2026-11-23_浏览器整组回收/`。

Electron ESM 测试入口用 `app.whenReady().then(...)`，不能在入口顶层 await ready，否则 Electron 可能等待模块装载而无法发出 ready。macOS OS 沙箱无法启动本测试的 GUI 进程时，使用已批准的单次宿主执行，不更改 Electron 安全选项。

前端：`npm run build --silent 2>&1 | tail -n 5`（完整命令启用 pipefail）。

Windows（2026-11-10 在 Windows 实机复核）：

- `python test\2026-11-09_WindowsBrowser\host.py build-production-probe` 编译通过（`retention.rs`、`views/layout/ai` 改动、`page_dialog.rs` 删除全部随生产模块一起编译）。
- `run-production-probe`：write 阶段 39 项、read 阶段 42 项全过；`test-ai-native` 29 项全过（含真实断言后的 `dialogDismissed`）。
- **仍未覆盖**：探针没有构造 `/api/browser-retention` 响应，回收本身的“休眠/按需重建”只是编译通过，需在应用内手动验证（切走对话 30 分钟看标签是否休眠、再点标签能否重建并提示页面已重载）。

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
