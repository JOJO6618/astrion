# Windows Electron 桌面版

Windows 与 macOS 共用 `desktop-electron/` 主进程、双视图窗口、浏览器控制器与快捷对话。迁移于 2026-11-14 以提交 `7cb8f769` 合入 `main`；**Windows Electron 0.10.0 已于 2026-11-14 正式发布上线**（安装包、`latest.yml`、`latest-windows-x86_64.json` 与下载页均指向 0.10.0）。旧 `desktop/` 保留作原生模块与运行时 staging 来源，不在本轮删除。

## 正式版与测试包编号

Windows Electron 当前线上正式版为 **0.10.3**（2026-11-15 发布，安装包 246,741,069 字节，`latest.yml`、`latest-windows-x86_64.json` 与下载页均指向 0.10.3）；macOS 同期为独立的 0.10.4。源码版本与更新说明记录在 `desktop-electron/windows-release.json`；macOS 继续独立维护。迁移验证期使用的 `0.10.1`、`0.10.2`、`0.10.3` 曾作为未发布的测试包编号，正式版与本轮测试包同号属巧合，历史测试产物保留原编号不改写。

发布前的 Windows 正式版是 Tauri 壳 **0.9.3**（2026-11-11），因此已发布 0.10.0 的更新说明同时覆盖了 0.9.4 / 0.9.5 的共享层新增能力；0.10.1 的说明记录更新策略与安装窗口调整；0.10.3 的说明覆盖 0.10.2 新增能力（对话级 Git 工作树、默认工作区、浏览器跟随开关、目标模式单次完全访问）与 0.10.3 自身更新（标签拖出独立窗口、外部工作树、存储空间等），因 0.10.2 未单独发布。

下文的 0.10.1 顶栏修复、0.10.2 标题栏配色、0.10.3 手动导入均指迁移验证期的测试包，这些改动已统一纳入正式版 0.10.0。历史报告、安装器和便携目录保留原编号，不能覆盖其中已有的试用资料；正式版输出到独立交付目录。

合入时已拉取浏览器跟随智能体功能，解决 preload 能力声明冲突，同时保留 `followAi` 与 Windows `credentialFileImport`。合并后前端构建通过，跟随回归 9 项、迁移/主题回归 29 项、导入/重启回归 35 项通过；此前交付的 0.10.3 测试包未重新构建，不包含本次拉取的新跟随功能。Git 合入证据见 `output/windows-electron/git-integration/final-result.json`。设置下一正式版版本号不等于已经打包或发布，发布仍需单独授权。

## 构建

使用 Windows x64 原生 Node 与 Rust MSVC 工具链。Chrome 导入需要 `desktop/src-tauri/runtime/python/python.exe` 和随解释器安装的 `cryptography`。

```cmd
set "ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/"
npm ci --prefix desktop-electron --no-audit --no-fund
node desktop-electron\scripts\build_windows.mjs
```

构建先生成共享前端与 Windows native helper，再用 `x86_64-pc-windows-msvc` 构建 Rust 后端，并将 `astrion-headless.exe`、Web 静态资源及 WSL2 安装脚本打入 `resources/runtime/rust`。`resources/runtime/python` 的解释器供私有 Chrome 导入 worker 使用。Host 服务启动由包内 Rust sidecar 和 Windows Job guardian 负责。已有本次前端与 helper 时可用 `--skip-frontend --skip-native`，已有本次 Rust 二进制或 Web 静态目录时可指定 `ASTRION_RUST_BACKEND_BIN` 或 `ASTRION_RUST_STATIC_ROOT`。仅目录包加 `--dir`，构建固定 `--publish never`。

- Windows 版本及说明：`desktop-electron/windows-release.json`，由 builder 的 `extraMetadata.version` 注入。
- macOS 版本：`desktop-electron/package.json`，独立维护。
- Windows 配置：`desktop-electron/electron-builder.win.cjs`。
- 输出：`output/windows-electron/build/win-unpacked/astrion-desktop.exe` 与 `Astrion-<version>-windows-x64-setup.exe`。
- 构建回执 `output/windows-electron/build-receipt.json` 记录 shell 与 Rust 后端的路径和 SHA-256。包内验收另有回执。

