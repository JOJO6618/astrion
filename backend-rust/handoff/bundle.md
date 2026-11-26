# 正常 macOS Rust 目录包（Astrion.app）交接

本文保存暂停交接时的构建路径与历史命令。1.0.0 已在隔离数据上构建并启动，当前 build ID、严格签名和实包业务验收见[迁移状态](../MIGRATION_STATUS.md)。

## 1. 目标与边界

- 交付形态：可正常直接启动的 `Astrion.app`，包内**只有 Rust 后端**（`Contents/Resources/runtime/rust`），启动后默认使用包内 Rust。
- 沿用正式标识：`appId=com.astrion.desktop`、`productName=Astrion`、版本取仓库版本。本地目录包使用 ad-hoc 签名，不公证、不发布、不替换已安装应用。
- 与两个既有入口区分：
  - `npm run build:dir`（`scripts/run_builder.mjs`）= 正式 Python 包，会 staging 内嵌 Python；
  - `npm run build:rust:dir`（`scripts/build_rust_dir.mjs`）= 隔离实验包，`Astrion Rust Experiment` / `com.astrion.desktop.rust-experiment`，要求专用空数据根、跳过控制桥与更新策略。
- 本轮允许改动：`desktop-electron/`、`desktop/scripts/`、测试目录；未改 `main.py` 与 `server/`。

## 2. 已落地改动

| 文件 | 改动 | 说明 |
|---|---|---|
| `desktop-electron/src/rust-backend.js` | `resolveDesktopBackend` 默认后端判定 | 有 `runtime/rust` 且**没有** `runtime/python` 时，默认 `mode='rust'`；开发形态（无 `resourcesPath`）仍默认 Python；显式 `ASTRION_DESKTOP_BACKEND` 始终优先；`python` + 只有 Rust 运行时仍抛原有错误（防御性保留） |
| `desktop-electron/scripts/build_rust_app.mjs` | 新增 | 正常目录包构建：`cargo build --release` → 根前端 `npm run build -- --emptyOutDir` → `stageRustBackend` → electron-builder `dir`（`identity:null`、`notarize:false`、`publish:'never'`）；产物 `desktop-electron/dist-rust/mac-arm64/Astrion.app`；构建后自检 runtime 只有 `rust`、无 `runtime/python`、前端 manifest 与资源种子存在，并打印二进制字节数与 SHA256 |
| `desktop-electron/package.json` | 新增脚本 | `"build:rust:app": "npm run build:quick-native && node scripts/build_rust_app.mjs"` |
| `test/2026-11-26_Rust目录包/verify_bundle.py` | 新增 | 选择逻辑预演（`--logic-only`）、包静态校验（`--app`）、隔离启动验证（`--launch`） |

`build_rust_app.mjs` 接受的复用开关（用于共享产物、避免重复编译）：

- `ASTRION_RUST_BACKEND_BIN`：复用已构建二进制（跳过 cargo）；
- `ASTRION_RUST_STATIC_ROOT`：复用已构建前端（跳过 `npm run build`）；
- `ASTRION_RUST_CARGO_TARGET_DIR`：复用 release target 目录（默认 `desktop-electron/.cache/rust-app/cargo-target`）；
- `ASTRION_BUILD_ID`：固定包内 build id（默认随机 UUID，写入 `extraMetadata.astrionBuildId`）。

## 3. 路径契约

| 项 | 路径 |
|---|---|
| 构建入口 | `npm --prefix desktop-electron run build:rust:app`（脚本**不接受任何参数**，`process.argv.length !== 2` 直接抛错） |
| 产物 | `desktop-electron/dist-rust/mac-arm64/Astrion.app`（arm64） |
| 包内后端 | `Contents/Resources/runtime/rust/astrion-headless` |
| 包内前端 | `Contents/Resources/runtime/rust/static`（含 `dist`、`dist/.vite/manifest.json`、`entry-styles.json`） |
| 包内资源 | `runtime/rust/{config,workflows,agentskills}` |
| 隔离实验包（未受影响） | `desktop-electron/.cache/rust-experiment/build-*/output/mac-arm64/Astrion Rust Experiment.app` |
| release 编译缓存 | `desktop-electron/.cache/rust-app/cargo-target`（当前已存在部分依赖产物，约 396 MB） |

