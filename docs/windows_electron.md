# Windows Electron 迁移版

2026-11-13，分支 `feat/windows-electron-migration`。Windows 与 macOS 共用 `desktop-electron/` 主进程、双视图窗口、浏览器控制器与快捷对话；Windows 包尚未正式发布。旧 `desktop/` 保留作原生模块与运行时 staging 来源，不在本轮删除。

## 构建

必须使用 Windows 原生 Node、Python 与 Rust MSVC 工具链。不要在 WSL/Linux 中执行 Windows `.exe`。

```cmd
set "ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/"
npm ci --prefix desktop-electron --no-audit --no-fund
node desktop-electron\scripts\build_windows.mjs
```

构建先生成共享前端，再构建 Windows native helper、调用 `desktop/scripts/prepare_backend.py` staging 后端，最后运行 electron-builder。已有本次前端/helper 产物时可用 `--skip-frontend --skip-native`；仅目录包加 `--dir`。所有构建固定 `--publish never`。

- Windows 版本及说明：`desktop-electron/windows-release.json`，由 builder 的 `extraMetadata.version` 注入。
- macOS 版本：`desktop-electron/package.json`，独立维护。
- Windows 配置：`desktop-electron/electron-builder.win.cjs`。
- 输出：`output/windows-electron/build/win-unpacked/astrion-desktop.exe` 与 `Astrion-<version>-windows-x64-setup.exe`。
- 构建回执：`output/windows-electron/build-receipt.json`；构建成功不代表原生交互验证成功。

Windows 普通用户可能无法解压 winCodeSign 归档中的 macOS 符号链接。`test/2026-11-13_WindowsElectron/prepare_sign_cache.py` 可从已下载归档仅准备 Windows rcedit 工具，排除不用的 Darwin 目录；不关闭签名配置、不修改系统权限。

## 平台边界

- `src/platform.js`：Windows 主窗口 frameless，复用已有 chrome 页面和三大键；独立浏览器窗口使用 Window Controls Overlay。macOS 保持原红绿灯。
- Windows 0.10.2 的独立浏览器控制区使用前端 `--chrome-bar-bg`（theme/layout 的 `chromeColor`），不能使用内容区 `surfaceColor`；图标使用 `textColor`。创建时读取缓存，切换主题时更新全部存活独立窗口，标签栏色单独变化也必须上报。未收到主题时不写死颜色，macOS 不调用 `setTitleBarOverlay`。主题逻辑回归见 `test/2026-11-13_Windows浏览器标题栏配色/`；视觉交由用户确认。
- `src/preload-chrome.cjs` 与主视图 preload 均须注入 `__ASTRION_PLATFORM__`。独立顶栏依赖此标记显示 Windows 设置和三大键；仅主视图注入会导致顶栏按钮缺失。Windows 0.10.1 已修正，并覆盖 win32/darwin 两项 preload 回归及真实包内按钮操作。
- `src/windows/backend-guardian.py`：自带 Python 先加入 `KILL_ON_JOB_CLOSE` Job，再启动后端；Electron 退出时连同后代回收。外部 Python 必须读取 `app.asar.unpacked` 内脚本。
- `src/backend-transport.js`：Windows 一次性 stdin、macOS fd 3 传浏览器凭据；凭据不放环境变量和日志。
- `native/windows-helper/`：通过 Rust `#[path]` 复用旧 `desktop/src-tauri/src/quick/` 的双击修饰键、WGC 与 PNG 裁剪；不依赖 Tauri/WebView2。监听器靠父 stdin EOF 回收。
- `src/windows/capture.js`：Electron DIP 与原生物理像素转换。列表排除 shell 自己的窗口；测试窗口必须属于独立进程。测试夹具不能带 `windowsHide:true`，否则 Win32 枚举可能看不到它。
- `src/windows/shell-path.js`：合并继承的 PATH 与 HKLM/HKCU 的 PATH；不读取其它注册表凭据。
- `src/quick/gateway.js`：桌面托管后端不可用时报错，不自动再启动 headless。

