# Astrion Rust 后端

`astrion-headless` 为 CLI 提供 Gateway；加 `--web` 可启动 Host Web，也是 Electron 选择 Rust 时使用的入口。两个入口共用会话存储、模型目录和任务运行时。各入口已验收的功能、Python 数据对照和剩余缺口见 [迁移状态](MIGRATION_STATUS.md)。

## 从工作树根目录运行

需要 Rust stable，CLI 沿用仓库现有 Bun 环境。将 `MODEL_URL` 设为提供商的完整端点（如以 `/v1/chat/completions` 或 `/v1/responses` 结尾），`MODEL_NAME` 设为其模型 ID。有鉴权的提供商从环境变量 `ASTRION_MODEL_API_KEY` 读取密钥。

```sh
cargo build --locked --manifest-path backend-rust/Cargo.toml
backend-rust/target/debug/astrion-headless \
  --workspace "$PWD" --data-dir "$HOME/.astrion/rust-headless-dev" \
  --port 8092 --endpoint "$MODEL_URL" --model "$MODEL_NAME"
```

在同一工作区的另一终端启动 CLI。

```sh
ASTRION_API_BASE=http://127.0.0.1:8092 \
ASTRION_HOST_TOKEN_FILE="$HOME/.astrion/rust-headless-dev/host_api_token" \
  bun cli/src/main.tsx
```

网关监听 IPv4 回环地址。以上命令使用独立开发目录；`--data-dir` 也支持既有 Astrion `host/data` 布局，Python 与 Rust 进程按同一实例锁依次接管。启动 Web 时增加 `--web --static-root static`，需要已有前端构建产物。Electron 默认启动 Python，显式设置 `ASTRION_DESKTOP_BACKEND=rust` 可选择包含 Rust 可执行文件的目录包。

使用已保存的模型目录时，省略 `--endpoint` 和 `--model`。已连接的 ChatGPT 订阅模型按目录中的完整账号/模型 key 选择。启动服务会沿用指定数据目录中的账号凭据与模型缓存。

## 离线验收

首次获取构建依赖后，在工作树根目录运行以下命令。Node 用 24 或更高版本。测试仅使用临时数据、合成凭据和回环模型服务。

```sh
cargo build --offline --locked --manifest-path backend-rust/Cargo.toml
cargo test --offline --locked --manifest-path backend-rust/Cargo.toml
python3 -B test/2026-11-24_Rust后端/responses_cases.py
node --test test/2026-11-24_Rust后端/gateway_e2e.test.mjs test/2026-11-24_Rust后端/responses_gateway_e2e.test.mjs
```

`responses_cases.py` 用现有 Python 实现复核固定样本，Rust 测试逐项对照。Node 验收启动实际 Rust 二进制，用现有 TypeScript `GatewayClient` 驱动两个模型协议。Chat Completions 用例还输出工具完成到下一轮 `api_request_start` 的服务端事件时间差；该指标不包含网络或模型推理。

## 存储范围

会话、索引、草稿和 Chat 消息树沿用 Python 的磁盘布局。任务事件与轮询游标属于当前进程，重启后从已提交的会话历史继续。Agent 在每个完整工具批次后提交助手消息及工具结果。两档合成数据的首次列表耗时与就绪瞬间 RSS 见[启动基准](../test/2026-11-26_Rust启动性能/README.md)。长期运行的内存占用和发行包冷启动仍待实测。