Windows 普通用户可能无法解压 winCodeSign 归档中的 macOS 符号链接。`test/2026-11-13_WindowsElectron/prepare_sign_cache.py` 可从已下载归档仅准备 Windows rcedit 工具，排除不用的 Darwin 目录；不关闭签名配置、不修改系统权限。

## 发布（2026-11-14 首次全链跑通）

服务器 `/var/www/astrion/downloads/`（`59.110.19.30`），公网基址 `https://astrion.cyjai.com/downloads/`。关键约束：`regen_manifest.py` 的 Windows 分支只认**下划线 Tauri 命名** `Astrion_<ver>_x64-setup.exe` + `.sig`，它据此生成 `latest-windows-x86_64.json` 与 `downloads.json` 的 windows-x64 条目；electron-builder 的连字符产物不会被识别。发布步骤：

1. `node desktop-electron/scripts/build_windows.mjs`，产物 `output/windows-electron/build/Astrion-<ver>-windows-x64-setup.exe`。
2. 跑 `node test\2026-11-13_WindowsElectron\packaged_smoke.mjs`，`success=true` 才继续。
3. 补签：`cd desktop` 后 `npx tauri signer sign -f "%USERPROFILE%\.astrion-desktop-keys\updater.key" -p "" <setup.exe>`；`keynum`（base64 解码 `[2:10]`）必须等于 `desktop/src-tauri/tauri.conf.json` 的 `plugins.updater.pubkey`。不带 `--app-version` 可发布，但清单不能启用 `requireSignedVersion`。
4. 安装器按 Tauri 命名复制一份（签名同名复制），并**把 `latest.yml` 的 `url`/`path` 改写成该 Tauri 文件名**。字节没变，sha512 仍匹配，electron-updater 与旧 Tauri 共用同一份文件，服务器上只存一份。
5. 上传 exe / `.sig` / `latest.yml` / `release-notes-windows.txt`，再执行 `python3 /var/www/astrion/downloads/regen_manifest.py`。说明由 `windows-release.json` 的 notes 逐条加 `- ` 生成。
6. 公网核对：`latest.yml` 版本、`latest-windows-x86_64.json` 版本与签名、`downloads.json` windows-x64 版本/字节数、exe HEAD 200 且长度一致、notes UTF-8（cmd 显示 GBK 乱码不算错）。

注意：`read_notes()` 会把说明截断到 2000 字符；`regen_manifest.py` 同时重写 `latest-mac.json`/`downloads.json` 的 mac 条目，发布 Windows 不影响 mac 版本。0.10.0 已按此链路发布（安装器 242,899,717 字节，sha256 `211bb625d25c50bf4f93298f874d718d7133d5df85ee38b6950f1f7c6830352b`）。

0.10.3 已按此链路发布（2026-11-15，安装器 246,741,069 字节，build_id `d25ce084-b177-459e-aa1e-e473656a85ea`，14 条说明，含启动期会话预热修复 `27711439`）。**本次包内冒烟未跑通**：`packaged_smoke.mjs` 在“已注册工作区但 `conversation_tabs.json` 无标签”的全新数据目录下等不到内容视图；实测注入与登录预热正常，chrome 页 Vue 已挂载但 `tabs=0`。尚未区分是产品缺口（空标签无兜底）还是测试夹具需补初始标签，待排查后再作为后续版本验证项。

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
- 旧 Tauri 跨壳更新仍需原 minisign 私钥签名和 `latest-windows-x86_64.json` 清单；0.10.0 起已发布到正式更新端点。
- 正式发布需同步后端版本检查读取的 JSON 与 Electron 的 YAML，保证二者版本一致；不能只上传安装器。
- 未签名实验包不等于经过 Windows Authenticode 签名的发行包。

### NSIS 初始化崩溃（0.10.0 首次发布时踩到，已修）

症状：应用内更新下载完成、旧 App 关闭后再也不启动，重开仍是旧版本；手工运行安装器同样是 `0xc0000005`，事件日志指向 `System.dll`，`$PLUGINSDIR` 里只有 `System.dll` + `UAC.dll`。

