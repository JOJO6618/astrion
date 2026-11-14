# Windows 安装进度窗口交接（2026-11-14）

用户要求：补上更新安装期间的独立进度窗口，不由智能体运行更新测试，启动原版 0.9.3 后由用户自行点击验收。

## 实现与状态

生产源码为 `desktop-electron/build/installer-windows.nsh`、`astrion-install-guard.ps1`、`astrion-install-progress.ps1`、`astrion-install-progress.strings.json`。阶段/活动条/已用时间由独立 PowerShell WPF 窗口显示；不伪造百分比，观察新版目标路径新进程的可见窗口后收起。错误与日志保留，处理中关闭提示窗只会最小化。缺文件重试一次保留原更新与重启参数及安装目录，复用原进度会话。

只完成 NSIS 编译、PowerShell 语法解析和签名/清单装配核对。**没有运行新窗口或点击更新验收，不继承前轮安装包的测试通过结论。** 不包含完整更新事务回滚，窗口出现也不等于模型与后端全部健康。

## 打包与交付文件

在 Windows 原生环境、`desktop-electron` 目录执行：

```cmd
node node_modules\electron-builder\cli.js --config "..\test\2026-11-14_WindowsUpdateProgress\installer.cjs" --prepackaged "..\output\windows-electron\build\win-unpacked" --win --x64 --publish never
```

`installer.cjs` 保留正式 appId、入口、0.10.0 版本与官方更新源，只改变输出目录；复用完整原应用载荷，修改的是安装器。待发布目录 `output/windows-update-progress-20261114/release-0.10.0/` 包含：

- `Astrion_0.10.0_x64-setup.exe`
- `Astrion_0.10.0_x64-setup.exe.sig`（原版 Tauri 更新器需要的 minisign 签名）
- `latest.yml`（Electron 使用的 SHA512 清单，文件名同步为下划线命名）
- `release-notes-windows.txt`

`stage-package.ps1` 只做编译检查和交付暂存，不上传。包身份及源码哈希见 `output/windows-update-progress-20261114/package-receipt.json`：242,907,034 字节，SHA-256 `392e27c97e567ada19b95c1c2bf4d44ab8237cf11c88e0bd7001e53c38013c57`。未提供 Windows Authenticode 证书签名；Tauri 更新签名与 Authenticode 是两件事。

## 原版 0.9.3 已准备

沿用上一轮独立标准账户和受保护正式进程基线。外层入口为 `test/2026-11-14_Windows更新实机/invoke-isolated.ps1 -Mode PrepareManualLegacy`：只卸载上一轮该账户内的 0.10.1 测试安装、保留独立用户数据，校验原版 0.9.3 安装器 SHA256 后安装并启动；不发起升级、不点击界面、不改原版更新源。准备脚本使用 ASCII 路径，避免 Windows PowerShell 对无 BOM 源文件的中文路径解码错误。

启动回执 `output/windows-update-progress-20261114/manual-legacy-launch.json`，版本 0.9.3、PID 179904。9 个现用正式进程前后保持不变，承载当前对话的正式 App 和后端未被停止或重启。

原版 0.9.3 的更新地址编译在旧壳中，固定为官网，本地暂存不能让旧版界面取得新包。用户随后明确授权“发布吧”：已于 2026-11-14 20:33（UTC+8）备份并替换官网 0.10.0 安装器、签名、YAML 和更新说明，重生成旧 Tauri JSON 与下载清单；20:35 公网核对通过，现在可由用户点击更新体验。未改原版旧壳、未绕过签名，未代用户点击升级。

发布证据为 `output/windows-update-progress-20261114/server-publish-receipt.json` 与 `publication-verification.json`，新包哈希与上文一致，macOS 仍为 0.9.5。远端备份 `/var/www/astrion/.release-work/windows-progress-3a7344ed9d74429d8242d852f2c92353/backup`。`package-receipt.json` 保留暂存时点的 `published=false`，实际发布状态以发布回执为准。功能与视觉验收仍待用户执行。
