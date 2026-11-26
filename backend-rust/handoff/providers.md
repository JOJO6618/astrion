# Providers 订阅登录 / 额度 Handoff

日期：2026-11-27
分支：`migration/rust-backend`
工作树：`/Users/jojo/Desktop/agents/正在修复中/agents/.astrion/worktrees/migration-rust-backend-d5f6ae5a`

## 续做记录 · 2026-11-27

额度路径已挂到真实 Host Web 的 Codex 路由组。订阅查询、周价值、额度列表与兑换、OpenCode Go 均使用已登录浏览器会话。兑换额外验证 CSRF。Headless 无这些端点。

- ChatGPT 查询按账号同步，缓存 30 秒。凭证文件变化和成功兑换会失效。手动查询可跳过缓存。兑换仅向所选账号提交一次，后端生成 UUID。
- OpenCode Go 默认选择第一个启用且有凭证的 Go 连接。返回 rolling、weekly、monthly 顺序的额度窗口。401／403、404 和其他非 200 状态各有稳定错误码。使用连接与凭证摘要判断缓存有效性。
- 周历史源为 `weekly_quota.json` 的当前周期与最近 26 条归档。窗口取订阅接口最接近 7 天的有效结果。金额从 Host `api_costs`、Chat `chat/api_costs` 与旧账号直属目录的 `api_costs` 读取。只计周期内 `price.model_key` 含 GPT 的记录，按请求 ID 去重，并沿用费用账本的小数精度。采样失败保留上次的 current。
- 新增 `test/2026-11-27_Rust订阅额度/`。合成回环提供商及实际 Host/headless 授权测试使用隔离数据，未使用真实订阅账号或网络。

## 原始交接快照

下文保留续做前的文件状态和历史证据。

## 1. 当前结论

- **订阅账号登录/status/logout 的 Rust 模块已在工作树中存在**：`codex_lifecycle/{mod,accounts,oauth}.rs`，并由 `codex_oauth::router` 合并。该部分属于 ResponsesRust 二轮范围，本轮没有改动。
- **订阅额度模块目前是部分落盘，未完成、未接入、未编译验证**：只有 `providers/quota/mod.rs` 和 `providers/quota/codex_api.rs`。
- `quota/mod.rs` 声明了 `mod opencode;` 和 `mod weekly;`，但这两个文件尚不存在，当前 Rust crate 不能编译通过。
- `quota` 路由没有接入 `providers.rs` / `codex_oauth.rs` 的正常入口；当前二进制不会暴露 `/api/codex/usage`、`/api/codex/weekly-value`、`/api/codex/reset-credits*`、`/api/subscription/opencode-usage`。
- 没有创建 `/api/...` 额度隔离夹具，没有 Rust quota 测试；本轮也没有运行 `cargo test` 或任何新二进制验证。

## 2. 已落盘文件

### 2.1 登录 / 账号生命周期（已存在，非本轮实现）

| 文件 | 行数 | 状态 |
|---|---:|---|
| `backend-rust/src/providers/codex_lifecycle/mod.rs` | 514 | 路由与流程状态机已写入 |
| `backend-rust/src/providers/codex_lifecycle/accounts.rs` | 360 | 账号索引/upsert/logout 辅助已写入 |
| `backend-rust/src/providers/codex_lifecycle/oauth.rs` | 371 | 浏览器/设备码 OAuth 辅助已写入 |

当前 `codex_oauth::router` 中已有：

```rust
let lifecycle = super::codex_lifecycle::router(config.codex.clone());
...
Router::new()
    .route("/api/codex/accounts", get(accounts_endpoint))
    .route("/api/codex/settings", get(settings::get_settings).post(settings::save_settings))
    .route("/api/codex/models/refresh", post(refresh_endpoint))
    .route_layer(middleware::from_fn(require_admin_session))
    .with_state(state)
    .merge(lifecycle)
```

### 2.2 额度模块（部分落盘）

