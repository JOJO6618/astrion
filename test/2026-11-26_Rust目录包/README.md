# macOS Astrion 1.0.0 Rust 目录包验收

2026-11-27，在 macOS arm64 上使用临时工作区、临时数据根、独立 Electron profile 和回环 SSE 模型完成。目录包位于 `desktop-electron/dist-rust/mac-arm64/Astrion.app`。该次验收构建编号为 `12104b17-3ff2-4873-85a3-686ce4c19470`，包内 `astrion-headless` 的 SHA-256 为 `553fe6f632b9943ad1915c4727475efb5bfc53a8d6c431b1978c85d6877d3365`。

- ASAR、版本、应用标识、可执行文件、前端 manifest、配置、Quick Chat 原生组件与更新策略资源共 10 项静态校验通过。运行目录内仅有 `runtime/rust`。
- 真实 Electron 渲染器显示桌面启动提示。选择稍后后，从侧栏点开旧档，内容区显示不兼容提示和迁移入口。主动迁移后，侧栏状态与当前消息自动刷新，合成旧 JSON 的原始字节保持一致。
- 从输入框发送两条消息。首轮模型返回真实 `read_file` 调用，回环上游收到工作区文件的结果和冻结提示词。第二轮收到已保存的首轮结果与工具调用。
- 两次应用启动分别使用包内 Rust 子进程 83652 和 83764。第一次正常退出时 Rust 退出码为 0。使用相同测试 profile 与数据根重启后保留对话标签和消息，第二次正常退出。
- CLI 与桌面启动回归共 6 项通过，含 CLI 正常默认 Rust、复用监听器、保留待迁移旧档，及桌面同一数据根显式迁移、工具续写和重启读回。
- 用真实 `cli/bin/astrion` 和 Bun 1.4.2 在隔离 PTY 启动。CLI PID 93923 使用包内 Rust PID 93927，在指定临时工作区创建 1 条会话。探针关闭终端并回收测试后端。
- 两轮目录包验收使用合成数据。界面视觉、Windows 原生启动、签名、公证和更新安装另行验收。

Chrome 视图负责桌面启动提示，避免第一次打开应用尚无对话标签时漏弹。关闭桌面时，退出流程在包内 Rust 停止后结束 Electron 主进程。

## 复跑

在工作树根执行。真实 Electron 探针要求 macOS 有图形会话并允许回环监听，执行后会退出测试应用。测试保留原始合成 JSON，从临时目录的日志可定位失败。目录包使用本机 ad-hoc 签名。

```sh
npm --prefix desktop-electron run build:rust:app
python3.11 -B test/2026-11-26_Rust目录包/verify_bundle.py
node --test test/2026-11-27_对话存储迁移界面/migration.test.mjs
node test/2026-11-26_Rust目录包/electron_acceptance.mjs
python3.11 -B test/2026-11-27_RustCLI正式入口/cli_smoke.py \
  desktop-electron/dist-rust/mac-arm64/Astrion.app/Contents/Resources/runtime/rust
ASTRION_HEADLESS_BIN="$PWD/desktop-electron/dist-rust/mac-arm64/Astrion.app/Contents/Resources/runtime/rust/astrion-headless" \
  node --test --test-concurrency=1 test/2026-11-24_Rust后端/{cli_startup,desktop_startup}.test.mjs
```
