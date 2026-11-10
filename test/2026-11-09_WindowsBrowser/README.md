# Windows 浏览器适配验证

基线：远程 main `40d3c5a9`，叠加本轮 Windows Tauri + WebView2 适配。生产浏览器代码在 `desktop/src-tauri/src/browser/`；AI 复用 `desktop-electron/src/browser/ai/` 的控制器、驱动、隔离世界及快照，不依赖用户安装 Node。

## 已执行结果

| 验证 | 本机结果 |
| --- | --- |
| 生产模块双进程写入/恢复 | r2 39 / 43 项通过，含 CSV、分区 Cookie 回读/重启、file 相对资源与历史导航、WGC 裁剪 |
| 23 个 AI 工具主要行为 | 29 项断言通过 |
| Rust 浏览器单测 | 12 项通过 |
| 共享浏览器权限/审批 | 10 项通过 |
| Windows 凭据管道和平台门控 | 3 项通过 |
| 合成 Chrome SQLite/DPAPI/AES-GCM 导入 | 4 项通过 |
| 共享 UI / Windows 主桥 | 22 项通过 |
| 定向 ESLint、tsc/stylelint/Vite | 通过；保留现有 Vite 大 chunk 提示 |
| 最终便携包实际启动与退出 | r2 11 项检查全部通过 |
| 新版导出文件解析 | 9 项通过 |
| 独立 Chromium 149 扩展真实 API | 7 项通过，包含完整分区导出；权限弹窗仍需手动验收 |
| Electron file 策略/事件/AI 门控 | 生产模块 + 原生视图替身 9 项通过，未做 Mac 实机验证 |

全部使用隔离 profile 和合成账号，未读取真实 Chrome 资料。工具回归不等于真实模型、外部网站、DPI、输入法和系统交互已经验收。

**2026-11-10 复核（拉取 main `fe9c1040` 后重跑）**：`check-native` 6m 通过、`test-native` 12 项通过、`build-production-probe` 45.56s 通过、`run-production-probe`（write 39 / read 42）与 `test-ai-native`（29 项，含改成真实断言的 `dialogDismissed`）全过。`host.py` 的 `lint-ui`/`format-ui` 文件清单改为枚举 `static/src/components/browser` 目录：原清单还写着已删除的 `BrowserPageDialog.vue`，会让 ESLint 以 “No files matching the pattern” 直接退出。回收本身的休眠/重建路径仍未被探针覆盖。

## Windows 原生执行命令

在仓库根目录的 Windows cmd 中执行（Python 和 Node 已就位，Cargo 由脚本补 PATH）：

```cmd
python test\2026-11-09_WindowsBrowser\host.py check-native
python test\2026-11-09_WindowsBrowser\host.py test-native
python test\2026-11-09_WindowsBrowser\host.py build-production-probe
python test\2026-11-09_WindowsBrowser\host.py run-production-probe
python test\2026-11-09_WindowsBrowser\host.py test-ai-native
python test\2026-11-09_WindowsBrowser\host.py test-ai-backend
python test\2026-11-09_WindowsBrowser\host.py test-ai-startup
python test\2026-11-09_WindowsBrowser\host.py test-import
python test\2026-11-09_WindowsBrowser\host.py test-ui
python test\2026-11-09_WindowsBrowser\host.py lint-ui
python test\2026-11-09_WindowsBrowser\host.py frontend
```

证据目录：`_experiments/windows-browser-20261109/`。`production-latest.json` 指向最新双进程结果，`ai-latest.json` 指向最新 AI 结果；各命令完整 stdout/stderr 保存在同目录日志。

## 便携包

```cmd
python test\2026-11-09_WindowsBrowser\package_test.py build
python test\2026-11-09_WindowsBrowser\package_test.py assemble
python test\2026-11-09_WindowsBrowser\package_test.py smoke
python test\2026-11-09_WindowsBrowser\package_test.py archive
```

`build` 包含后端 staging 和原生编译。若资源已装配且源码未变化，可用 `compile` 单独续编。只有编译 exit 0 才写 `package-build-success.json`；assemble 必须核对源文件、前端 manifest 和 exe SHA256，防止旧产物误交付。使用已存在目录/ZIP 时拒绝覆盖。

目标目录：`output/Astrion-Windows-Browser-Test-20261109-r2/`，独立 identifier `com.astrion.desktop.browser-test-20261109-r2`，保留 r1 及其用户资料。测试配置与正式版本分离，禁止修改正式版发布版本或更新通道。包内保留 Playwright Apache-2.0 许可证和 notice，并附 `chrome-cookie-migration/` 用户授权扩展。

smoke 从包内 exe 启动，独立实验数据根，核对自带 Python、健康接口、AI 工具能力、页面资源、原生窗口、关窗后后端回收；不会把烟测 profile 装入 ZIP。archive 额外核对 CRC、核心文件和 profile 排除，输出 SHA256。

当前打包状态（2026-11-09 r2）：后端 staging 76.9 MB、依赖与 lock 一致；r2 exe 已成功编译并按构建回执核验后组装。最终包内 smoke 的 11 项检查全部通过，包括自带后端、隔离数据根/工作区目录/主页面 WebView2、AI 能力、页面资源、原生窗口注册与可见、实际关闭按钮和后端回收。结果见包内 `verification/package-smoke.json`。ZIP 由 archive 命令生成并校验 CRC、核心文件与 profile 排除，同时输出 SHA256 文件。构建输入哈希也覆盖 include_str 的 Python、共享密码适配器和随包扩展。

r2 补充命令及限制见 `../2026-11-09_浏览器授权迁移/README.md`。真实 Chrome v20 不由直接读库解密；密码走 CSV、Cookie 走用户主动授权导出。file 仅开放手动浏览，模型仍限网页；远程网页不能任意跳入本地 file。

烟测时序：可见 HWND 可能早于 WebView2 创建完成及 Tauri 窗口注册，必须等待 `/api/desktop/window/state` 成功后再断言 shell 资料目录并关闭。便携启动还显式设置 `ASTRION_QUICK_SETTINGS_FILE`，避免读取正式版全局快捷对话设置。

用户手动验收说明见 `TESTING.md`。不自动 commit、push、签名或发布。