## 数据

普通对话、模型配置、工作区目录、设置仍使用现有桌面数据根，默认 `%USERPROFILE%\.astrion\astrion-desktop`；数据目录设置仍为 `%USERPROFILE%\.astrion\rundata_settings.json`，启动前迁移机制不变。

不自动迁移旧 WebView2 或 Chrome 浏览器资料。旧 `<desktopDataRoot>/browser/` 保留；Windows Electron 元数据、密码库位于 `<desktopDataRoot>/electron/browser/`，网站 Cookie/cache 位于 Electron sessionData 下。macOS 原路径不变。

Windows 0.10.3 按用户要求补齐**手动 Chrome 导入**：主/独立窗口均启用密码 CSV 和授权 Cookie JSON 入口，同时支持当前用户的旧 DPAPI / v10-v11 AES-GCM 数据库读取；v20 如实报告不支持。共享 `chrome_worker.py` 按 extraResources 打包为 `chrome-import/windows-worker.py`，通过随包 Python 的私有管道读取；不接受 renderer 文件路径。预览、冲突选择、取消与提交在共享 Electron 模块，Cookie 通过当前 profile 的私有 CDP 目标写入并回读核验（含 partitionKey 两字段、host-only/domain、session、SameSite、Secure/HttpOnly）。解析边界、权限与验收范围见 `docs/native_browser.md`；真实合成导入与跨进程持久化回归在 `test/2026-11-13_WindowsElectron导入/`。此手动入口不代表自动迁移旧资料，也不保证任意网站登录保持。

Windows 快捷对话设置沿用 `%USERPROFILE%\.astrion\quick-entry-windows.json`，默认 Alt，支持左 Ctrl、右 Ctrl、Shift。配置启用时关闭主窗会留托盘；禁用快捷对话后关主窗退出应用。

## 安装与更新

Windows 更新走 electron-updater 的 NSIS `quitAndInstall(true, true)`：静默替换并自动重启，不走 macOS Squirrel 的准备阶段。关闭闸门先设为 installing，再停止后端并安装。静默更新必须保持开启：assistedInstaller 的 `instFilesPre` 会在可见向导中追加 APP_FILENAME；初始 `/D=` 指定自定义目录后若改用可见更新，可能安装进子目录并触发深层文件解压失败。初始手动安装仍保留选择目录的向导。

生产安装宏识别旧 Tauri 的已验证 ARP/安装目录，支持 `/UPDATE`；新文件安装成功后才移除旧 ARP 与卸载器，保留旧 runtime 和用户数据。选择目录后的进程回收只匹配该安装目录内的精确 shell/helper 路径及 Python runtime 前缀，覆盖 electron-builder 默认的全局同名进程终止逻辑。

旧 `tauri-plugin-updater 2.12.0` 的 Windows passive 更新传入 `/P /UPDATE /R /ARGS ...`，启动安装器后旧进程直接退出，Rust bridge 后续 `app.restart()` 不会执行。`/R` 与 electron-builder 的 `--force-run` 不兼容：安装宏只在同时收到 `/UPDATE`、`/R` 时记录重启要求，成功后通过 `StdUtils.ExecShellAsUser` 启动新 Electron。Electron 自身的 `--updated --force-run` 不进入此额外重启分支，避免双启动。

NSIS 使用 `useZip:true` 与 `differentialPackage:false` 配对。electron-builder 25.1.8 在差分内包开启时仍生成 7z，而安装脚本按 `useZip` 选择 ZIP 解压，单设前者会生成无法解压的安装器。