本工作树**没有** `desktop-electron/dist/`，因此不会与正式 DMG/ZIP 产物冲突。

## 4. 隔离与“不干扰在跑服务”的机制（按代码核对）

- **单实例锁**：`main.js` 的 `requestSingleInstanceLock()` 以 Electron `userData` 为作用域；用 `ASTRION_ELECTRON_USER_DATA=<专用目录>` 即得到独立锁，不会与已安装应用/其他工作树实例互相顶掉。
- **数据根**：正常形态 `resolveRunDataRoot()` 优先读 `ASTRION_DESKTOP_DATA_ROOT`（绝对路径），设置后不会再读写 `~/.astrion/rundata_settings.json`，也不会触发迁移（`hasPendingMigration` 在显式 env 下返回 false）。
- **后端进程锁**：`astrion-backend.lock` 位于数据根 `host/data`，隔离数据根即隔离该锁。
- **端口**：`resolveBackendPort` 优先读 `<userData>/backend-port.json`，隔离 profile 后端口不共享；冲突时回退随机端口。
- **更新策略缓存/日志/浏览器数据**：均落在隔离 profile 或数据根下。
- **实验形态未改动**：`ASTRION_RUST_EXPERIMENT=1` 仍要求三个专用空目录、仍跳过控制桥/更新策略/迁移/快捷窗。

预演启动所需的三件套（正常形态）：

```
ASTRION_DESKTOP_DATA_ROOT=<tmp>/data
ASTRION_ELECTRON_USER_DATA=<tmp>/profile
ASTRION_DESKTOP_INITIAL_WORKSPACE=<tmp>/ws
# 不设 ASTRION_DESKTOP_BACKEND —— 要验证“只有 Rust 时默认 Rust”
# 不设 ASTRION_RUST_EXPERIMENT —— 走正常形态
```

## 5. 已验证证据（本轮，未编译）

`python3.11 -B test/2026-11-26_Rust目录包/verify_bundle.py --logic-only`，用临时 fixture（`runtime/rust` 内符号链接到 `backend-rust/target-master/debug/astrion-headless` 与 `static/`）直接调用真实 `resolveDesktopBackend`，全部通过：

| 断言 | 结果 |
|---|---|
| 只有 Rust 运行时 → 默认 `rust` 且 `experimental=false` | 通过 |
| 显式 `ASTRION_DESKTOP_BACKEND=python` → 报错（不会静默回退） | 通过 |
| 实验形态未给专用目录 → 报错 | 通过 |
| 实验形态给全三个目录 → `experimental=true` | 通过 |
| 启动计划参数完整（含 `--web`） | 通过 |
| 同时存在 `runtime/python` → 默认仍 `python` | 通过 |

启动计划实测（同一 fixture，`rustDesktopLaunch`）：

```
command = <Resources>/runtime/rust/astrion-headless
args    = --web --static-root <...>/runtime/rust/static --workspace <ws>
          --data-dir <dataRoot>/host/data --port 12345
cwd     = <ws>
env     = ASTRION_DATA_ROOT / ASTRION_RESOURCE_ROOT / DEPLOY_CONFIG_DIR /
          HOST_WORKSPACES_FILE / ASTRION_DESKTOP_PLATFORM=darwin / ASTRION_DESKTOP_ARCH=arm64
```

即：包内二进制、包内 static、隔离数据根、隔离注册表都在启动参数里被显式传递，没有落到仓库路径或真实数据根。

## 6. 交接暂停时的历史未完成项

