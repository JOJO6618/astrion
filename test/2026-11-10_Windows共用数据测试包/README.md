# Windows 共用数据测试包 · 2026-11-10

给正式版做验收用的**调试构建**：界面与后端来自当前 `main`，但**运行数据目录与已安装正式版完全相同**，方便直接在真实对话、真实工作区、真实浏览器资料上验证本轮改动。

## 与 `2026-11-09_WindowsBrowser/package_test.py` 的区别

| 项 | r1/r2/r3 探针包 | 本包 |
| --- | --- | --- |
| 数据根 | `ASTRION_DESKTOP_DATA_ROOT` 指向同级 `validation-profile` | **不覆盖任何 `ASTRION_DESKTOP_*`**，落到 `%USERPROFILE%\.astrion\astrion-desktop` |
| 快捷对话设置 | 包内 `validation-profile\quick-entry-windows.json` | 沿用 `%USERPROFILE%\.astrion\quick-entry-windows.json` |
| identifier | `com.astrion.desktop.browser-test-20261109-r3` | `com.astrion.desktop.shared-test-20261110`（与正式版不同，不抢单实例锁） |
| 启动保护 | 无 | 检测到 `astrion-desktop.exe` 在运行就报错退出 |

identifier 与正式版不同是刻意的：相同 identifier 时 Tauri 的单实例锁会让测试版直接激活正式版窗口。代价是两个进程可以并存，所以必须靠启动器拦一道——**同一份数据目录只能有一个写者**（AGENTS §12 单写者不变量）。

## 构建与校验

```cmd
python test\2026-11-10_Windows共用数据测试包\package.py build
python test\2026-11-10_Windows共用数据测试包\package.py assemble
python test\2026-11-10_Windows共用数据测试包\package.py smoke
```

- `build`：`desktop/scripts/prepare_backend.py` staging 后端 → `tauri build --debug --no-bundle --features browser-test --config windows-shared.conf.json`，成功后把源码输入的哈希与 exe 哈希写进 `_experiments/windows-shared-test-20261110/build-success.json`。
- `assemble`：校验 receipt 与当前源码一致才落盘到 `output/Astrion-Windows-Shared-Test-20261110/`；同时检查打包内容不含 `.env`/`secrets.json`/`providers.json`/`host_api_token`/`passwords.dpapi`，并按 `static/dist/.vite/manifest.json` 核对前端资源齐全。
- `smoke`：用**临时隔离数据根**启动包内 exe，验健康检查、前端资源、browser 工具能力、原生窗口可见、关闭按钮与后端回收。**不会**读写共用数据目录。

## 本轮结果（2026-11-10）

- `build`：staging 81.5 MB，`tauri build --debug` 8m14s，exe sha256 `1c8571765e69bae44b4893841c96bcf2121a4c2b2239fd4c6fce7051c9b8f763`。
- `assemble`：`output/Astrion-Windows-Shared-Test-20261110/`（406.5 MB），前端 manifest 115 条全部存在，无私有配置。
- `smoke`：9 项全过（embeddedBackendHealthy / isolatedRootUsed / workspaceIsolated / entryAssets / browserToolsSupported / nativeWindowRegistered / nativeWindowVisible / closeButtonAccepted / backendReapedOnClose）。

两个踩过的坑：

1. `/api/browser-tools` 带 `@with_workspace_data`，没建/选工作区时它自己会报错，容易被误判成浏览器不受支持；smoke 里必须先 create + select 工作区。
2. identifier 不能与正式版相同：Tauri 单实例插件按 identifier 建锁，相同 identifier 会让测试版直接激活正式版窗口，看起来像「没启动」。

## 数据根未被覆盖的依据

- 启动器不设置 `ASTRION_DESKTOP_DATA_ROOT` / `ASTRION_DESKTOP_SETTINGS_FILE` / `ASTRION_QUICK_SETTINGS_FILE`。
- `rundata.rs` 优先级：环境变量 > `%USERPROFILE%\.astrion\rundata_settings.json` > 默认 `%USERPROFILE%\.astrion\astrion-desktop`；本机该设置文件不存在，因此落到默认目录，与已安装正式版一致。
- 正式版实际运行的是 `%LOCALAPPDATA%\Astrion\astrion-desktop.exe`，数据根同为上述默认目录。

## 已知前置条件与风险

- 启动前必须完全退出正式版（含托盘图标）。测试版与正式版共用对话、工作区、日志、浏览器 Cookie/密码/历史与快捷对话设置。
- 这是 debug 未发布构建：不签名、不安装、不接更新通道；性能不代表 release 版。
- 建议使用前自行复制备份 `%USERPROFILE%\.astrion\astrion-desktop`。回退方式就是关掉本包、重新打开正式版，数据是同一份。
