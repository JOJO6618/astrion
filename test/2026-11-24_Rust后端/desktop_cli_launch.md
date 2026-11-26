# Rust 桌面与 CLI 启动验收

生产桌面与 CLI 在等价验收前继续默认启动 Python。普通入口可分别用 `ASTRION_DESKTOP_BACKEND=rust` 或 `ASTRION_CLI_BACKEND=rust` 显式选择 Rust，并沿用原 Python 数据根、`host/data`、`host/logs`、`config` 和对话 JSON。已有目录可直接继续使用。启动器沿用数据目录、日志目录、部署配置目录和工作区注册文件的环境覆盖。Rust 后端用跨进程写者锁持有数据目录。

设置 `ASTRION_RUST_EXPERIMENT=1` 启用独立试运行。实验数据目录和 Electron profile 首次使用时为空，随后可从同一路径恢复。目录标记只用于保护该实验启动模式。桌面实验包的应用标识为 `com.astrion.desktop.rust-experiment`，实验模式使用指定的独立 profile。已登记的工作区使用原有 ID、标签和路径。

## Electron 目录包

从工作树根运行，预先安装根目录和 `desktop-electron` 的 Node 依赖以及 Rust 工具链。

```sh
npm --prefix desktop-electron run build:rust:dir
```

脚本构建 Rust sidecar 和干净的前端，产物在 `desktop-electron/.cache/rust-experiment/build-*/output/`。可用 `ASTRION_RUST_BACKEND_BIN` 和 `ASTRION_RUST_STATIC_ROOT` 提供预构建产物。此命令只生成目录包，配置关闭签名、公证和发布。包的 `runtime/rust/` 内含 sidecar、前端（含 Vite manifest 和入口 CSS）、内置工作流、内置技能和公开配置种子。部分技能脚本有各自的工具链依赖。

macOS 下将 `app` 指向构建输出的 `Astrion Rust Experiment.app` 的绝对路径，再用独立目录启动。

```sh
experiment="$(mktemp -d)"
mkdir "$experiment/profile" "$experiment/data" "$experiment/workspace"
ASTRION_DESKTOP_BACKEND=rust \
ASTRION_RUST_EXPERIMENT=1 \
ASTRION_ELECTRON_USER_DATA="$experiment/profile" \
ASTRION_DESKTOP_DATA_ROOT="$experiment/data" \
ASTRION_DESKTOP_INITIAL_WORKSPACE="$experiment/workspace" \
  "$app/Contents/MacOS/Astrion Rust Experiment"
```

从源码 `npm --prefix desktop-electron run dev` 使用同一组实验变量。源码启动前运行 `node desktop/scripts/prepare_rust_backend.mjs`、`npm run build -- --emptyOutDir` 和 `cargo build --locked --manifest-path backend-rust/Cargo.toml`。需要另一个资源目录时，准备脚本可接受一个新的目录作为参数，并用绝对路径设置 `ASTRION_RUST_RESOURCE_ROOT`。

壳先验证 `/api/host-mode-enabled` 的 Rust 标识和子进程 PID，再完成原有的同源 Cookie/CSRF 桌面预登录。桌面退出时 macOS 发送 `SIGTERM`，Windows 关闭专用 stdin 控制管道，壳最多等待 5 秒，再对超时进程强制回收。实验模式跳过更新、数据迁移、Quick Chat 原生监听和系统通知启动。普通桌面启动沿用设置中选定的数据根，优先使用已登记的默认工作区。两种模式均可显式设 `ASTRION_DESKTOP_INITIAL_WORKSPACE`。

同一构建脚本提供 Windows x64 目录通道，需在 Windows 上使用本机工具链。Rust 分支使用原生 helper 的 Job Object 守护和 stdin 停止信号，安装脚本按路径识别并停止受管 Rust 进程。Python 默认分支继续使用原有 Python guardian。macOS 目录已实测构建。Windows Job Object 与安装链仍需原生验收。

## CLI

在源码树准备 Rust 二进制和独立的内置资源目录。CLI 沿用 Bun。将 `repo` 指向工作树的绝对路径。

```sh
cargo build --locked --manifest-path backend-rust/Cargo.toml
node desktop/scripts/prepare_rust_backend.mjs
experiment="$(mktemp -d)"
mkdir "$experiment/data" "$experiment/workspace"
(
  cd "$experiment/workspace"
  ASTRION_CLI_BACKEND=rust ASTRION_RUST_EXPERIMENT=1 ASTRION_RUST_DATA_ROOT="$experiment/data" \
    bun "$repo/cli/src/main.tsx"
)
```

实验 CLI 默认端口为 8092。普通入口继续使用 `WEB_SERVER_PORT` 或默认 8091。两者都可用 `ASTRION_API_PORT` 指定端口。CLI 对已有 Rust 服务使用选定数据目录的 `host_api_token` 认证，成功后将工作区列表交给原有引导流程处理。CLI 自启单个 headless 进程，后续 CLI 可复用。启动日志位于 `host/logs/cli_spawned_server.log`。已有服务可通过 `ASTRION_API_BASE` 与 `ASTRION_HOST_TOKEN_FILE` 显式配对，此时由服务启动方管理其生命周期。编译后的单文件 CLI 可通过绝对路径 `ASTRION_RUST_BACKEND_BIN` 和 `ASTRION_RUST_RESOURCE_ROOT` 使用旁置的 Rust 程序和程序资源。

桌面与 CLI 均可用 `ASTRION_RUST_BACKEND_BIN` 选择指定可执行文件，并用 `ASTRION_RUST_MODEL_ENDPOINT` 与 `ASTRION_RUST_MODEL` 指定固定测试模型。测试模型凭据来自 `ASTRION_MODEL_API_KEY`。离线回归用回环模型替身。

## 离线回归

构建 Rust 二进制和前端后，在工作树根运行。测试启动本地 sidecar，使用合成数据。桌面测试运行壳层启动编排和 HTTP 预登录，以替身承接 Electron 窗口调用。执行环境需允许回收测试启动的子进程。

```sh
node --test test/2026-11-24_Rust后端/{desktop_startup,desktop_guardian,cli_startup}.test.mjs
```

原位续写测试通过 Python `ConversationManager` 生成并回读合成会话，可用 `ASTRION_TEST_PYTHON` 指定解释器。可用 `ASTRION_HEADLESS_BIN` 指定被测二进制，用 `ASTRION_RUST_STATIC_ROOT` 指定提供完整 Vite 构建的静态根。测试覆盖端口归属、隔离数据目录、工作区注册 ID、桌面预登录、包内技能和工作流、CLI 复用、专用停止管道，以及桌面正常关停后的会话恢复。