1. **正式包未构建**：本轮按要求保持暂停。此前（11-26 06:xx）用 release 试编时源码正处于他人集成中间态，报 `chat_mode/handoff.rs` 缺 `materials`/`service` 模块与 `project_git/workspace/checkout.rs` 借用错误；现已由共享 check/build 通过，但**尚未重新跑 release**。
2. **包静态校验未跑**：`verify_bundle.py --app` 需要真实产物（`dist-rust/mac-arm64/Astrion.app`）。
3. **启动 PID 验证未跑**：`--launch` 已写好但未执行。需要确认的实际链路：Electron 主进程 PID → 包内 `astrion-headless` 子进程 PID → `<profile>/backend-port.json` 端口 → `/api/host-mode-enabled` 的 `backend=rust` 与 `pid` 匹配 → 带 Bearer 的 `/api/runtime/sessions` 返回 200。
4. **退出方式未验证**：沙箱对本会话的子进程禁发任何信号（`kill -0` 都是 EPERM），故 `--launch` 设计为通过 `--inspect` + DevTools `Runtime.evaluate(require('electron').app.quit())` 让应用自己退出；该路径**从未执行过**，需在首次预演时确认。
5. **`verify_bundle.py` 的 `buildId` 字段是空壳**：脚本目前没有解 asar（`app.asar` 被当成 `{}`），需要补 `@electron/asar` 读取包内 `package.json` 的 `astrionBuildId` 后才能作为证据。诊断 `mtime/sha256` 已有，buildId 仍未取证。
6. CLI 已使用常规 `cli/bin/astrion` 进入隔离 TUI，由默认 Rust 模式启动目录包的后端并通过 Bearer 读取工作区。 [CLI 可复跑脚本](../../test/2026-11-27_Rust快捷对话/README.md)
7. 本地目录包先让 electron-builder 装配文件，再用系统 ad-hoc 身份签封嵌套 Mach-O、Swift 辅助程序和整个 App。构建结束严格验证签名。正式发布入口使用独立的开发者身份配置。
8. **包内无 `node_modules`**：`files: ['src/**','package.json']`，`electron-updater` 只被 `bridge.js` 惰性 `import()`。正常形态会启动控制桥，更新类端点一旦被调用会因缺模块失败；验收不覆盖更新，需在文档中写明。
9. **release 编译的沙箱前置**：`ring`/clang 在本沙箱会因临时文件权限失败（`clang: unable to make temporary file: Operation not permitted`）。可用组合（未写入脚本，需在命令里给）：

```
X=/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin
SDK=/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk
TMPDIR=<工作树内目录> CC=$X/clang CXX=$X/clang++ SDKROOT=$SDK \
CARGO_TARGET_AARCH64_APPLE_DARWIN_LINKER=$X/clang \
  npm --prefix desktop-electron run build:rust:app
```

10. **旧实验包不可复用**：`.cache/rust-experiment/build-*` 两个 11-25 的 `Astrion Rust Experiment.app`（291/308 MB）早于当前源码，其中一个 runtime 缺 `config/workflows/agentskills`，只能作为历史产物，不得当验收凭据。

## 7. 恢复后的执行顺序（等通知代码可构建）

1. 复用共享二进制与前端的快速预演（避免重复编译）：

```sh
ASTRION_RUST_BACKEND_BIN="$PWD/backend-rust/target-master/debug/astrion-headless" \
ASTRION_RUST_STATIC_ROOT="$PWD/static" \
  npm --prefix desktop-electron run build:rust:app
python3.11 -B test/2026-11-26_Rust目录包/verify_bundle.py --app "$PWD/desktop-electron/dist-rust/mac-arm64/Astrion.app"
```

2. 需要 release 形态时去掉上面两个环境变量、补第 6 节第 9 条的工具链变量，复用 `.cache/rust-app/cargo-target` 继续增量编译。
3. 隔离启动与 PID 验证：`verify_bundle.py --app ... --launch`；若 CDP 退出路径不成立，记录实测 PID 后交给有权限的一方回收，不要向在跑服务发任何操作。
4. 任何一步失败都只记录，不要改用实验包或旧包顶替，也不要把 debug 二进制的结论当成发布包结论。