根因：electron-builder 的 `multiUser.nsh` 在 `HKCU\Software\<APP_GUID>` 还没有 `InstallLocation` 时，会在 `.onInit` 调用 `SHELL32::SHGetKnownFolderPath`；本机（Windows 11 26200 + nsis-resources 3.4.1 插件）该调用稳定崩溃。**只有“全新 appId”会走到这里**——之前所有测试安装都已存在该注册键，因此从未暴露。

修复：`desktop-electron/build/installer-windows.nsh` 新增 `preInit`（比 `initMultiUser` 更早执行）：`$INSTDIR` 为空时取 `$LocalAppData\Programs\${APP_FILENAME}`，并在 `InstallLocation` 缺失时写入该值，让模板走注册表分支；安装结束时 `include/installer.nsh` 会按最终 `$INSTDIR` 回写。`BUILD_UNINSTALLER` 下不执行（否则构建机会写入临时目录）。

复现与验证：用独立 appId + 极小 payload 的安装器（`cache/nsis-diag-app`）做前后对照，不直接在正在使用的正式 App 上跑（安装器对同名进程会执行 `taskkill`）。

### 安装不完整导致启动失败（2026-11-14 第二次踩到，已加固）

症状：0.9.3 → 0.10.0 应用内更新后 App 关闭且再也起不来；`%LOCALAPPDATA%\Astrion` 里 `astrion-desktop.exe` 与绝大多数文件都在，但 `resources.pak`、`snapshot_blob.bin`、`v8_context_snapshot.bin`（连同 `vk_swiftshader*`、`vulkan-1.dll`）缺失，`debug.log` 报 `Error loading V8 startup snapshot file`，且没有任何失败日志。

现场取证：一次成功安装中，这 6 个文件正是**最后一批**写入（比其余文件晚约 2 分钟）；失败现场缺少这些文件，且 `install-incomplete.log` 不存在。这支持“载荷未完整落盘”的判断，但没有证据确认中断触发源。安全软件同时运行不能证明是它造成的，安装过程中的暂时缺文件也不能单独判为安装失败。

关键机制（用 `cache/nsis-cb-quit.nsi` 实测）：electron-builder 的 `include/extractAppPackage.nsh` 在 `nsisunz::Unzip` 返回非 `success` 时只弹 MessageBox 然后 `Quit`；**`Quit` 不执行 `.onGUIEnd` / `.onInstFailed`**，所以写在 `customInstall` 里的校验在那种路径下根本不会运行。

加固（两层，均在 `desktop-electron/build/`）：

1. `installer-windows.nsh` 的 `AstrionCheckRequired` + `customInstall`：校验 `astrion-desktop.exe`、`resources.pak`、`snapshot_blob.bin`、`v8_context_snapshot.bin`、`resources\app.asar`，以及（仅当 `resources\runtime` 存在时）`resources\runtime\python\python.exe`；缺失则用同一份 `$PLUGINSDIR\app-$packageArch.zip` 原地重解压一次，仍缺失则写 `install-incomplete.log`、`SetErrorLevel 3`、非静默弹窗并 `Abort`。
2. `astrion-install-guard.ps1`（仅静默安装时由 `customInit` 启动）：独立进程，等安装器退出后复核同一组文件。最初发布包使用共享 TEMP 状态、缺失重跑一次并关闭阻塞对话框；后续源码已改为每次安装独立状态目录、保留错误对话框与日志、重试保留参数与目录，并接续独立进度窗口（见文末“安装进度窗口”）。独立进程自身也可能启动失败或被终止，不能保证必然恢复。

此前隔离 appId 验证：`cache/nsis-diag-app` 三轮——正常安装无误报、人为删文件后自动重解压修复、归档不可用时 `exit 3` + 失败日志；`cache/crosscheck-app`（`--prepackaged` 真实 1.2GB 载荷、appId `com.astrion.crosscheck.20261114`、装到 E 盘隔离目录）——完整安装 15,476 文件，删掉 3 个关键文件后重跑恢复为同样的 15,476 文件。后续正式产品身份、真实界面验收见文末。

仍未定论：原账户那次解压为何中断没有直接证据（无崩溃事件、无日志），不能确认实际进入了模板 `Quit` 分支。前一轮基线包的 guard 重试只传 `/S`、关闭错误对话框，存在目录/重启参数与诊断线索丢失风险；后续进度窗口改动已处理这两个源码问题，但实机验收待用户执行。加固仍不提供整个安装事务回滚。

