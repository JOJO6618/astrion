# Responses、文件前像与 Codex 订阅登录交接

工作树为 `migration/rust-backend`。本文记录暂停前的实现和验证结果。订阅生命周期的生产改动在 `backend-rust/src/providers/codex.rs` 和 `backend-rust/src/providers/codex_lifecycle/`。主侧负责共享路由、OAuth 可见性和二进制构建。本轮未提交。

## 订阅账号生命周期

- 挂载接口为 `codex_lifecycle::router(config: CodexConfig) -> Router`。已由主侧从 `codex_oauth::router` 合并，并纳入 Web API 的 session 与 CSRF 保护。承接 `GET /api/codex/status`、`POST /api/codex/login/start`、`POST /api/codex/login/device/start`、`GET /api/codex/login/poll`、`POST /api/codex/login/cancel` 和 `POST /api/codex/logout`。`/api/codex/accounts` 和模型刷新保留现有入口。
- `status` 按显式连接 ID 查询，空选择使用主账号。未知账号返回 404，注册仍在但凭证缺失时保留连接 ID 和显示哈希。无账号时返回可展示的空状态。状态读取可展示凭证是否齐全及访问令牌有效期，响应不包含 token。
- 浏览器流程使用双栈回环监听、PKCE S256、随机 state 和一次性回调。设备码流程在后台申请、轮询并交换授权码。两种登录方式共用当前流程，重复启动返回 `409 login_in_progress`。轮询读取进度，模型刷新结束后才发布 `completed`。浏览器回调收到错误 state 时返回 400 并保留当前流程，上游报错响应只使用经过整理的消息和 HTTP 状态，避免回显授权码或 token。
- 写入前按稳定身份匹配连接。重复登录更新原连接，重连同时检查原连接和授权身份，身份不符返回 `reconnect_account_mismatch`。凭证提交与取消互斥，提交前取消阻止落盘，提交后由后台完成模型刷新。登录提交和登出复用现有每账号锁，和 token 轮换及模型缓存写入串行。登出只删除选定账号，清理 legacy 模型段时保留代理、客户端版本与其他设置。
- 复用现有 `codex_oauth::oauth_client`、`account_lock`、`refresh_account`、`OAUTH_CLIENT_ID`、`CodexConfig::token_url()` 和 `write_private_json_blocking`，从 `Outcome.result["refreshed"]` 读取模型刷新结果。生命周期使用严格注册表读取，损坏的索引会明确报错。

## 已有验证证据

- Python 契约快照 16 项全部通过，使用临时凭证、模拟登录和模拟配额服务。命令为 `python3.11 -B test/2026-11-26_Rust订阅生命周期/contract_snapshot.py`，输出在 `backend-rust/target-tmp/codex-lifecycle-python.log`。
- Rust 隔离 fixture 4 项全部通过，使用同一份生产路由与模块、合成 JWT 和回环 HTTP 上游。覆盖账号状态和选择器、缺凭证、登出错误与 legacy 配置保留、浏览器双栈回调和 PKCE、并发启动、浏览器成功后等待模型刷新、重连身份不符、设备码 pending 兼容、交换过程中取消或登出，以及登出等待同账号模型刷新。输出在 `backend-rust/target-tmp/codex-lifecycle-fixture/fixture-results.log`。本机普通 clang 链接受临时文件权限限制，隔离测试用系统链接器完成。
- 主侧重建的 `backend-rust/target-master/debug/astrion-headless` 已通过 Web 进程挂载测试。验证实际 session 登录、CSRF、明确选择账号、IPv4/IPv6 回调、并发启动冲突、浏览器交换时取消、设备码启动及失败、按账号登出与 legacy 设置保留。OAuth 出站请求止于回环代理，代理未转发到外部。脚本和输出位于 `backend-rust/target-tmp/codex-lifecycle-fixture/mounted-lifecycle.mjs` 和 `backend-rust/target-tmp/codex-lifecycle-fixture/mounted-run.log`。该测试使用独立数据目录、工作区和 `HOME`。未访问真实账号。
- 已对所属 Rust 文件执行 `rustfmt --check` 并通过。全仓早期 `cargo check` 曾遇到并行开发中的 Chat、项目 Git、浅版本和子智能体模块编译错误。之后实际进程验证使用主侧通知的重建产物。

## 订阅生命周期待验项

- 设备码成功落盘并完成模型刷新尚未单独验收。已验证设备码交换前的协议和交换中的取消、登出，以及浏览器流程复用的共同落盘与刷新路径。
- 实际 Web 二进制测试验证了路由装配和失败、取消路径，完整的成功换 token 与模型刷新证据来自回环模拟上游的隔离 Rust fixture。Windows 和 Linux 的双栈回环监听尚未验收。
- 凭证与索引分别原子写入，进程内错误可尝试恢复移开的凭证。进程在两文件提交之间被强制终止的恢复行为未验收。

## Responses 与 writer 前像

- 转入订阅任务前已在 `backend-rust/src/responses/` 核实请求转换、SSE 解析和计时实现在工作树中。已有 Responses、`read_file` 和 `write_file` 契约测试。首次定向 `cargo test` 退出码为 101，随后按任务切换继续订阅生命周期，本轮没有得到这三个套件的运行结果。
- 首次检查 `backend-rust/src/tools/write_file.rs` 与 `backend-rust/src/tools/write_file/transaction.rs` 时，writer 已做工作区范围检查、预读判断、原始字节读取与暂存原子提交。那次检查的 `WorkspaceFileWriter::execute` 仍只接收授权作用域、对话 ID 和消息列表，未接收前像回调。此结论仅对应任务切换前的快照。之后未修改 writer、Responses、runtime 审批或浅备份模块。
- 当时两位二轮交接方尚未出现在子智能体状态列表中，未完成交接确认。恢复后需重新查看当前 writer 接口与 runtime 调用，因为其他智能体的并发修改可能已经进入工作树。待验行为包括覆盖、追加、新建和多组编辑的前像值，以及拒绝、验证失败或提交失败时的备份记录行为。