| 文件 | 行数 | 状态 |
|---|---:|---|
| `backend-rust/src/providers/quota/mod.rs` | 346 | 路由/状态/缓存骨架已写入 |
| `backend-rust/src/providers/quota/codex_api.rs` | 186 | usage / reset-credits / consume 处理器已写入 |
| `backend-rust/src/providers/quota/opencode.rs` | — | **缺失** |
| `backend-rust/src/providers/quota/weekly.rs` | — | **缺失** |
| `backend-rust/src/providers/quota/tests` 或独立 fixture | — | **缺失** |

已写入的固定契约入口：

- `GET /api/codex/usage`
- `GET /api/codex/weekly-value`
- `GET /api/codex/reset-credits`
- `POST /api/codex/reset-credits/consume`
- `GET /api/subscription/opencode-usage`

`Endpoints` 已支持测试注入 `usage_url`、`reset_credits_url`、`reset_credits_consume_url`、`opencode_usage_url`。

## 3. 正常入口接线现状

### `backend-rust/src/providers.rs`

当前只有：

```rust
mod codex_lifecycle;
mod codex_oauth;
```

**没有** `mod quota;`，因此额度模块完全没有参与构建。

### `backend-rust/src/providers/codex_oauth.rs`

`codex_router(config)` 当前只 merge 了 `codex_lifecycle::router`，没有 merge quota router。

### `backend-rust/src/main.rs`

- Host Web 分支：
  - `providers::router(provider_config.clone())`
  - `providers::codex_router(provider_config)`
  - `crosspet.router()`
  - `project_storage.router()`
- Headless 分支：
  - `providers::router(...)`
  - 没有 `codex_router`
  - `usage::quota_router()` 是普通 token 用量统计，不是 Codex 订阅额度。

因此正确接线仍应保持 Host-only：在 `codex_router` 内合并 quota，避免把额度端点暴露给 headless。

建议的待应用接线（未执行）：

```rust
// providers.rs
mod quota;

// codex_oauth.rs::router
let quota = super::quota::router(config.clone());
...
.with_state(state)
.merge(lifecycle)
.merge(quota)
```

不要把 quota 直接合并到 `providers::router`：该 router 同时用于 headless，而 Python 的 `codex_auth_bp` 和 `opencode_quota_bp` 都只在 `app_legacy.py` 注册。

## 4. 已有验证证据

以下是此前轮次已经实际执行过的验证，不是本轮新跑的结果：

1. **Codex settings 往返（Rust 真二进制 + Python 生产 blueprint）**

```bash
python3.11 -B "test/2026-11-26_Rust订阅设置/codex_settings_roundtrip.py" \
  "backend-rust/target-master/debug/astrion-headless"
```

结果：

```text
PASS: codex settings precedence, writes/clears, auth, restarts, Python takeover and headless 404
```

连续运行两次均通过。覆盖代理优先级、写入/清空、默认版本、未知字段保留、Python takeover、headless 404。

2. **Python Codex 账号生命周期契约快照**

```bash
python3.11 -B "test/2026-11-26_Rust订阅生命周期/contract_snapshot.py"
```

结果（此前记录）：

```text
Ran 16 tests in 0.081s
OK
```

覆盖 status、logout、browser/device 登录 envelope、reconnect、usage/reset-credits 的 Python 契约。仅临时目录、合成 JWT、fake subscription query。

3. **当前 Rust 二进制**

最近可见的 `backend-rust/target-master/debug/astrion-headless` 时间约为 `2026-11-27 00:17`。该二进制在 quota 模块接线前构建；不能据此声称 quota 端点可用。

4. **隔离测试文件现状**

- `test/2026-11-26_Rust订阅设置/codex_settings_roundtrip.py`
- `test/2026-11-26_Rust订阅生命周期/contract_snapshot.py`
- `test/2026-11-26_Rust订阅任务/gateway_oauth.rs`（存在，但未在 `backend-rust/Cargo.toml` 中看到对应 test target）
- 没有 `test/2026-11-26_Rust订阅额度/` 目录或 fixture。

## 5. 已知阻塞 / 待验项

### 编译阻塞

1. `quota/mod.rs` 声明：
   - `mod opencode;`
   - `mod weekly;`
   两个文件均缺失，wired 后首先会报 unresolved module。