## 隔离实验

测试使用不同资料目录和合成 localhost 页面；不配置真实模型，不调用真实账号网站，不覆盖或终止正式 Astrion。

```cmd
node --experimental-vm-modules --test test\2026-11-13_WindowsElectron\migration_contract.test.mjs
python test\2026-11-13_WindowsElectron\native_smoke.py
desktop-electron\node_modules\electron\dist\electron.exe test\2026-11-13_WindowsElectron\native_capture.mjs
node test\2026-11-13_WindowsElectron\packaged_smoke.mjs
```

包内 smoke 使用独立 Electron profile、运行数据和合成工作区，并让 Chromium 的外部请求经过仅当次测试使用的本地代理。测试读取正常入口的 Rust PID，核验实际可执行文件位于目录包内、父进程为随包 guardian、后者由当前 Electron 启动，同时验证带 Bearer 的本地 Gateway 请求和关闭后的端口回收。完整操作和实际后端 SHA-256 记录于 `output/windows-electron/packaged-smoke-latest.json`。CDP 调试端口仅用于本次烟测。

独立安装配置 `test/2026-11-13_WindowsElectron/installer-test.cjs` 使用不同 appId、产品名、exe、安装目录、快捷方式与 localhost 更新源；不访问生产旧 Tauri 登记。

`test/2026-11-13_TauriToElectron/` 已完成真实旧 Tauri 0.9.3 → Electron 0.10.1 的 13 项隔离检查：旧 WebView2 壳与包内后端，通过应用内真实更新 API 下载；保留旧正式公钥，由本机原签名工具签本地测试安装器，先验证篡改签名被拒绝且旧壳不变，再验证正确签名、旧壳退出、原目录替换、新 Electron 自动启动、普通测试数据和工作区保留、顶栏及卸载回收。结果在 `output/windows-electron/cross-shell/results.json`，绑定安装器与 NSIS 钩子 SHA-256。

跨壳测试产品为 `com.astrion.crossshell-test`，使用独立 exe、登记、安装目录和资料根。测试 wrapper 只覆盖生产钩子的旧产品名/二进制名常量；初次安装禁用旧全局同名进程清理 hook，避免影响正式 App。测试未覆盖正式产品身份原位升级或正式服务器的检查更新发布链，未修改正式更新端点。生产包入口仍为 `src/main.js`，不包含测试 bootstrap 或固定 CDP 配置。

Windows 0.10.1 包内 smoke 共 17 项通过，包含独立顶栏设置/三大键显示和操作。最小化夹具用 `EnumWindows` 枚举精确测试 PID，按可见性及 `GetWindowPlacement` 正常尺寸选主窗，再验证 `IsIconic` 与恢复；不能使用多窗口进程的 `MainWindowHandle` 猜主窗。交付回执仅接纳成功且 NSIS 源哈希与当前源码匹配的安装证据，不沿用旧版本的通过结果。

历史隔离验证证据在 `output/windows-electron/`；后续正式身份真实更新证据在 `output/windows-update-real-20261114/`。隔离实测不等于真实模型对话、外网登录、混合 DPI 多屏、原账户故障恢复或长期运行全部通过。

## 正式身份真实界面升级验收（2026-11-14）

为保护承载对话的正式 0.10.0，使用独立 Windows 标准用户运行未改身份、未改公钥和清理钩子的原版 0.9.3 安装器。仅隔离目录不足以保护现有后端：原版旧安装钩子会结束当前用户的所有同名后端。脚本入口与保护方式见 `test/2026-11-14_Windows更新实机/README.md`。