更新卸载时，旧文件先逐项移到安装目录旁的同卷暂存目录；遇到占用文件则反向移回，返回非零并阻止继续安装。这样不依赖通常位于 C 盘的 `$PLUGINSDIR`，也可更新 E/D 盘安装。文件树枚举、目录检查、移动与删除使用 Windows `\\?\` 扩展路径；暂存路径先用 `GetFullPathName` 去掉 `..`，扩展路径本身不会解析相对段。真实完整目录曾在深层技能文档的移动处失败；278 字符合成文件的恢复与更新已纳入小包回归。

此保证只覆盖**腾空旧目录时的忙文件恢复**；新文件解压前暂存已清理，不提供整个更新事务的自动回滚。普通卸载只删除 App 安装目录，运行数据在独立数据根保留。小型独立产品的安装、忙文件恢复、跨卷临时目录更新与卸载证据为 `output/windows-electron/nsis-probe/results.json`。

- Electron 自动更新清单为 `latest.yml`；下载 hash 与 Windows Authenticode 是不同机制。
- 旧 Tauri 跨壳更新仍需原 minisign 私钥签名和 `latest-windows-x86_64.json` 清单；本轮未发布到正式更新端点。
- 正式发布需同步后端版本检查读取的 JSON 与 Electron 的 YAML，保证二者版本一致；不能只上传安装器。
- 未签名实验包不等于经过 Windows Authenticode 签名的发行包。

## 隔离实验

测试使用不同资料目录和合成 localhost 页面；不配置真实模型，不调用真实账号网站，不覆盖或终止正式 Astrion。

```cmd
node --experimental-vm-modules --test test\2026-11-13_WindowsElectron\migration_contract.test.mjs
python test\2026-11-13_WindowsElectron\native_smoke.py
desktop-electron\node_modules\electron\dist\electron.exe test\2026-11-13_WindowsElectron\native_capture.mjs
node test\2026-11-13_WindowsElectron\packaged_smoke.mjs
```

包内 smoke 自行设置独立 `ASTRION_ELECTRON_USER_DATA`、`ASTRION_DESKTOP_DATA_ROOT`、`ASTRION_DESKTOP_SETTINGS_FILE`、`ASTRION_QUICK_SETTINGS_FILE`、`ASTRION_QUICK_DISCOVERY_FILE` 和合成工作区。CDP 调试端口只在该次测试启动中启用，不写入产品启动参数。

独立安装配置 `test/2026-11-13_WindowsElectron/installer-test.cjs` 使用不同 appId、产品名、exe、安装目录、快捷方式与 localhost 更新源；不访问生产旧 Tauri 登记。

`test/2026-11-13_TauriToElectron/` 已完成真实旧 Tauri 0.9.3 → Electron 0.10.1 的 13 项隔离检查：旧 WebView2 壳与包内后端，通过应用内真实更新 API 下载；保留旧正式公钥，由本机原签名工具签本地测试安装器，先验证篡改签名被拒绝且旧壳不变，再验证正确签名、旧壳退出、原目录替换、新 Electron 自动启动、普通测试数据和工作区保留、顶栏及卸载回收。结果在 `output/windows-electron/cross-shell/results.json`，绑定安装器与 NSIS 钩子 SHA-256。

跨壳测试产品为 `com.astrion.crossshell-test`，使用独立 exe、登记、安装目录和资料根。测试 wrapper 只覆盖生产钩子的旧产品名/二进制名常量；初次安装禁用旧全局同名进程清理 hook，避免影响正式 App。测试未覆盖正式产品身份原位升级或正式服务器的检查更新发布链，未修改正式更新端点。生产包入口仍为 `src/main.js`，不包含测试 bootstrap 或固定 CDP 配置。

Windows 0.10.1 包内 smoke 共 17 项通过，包含独立顶栏设置/三大键显示和操作。最小化夹具用 `EnumWindows` 枚举精确测试 PID，按可见性及 `GetWindowPlacement` 正常尺寸选主窗，再验证 `IsIconic` 与恢复；不能使用多窗口进程的 `MainWindowHandle` 猜主窗。交付回执仅接纳成功且 NSIS 源哈希与当前源码匹配的安装证据，不沿用旧版本的通过结果。

最终验证证据与未覆盖范围以 `output/windows-electron/` 的交付报告为准。隔离实测不等于真实模型对话、外网登录、混合 DPI 多屏、正式产品身份升级或长期运行全部通过。