2. `codex_api.rs` 使用了 `query.refresh`，但 `mod.rs` 的 `AccountQuery` 只有 `account_id` 字段；当前源码本身不一致，wired 后会编译失败。
3. `quota/mod.rs` 的 `use super::{... object_body ...}` 当前未使用；不是阻塞，但会产生 warning。
4. `quota/mod.rs` 有重复的文档注释行（`Host-only quota router...` 出现两次），仅文档瑕疵。

### 契约/实现待修

1. `/usage` 缓存 TTL 当前使用 `USAGE_TIMEOUT`（10 秒）；Python `subscription_queries` 的 usage TTL 是 **30 秒**。需要拆成独立 `USAGE_CACHE_TTL = 30s`。
2. `wham_request` 把所有 `is_connect()` 错误都映射为“连接 ChatGPT 超时”；Python 对 `ConnectError` 走通用异常文本，仅 `ConnectTimeout` 是中文连接超时。需要区分。
3. `weekly.rs` 尚未实现：
   - `GET /api/codex/weekly-value` 快照；
   - `refresh=1` 非阻塞触发采样；
   - 7 天 ±15% 周窗口选择；
   - `api_costs` 中 `price.model_key` 含 `gpt` 的金额聚合、周期滚动、history 26 条、`weekly_quota.json` 0600 原子写。
4. `opencode.rs` 尚未实现：
   - `opencode.ai` + `/zen/go` 的 base_url 校验；
   - 未指定 provider 时选首个启用 Go 连接；
   - `provider_id` 未知/禁用/非 Go/无 key 的 200 `success:false` 错误码；
   - 30 秒 key 指纹缓存；
   - 上游 401/403→`invalid_credential`，404→`not_subscribed`，非 200→`http_N`；
   - `rolling/weekly/monthly` 窗口转换与百分比取整。
5. `codex_api.rs` 的错误语义部分待对齐：
   - 缺凭证在 Python 的 usage 缓存签名阶段可能表现为 502，而不是 401；当前实现映射 401 `no_credentials`，需按契约确认。
   - reset-credits 上游非对象 JSON、consume 的 `account_id` 回退主账号等边界尚未测试。
6. quota 路由没有接入，因此当前 Host 二进制 `/api/codex/usage` 等仍会 404。

### 验证待办

- 应用 `mod quota;` 与 `codex_router` merge 后先 `cargo check`。
- 补 `quota/opencode.rs`、`quota/weekly.rs`。
- 新建独立额度 fixture（只使用临时目录、合成 access/refresh token、回环 axum 上游）：
  - usage 成功、30 秒缓存、`refresh=1`、未知账号 404、非 200→502、无 `rate_limit`→502；
  - reset-credits GET 与 consume：路径、JSON body、`credit_id` 缺失 400、uuid4、成功后 usage 缓存失效；
  - OpenCode：Go/非 Go、未知 provider、缓存、401/403/404/非 200、窗口排序；
  - weekly-value：预置状态快照、刷新采样、7 天窗口、ledger 金额、history 滚动。
- 运行 `cargo test`（含现有 providers_contract，确认接线未破坏原路由）。
- 用新二进制做仅回环/临时数据的 Host 路由冒烟；不要连接真实 opencode.ai / chatgpt.com。

## 6. 重要的行为约束（已核对契约，仍需随实现验证）

- 所有额度端点都是 Host-only，`WebAuth::protect_api` + BrowserSession/admin。
- 账号选择：query `account_id` 优先于 body，空串=主账号；未知 id 404，不回落其他账号。
- `usage` 使用选定账号 token；临期 token 在同一账号锁内刷新；请求头不要带 `OpenAI-Beta`。
- `reset-credits/consume` 生成 uuid4 `redeem_request_id`，POST 不重试，成功后失效 usage 缓存。
- `weekly-value` 的金额唯一来源是 `api_costs`，不新增计数；失败保留旧 current。
- OpenCode 只接受 `https://opencode.ai/zen/go...`，key 永不下发，缓存键需包含 key 指纹。
- 不要将任何真实账号、真实 `providers.json`、真实 OAuth 端点带入测试。