- **原版 0.9.3 → 正式源 0.10.0：通过。** 正常点击界面“软件更新 → 立即更新并重启”，16:12:24 点击，16:14:26 新版自动启动（UTC+8，约 2 分 2 秒）。没有直接调用安装接口、手动补装或手动启动新版。PE 版本为 0.10.0.0，15,475 个载荷文件无缺失或长度不符，旧 Tauri ARP 已退役，后端正常响应。证据 `migration-first-result.json`、`ui-actions.jsonl`。
- 本次核对的最终正式 0.10.0 安装器为 **242,902,755 字节**，SHA-256 `0b33808c1d5badeb111bbfd81272cc5c5eb41cbceff92dd3060f130d7a9da4b8`。同版本曾覆盖发布多个包，不能只按版本号对比历史结果。
- **Electron 0.10.0 → 未发布 0.10.1：通过。** 完整生产入口、appId、exe 名与 NSIS 钩子保持一致，仅测试副本使用 localhost 更新源及独立版本/产物目录。16:40:20 点击，16:44:13 新版自动启动（约 3 分 53 秒），16:44:38 完成健康核验。15,475 个载荷文件无缺失或长度不符，exe/app.asar 哈希匹配，新进程与数据保留哨兵均核验通过。证据 `electron-update-result.json`、`electron-ui-actions.jsonl`。这不等于已发布 0.10.1 或验证了下一次正式服务器发布。
- **同账户多副本接管：已复现。** 测试账户已运行安装版 0.10.1 时，从另一目录启动 0.10.0，后启动进程退出码 0，原进程和后端继续运行。证据 `cross-copy-result.json`。共用默认 Electron profile 的副本可能把激活交给已有进程；这能解释部分“点击后没打开”的现象，但不能解释历史缺文件，也未证明是原账户每次失败的原因。

所有输出均位于 `output/windows-update-real-20261114/`。两轮均未捕获到测试用户安装错误弹窗，9 个受保护正式进程的 PID、启动时间和路径前后不变。收尾已关闭测试副本、恢复其官方更新源；保留账户、测试资料与证据，未修改正式版本文件或重新发布。本轮新增的是验收脚本和文档；原账户故障触发源仍未复现，不能宣称原账户已根治。

## 安装进度窗口（2026-11-14 后续改动，待用户实机验收）

前一节两条真实升级结果属于此前基线安装器，不覆盖本节新源码。用户要求本轮不代做更新测试，准备原版 0.9.3 由其亲自点击；只完成编译、脚本语法解析、签名与交付装配核对，不宣称进度 UI 或新版更新链已验证。

- 入口仍为静默 NSIS，避免 assistedInstaller 可见向导改变安装目录。`customInit` 在用户 TEMP 下创建独立会话目录，放入 guard、`astrion-install-progress.ps1` 及独立 UTF-8 双语文案 `astrion-install-progress.strings.json`。WPF 窗口由独立 PowerShell STA 进程承载，不依赖被替换的应用、后端、WebView2 或安装插件目录。
- NSIS 发送 preparing / closing / installing / checking / starting 阶段；窗口显示阶段、活动条与已用时间，不将粗略阶段映射成虚构百分比。颜色取 Windows 原生语义画刷，支持系统高对比度；处理中关闭窗口会最小化，不取消安装。
- 解压、登记与必需文件检查完成后写本次 `installed.txt`。guard 等待安装器退出，再核验标记、退出码与必需文件。需要重启时，只有在目标安装路径发现本次新启动进程且其可见 Chromium 窗口出现，才发布 ready 并自动收起进度窗；这只表示观察到窗口，不是完整后端或模型健康验收。未请求重启的静默安装以 complete 收尾。
- 安装器仍存活时最多观察 20 分钟，超时显示未确认结果并保留进程，不并发重装；启动窗口观察上限 3 分钟，超时区分为安装完成但未见新版窗口。错误留在进度窗中，提供日志目录入口，绝不关闭原安装器错误对话框来掩盖错误。
- 缺文件仅自动重跑一次，保留原更新/重启参数，移除旧 `/D=` 后将同一安装目录作为最后参数追加。guard 通过仅子进程继承的 `ASTRION_INSTALL_PROGRESS_DIR` 复用原会话和窗口，NSIS 比对会话内安装目录，成功启动前清除此变量，避免下一次更新误复用。该机制仍不提供完整更新事务回滚。
- 阶段、错误与日志留在本次 TEMP 会话目录（`phase.txt`、`detail.txt`、`attention.txt`、`install.log`、`progress-window.log`）；同一用户不同安装不共享重试计数或覆盖脚本。

