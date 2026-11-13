# 真实 Tauri → Electron 隔离升级（2026-11-13）

已执行旧 Tauri 0.9.3 → Electron 0.10.1 应用内升级，13/13 通过。完整结果：`output/windows-electron/cross-shell/results.json`；日志：`cross-shell-run.log`。测试没有触碰正式 Astrion 安装，没有发布更新。

## 产品与资料边界

- 独立产品 `Astrion CrossShell Test`，identifier/appId `com.astrion.crossshell-test`。
- 旧/新可执行文件均为独立的 `astrion-crossshell-legacy.exe`。
- 安装在 `output/windows-electron/cross-shell/installed`，测试后已正常卸载；资料保留在 `cross-shell/run`。
- localhost 清单/下载端口 19878；旧壳 CDP 19877，新 Electron 测试 bootstrap CDP 19879。固定端口和 bootstrap 只在测试包中，生产入口仍为 `src/main.js`。
- 旧初始安装使用空 `legacy-test-hooks.nsh`，避免旧生产 hook 全局终止同名正式后端。运行态旧壳、WebView2、Python、updater 和退出逻辑仍走原实现。
- Electron 的 `electron-include.nsh` 只覆盖旧产品/二进制名，复用真实生产 NSIS 钩子和 `/UPDATE /R` 分支。

## 夹具与执行

`tauri-test.json` 保留原正式公钥，仅改独立身份、测试版本和 localhost endpoint；HTTP 放行仅属于该测试配置。`build_legacy.mjs` 使用原生 Rust/MSVC 与现有离线缓存构建旧壳，保留并恢复原构建 exe。已生成旧安装器，不需要重复这一步。

在 Windows 原生环境、工作区根目录运行：

```cmd
node test\2026-11-13_TauriToElectron\build_legacy.mjs
```

`electron-test.cjs` 是独立 Electron 测试安装器配置。执行前需存在已构建、由本机原签名工具签名的 `output/windows-electron/cross-shell/electron/Astrion-CrossShell-Electron-0.10.1.exe` 及其 `.sig`。签名调用使用既有私钥文件作为工具输入，不读取或打印私钥内容；不将私钥复制到工作区。

准备好独立新旧安装器与签名后运行：

```cmd
node test\2026-11-13_TauriToElectron\cross_shell.mjs
```

脚本会实际安装并启动测试产品，通过旧 App 的 `POST /api/desktop/update/install` 调用 Rust bridge 和真实 `tauri-plugin-updater`。首次提供篡改签名，确认插件拒绝、旧进程存活且 exe 哈希不变；第二次提供正确签名，确认下载验签后旧进程退出，原目录替换并自动启动 Electron。随后检查数据/工作区哨兵、顶栏、后端回收、卸载器退役与普通卸载后保留资料。

旧 updater 的 passive 参数是 `/P /UPDATE /R /ARGS ...`，Windows 启动安装器后直接退出，后续 `app.restart()` 不会执行。生产 NSIS 必须显式识别 `/UPDATE /R`，安装成功后以当前用户启动 Electron；仅处理 Electron 的 `--force-run` 无法满足旧壳重启要求。

## 绑定证据

```text
旧测试安装器 SHA-256:
e6ae465f20cfbecf504ca4331c126612d8fee972c2beb939eb322c2cd1839a09
新测试安装器 SHA-256:
ed821af453270040fce68c27356d625331f8cd591a230d390d7ffe5ad0192680
生产 NSIS 钩子 SHA-256:
1f99e908219ab860ae00c354da457f9f59bb9d3a02e04ace7280f99c5a9e4d04
```

这些哈希绑定本轮结果；修改钩子后不能自动继承旧通过记录。这里的新安装器是独立测试产品，不是交付的正式身份安装器，两者不可混淆。

尚未验证正式产品身份原位替换和正式服务器的检查更新发布链；未向正式更新源上传 JSON/YAML、安装器或签名。普通数据检查使用测试哨兵与工作区配置，不等于所有真实用户数据场景验收；旧浏览器资料未迁移。
