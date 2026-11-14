# Windows 正式身份真实界面更新验收

2026-11-14 在 Windows 11 上完成两条真实升级：原版 Tauri 0.9.3 经正式服务器升级到正式 Electron 0.10.0；完整 Electron 0.10.0 经 localhost 测试源升级到未发布完整 0.10.1。两条均实际点击应用界面，由原生更新器安装并自动启动新版本，没有直接调用安装接口、手动补装或手动启动新版。

## 保护边界

原版 0.9.3 安装钩子会结束当前用户的全部同名后端，仅换安装目录不足以隔离。此实验使用独立本地标准用户，保留原版 appId、exe 名、公钥及钩子，安装于该用户默认目录。承载对话的正式 0.10.0 和后端不参与测试。

脚本绑定本轮指定测试 SID，禁止直接在主用户运行旧安装器。`invoke-isolated.ps1` 前后核对 `protected-processes.json` 的 9 个正式进程 PID、启动时间和路径。跨用户子进程再次验证 SID、profile 及非管理员身份。随机密码仅通过 Windows DPAPI 加密保存在主用户 LOCALAPPDATA 的专属凭据文件中，私有 ACL 限制访问；不得复制明文到仓库、日志或报告。

测试账户两天后到期；到期不等于自动删除账户或资料。已关闭测试程序、恢复测试副本官方更新源，保留账户、安装和证据供复查。不要无授权删除用户资料。

## 文件职责与执行顺序

所有命令在 Windows 原生环境、仓库根执行；WSL 沙箱不能运行这些账户/进程接口。以下记录本轮执行顺序，**当前账户已完成升级，不能从头重跑或覆盖现有证据**。复用到其他机器需先设计新的隔离账户、SID、保护基线、端口和输出目录。

1. 管理员执行 `provision.ps1` 创建标准用户。脚本发现同名账户会拒绝重置；本轮已创建，不再重复运行。
2. 主用户运行 `invoke-isolated.ps1 -Mode Probe` 验证隔离身份，再用 `-Mode InstallLegacy` 安装原版 0.9.3，用 `-Mode LaunchLegacy` 启动。父执行器经 CMD 设置测试用户环境后启动 PowerShell，使用 GUID 绑定完成回执，不以空的 ExitCode 判断成功。
3. `node test\2026-11-14_Windows更新实机\update-ui.mjs --install` 通过本次旧 WebView2 的 CDP 19973，正常点击侧栏、个人菜单、“软件更新”和“立即更新并重启”。不 mock 响应；日志中的安装 API 请求是界面自然发出的请求。用隔离执行器 `-Mode Observe` 核验自动启动的新版与载荷。
4. 构建下一版完整测试包：在 `desktop-electron` 目录执行 `node node_modules\electron-builder\cli.js --config "..\test\2026-11-14_Windows更新实机\next-version.cjs" --win --x64 --publish never`。派生配置保留生产入口、身份与 NSIS，只改测试版本、产物目录和 localhost 源，不修改正式 `windows-release.json`。
5. `-Mode PrepareElectron` 保存第一轮结果、停止已通过的测试副本、仅给测试安装配置本地更新源，备份原文件并放置数据哨兵，启动测试 0.10.0 的 CDP 19974。`node test\2026-11-14_Windows更新实机\electron-update.mjs` 提供有界 localhost 19976 更新源并真实点击升级，等待新进程，严格核验版本、载荷、关键哈希、数据保留及后端响应。退出时关闭测试源。
6. `-Mode CrossCopy` 验证同一测试账户共用默认 Electron profile 时，另一目录的程序是否将激活交给已运行副本。
7. `-Mode FinalizeTest` 仅停止测试安装目录内的进程，恢复官方 `app-update.yml`，保留全部资料。父执行器再次核对正式进程基线。
8. `powershell -NoProfile -ExecutionPolicy Bypass -File "test\2026-11-14_Windows更新实机\verify-final.ps1"` 只读核验脚本语法、两轮回执、正式进程保护、测试副本已退出和临时更新服务器已停止。内部 `-Mode VerifySources` 在测试账户自身读取安装文件并核对官方更新源，不改变 ACL；主账户无权直接读取这些文件。17:20 已通过，12 个脚本语法无误，结果 `final-verification.json`。