本轮打包入口 `test/2026-11-14_WindowsUpdateProgress/installer.cjs`，复用此前完整生产 0.10.0 目录包，只重编安装器，输出 `output/windows-update-progress-20261114/`，不覆盖前轮包。`package-receipt.json` 绑定当前四个安装源码哈希，明确 `functionalUpdateTestRun=false` 与 `published=false`。原版 0.9.3 的官网地址编译在旧壳中，新增窗口必须随新安装器更新到该地址后才能从原版界面获得；本地构建/签名不等于已获发布授权。

### 进度窗口补包已发布（2026-11-14 20:35，UTC+8）

用户明确授权发布后，同版本 0.10.0 补包已上传官网：242,907,034 字节，SHA-256 `392e27c97e567ada19b95c1c2bf4d44ab8237cf11c88e0bd7001e53c38013c57`。已同步 `.exe.sig`、`latest.yml`、Windows 更新说明并运行服务器清单生成器。公网核对旧 Tauri JSON 的签名/版本/URL/说明、Electron YAML、官网 Windows 下载条目及安装器 HTTP 200/Content-Length 均与本地一致；服务器安装器哈希一致。macOS 仍为 0.9.5，条目未改变。

证据为 `output/windows-update-progress-20261114/server-publish-receipt.json`、`publication-verification.json`。原包/签名/说明/清单备份在服务器 `/var/www/astrion/.release-work/windows-progress-3a7344ed9d74429d8242d852f2c92353/backup`。上文 `package-receipt.json` 是发布前的不可变装配快照，其 `published=false` 不代表当前官网状态。原版 0.9.3 已在独立标准账户启动，由用户自己点击更新；本轮未执行进度窗口或新补包的升级功能验收，不能继承旧包的通过结论。

### 用户验收反馈后的窗口收尾修正（2026-11-14，源码改动未构建）

用户亲自从原版 0.9.3 更新已发布的进度窗口补包，确认进度窗出现且新版成功重启；反馈启动前闪过 PowerShell 控制台，新版已出现后进度窗仍未收起，最终误报未检测到新版窗口。用户要求直接改代码，不运行测试、不创建待办。

- 旧识别同时依赖 PowerShell `Get-Process.Path`、进程时间及非空窗口标题。32 位监督器查询 64 位进程模块路径可能失败，Electron `BaseWindow` 也可能没有原生标题；这些条件都有漏判风险，未读取现场日志来断言唯一触发原因。
- 监督器现在直接枚举可见顶层 Chromium 窗口，通过 `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)`、`QueryFullProcessImageNameW` 与 `GetProcessTimes` 核对程序绝对路径和创建时间，不读对方模块、不要求窗口标题。时间下限取安装器创建的本次会话文件，避免监督脚本启动较晚时排除已经启动的新版。识别成功记录实际窗口 PID，再发布 ready 由进度窗自动关闭。
- NSIS 改用创建时带 `SW_HIDE` 的 `ExecShell` 启动监督器，在可用时使用 Sysnative 下的原生 PowerShell；进度窗子进程使用 `ProcessStartInfo(UseShellExecute=false, CreateNoWindow=true)`，避免仅依赖 PowerShell 启动后解析 `-WindowStyle Hidden`。

此次按用户要求仅修改源码与文档，未运行测试、未重新构建、未再次发布。官网仍为上一节已发布的安装器；本节修正需要后续重新打包发布才会进入安装流程。

## Windows / macOS 共用更新策略（2026-11-14，源码实现）

两平台均使用 `desktop-electron/` 的策略客户端和启动门槛。客户端上报当前版本、独立构建编号、平台和架构，官网可按具体版本、版本范围或构建编号决定是否必须更新，以及是否只允许官网下载。强制策略的检查位于主窗口、数据迁移、Python 后端和快捷对话启动之前；原生安装入口另行拒绝 manual 策略，不能仅依赖界面隐藏按钮。

首次无法取得策略且无有效缓存时放行；请求失败但仍有身份匹配且未过期的缓存时继续遵守缓存。协议、匹配配置、官网独立服务部署和构建编号约束见 [桌面更新策略契约](desktop_update_policy.md)。

此节改动仅完成源码接线和阅读校对，按用户要求未运行测试、构建、部署或发布。必须先部署官网策略服务，再完整构建并发布支持协议的新客户端；不能复用旧目录包只重编 NSIS。官网现有 0.10.0 补包不包含此策略功能，也不包含上一节尚未重包的窗口收尾修正。