`-Mode MonitorUpdate` 是只读错误窗口监视器：仅观察测试 SID 可见的安装器/Astrion `#32770` 对话框，不点击或关闭错误，不终止进程；20 分钟上限，也可由输出目录内 stop 文件结束。`inspect-ui.mjs` 仅读取本次测试页面。CDP 仅通过测试启动参数开启，不写入产品。

## 实际结果（UTC+8）

| 场景 | 点击更新 | 新版自动启动 | 核验 |
|---|---|---|---|
| 原版 0.9.3 → 正式源 0.10.0 | 16:12:24 | 16:14:26，约 2 分 2 秒 | PE 0.10.0.0；15,475 文件无缺失/长度不符；旧 ARP 退役；后端正常 |
| Electron 0.10.0 → 测试源 0.10.1 | 16:40:20 | 16:44:13，约 3 分 53 秒 | PE 0.10.1.0；15,475 文件无缺失/长度不符；exe/app.asar 哈希匹配；数据哨兵保留；16:44:38 完成健康核验 |

第二条完整测试安装器 242,902,745 字节，SHA-256 `fb70e6a138ad4dd0fe1748fe9e3bf27970b3e789b9098b70c7b0933e49920724`，未发布。第一条最终正式包 242,902,755 字节，SHA-256 `0b33808c1d5badeb111bbfd81272cc5c5eb41cbceff92dd3060f130d7a9da4b8`。同版本曾覆盖发布多个包，应按哈希区分。

两轮均未捕获到安装错误弹窗。17:09 完成收尾，9 个受保护正式进程前后不变。安装中会有明显无窗口等待期，不能用短超时或暂时缺文件直接判失败，也不能超时后盲目重跑。

## 证据

统一在 [output/windows-update-real-20261114](../../output/windows-update-real-20261114/)：

- [调查报告](../../output/windows-update-real-20261114/windows-update-investigation.md)
- 第一轮：[结果](../../output/windows-update-real-20261114/migration-first-result.json)、[真实点击日志](../../output/windows-update-real-20261114/ui-actions.jsonl)、[更新弹窗](../../output/windows-update-real-20261114/legacy-update-dialog.png)
- 第二轮：[结果](../../output/windows-update-real-20261114/electron-update-result.json)、[真实点击日志](../../output/windows-update-real-20261114/electron-ui-actions.jsonl)、[真实下载请求](../../output/windows-update-real-20261114/electron-feed-requests.json)、[更新弹窗](../../output/windows-update-real-20261114/electron-update-dialog.png)
- [多副本实验](../../output/windows-update-real-20261114/cross-copy-result.json)、[正式进程保护基线](../../output/windows-update-real-20261114/protected-processes.json)、[测试收尾](../../output/windows-update-real-20261114/cleanup-result.json)

## 结论边界

已验证独立新标准账户中的正式身份原版跨壳升级和 Electron 后续升级。第二条使用测试源，不等于下一版已发布或正式服务器链路已再次验收。本轮没有新增生产更新补丁，也没有 commit、push 或发布。

同账户多副本接管已复现：后启动程序退出码 0，已有副本及后端保持正常。这可能解释部分“点了没打开”，不能解释历史载荷缺失，也未证明是原账户每次故障的原因。原账户失败现场仍未复现，不能把新账户通过说成原账户已根治。

静态风险仍存在：旧文件移动完成后即清除备份，不提供整个更新事务回滚；模板解压失败可直接 Quit 跳过 customInstall；guard 重试未完整保留安装/重启参数，关闭错误弹窗可能丢失线索。生产机制与限制见 [docs/windows_electron.md](../../docs/windows_electron.md)。
