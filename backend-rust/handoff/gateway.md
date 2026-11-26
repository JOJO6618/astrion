# Gateway、Git 与预览验收交接

本轮按要求暂停开发与测试。恢复后运行了 Git 和 Node 真实监听器验收，均显式使用工作树内 `backend-rust/target-master/debug/astrion-headless`。本轮仅写入本交接文件。下列全量数字来自已有 TSV 的头部，监听器结果来自本轮进程输出与已执行的断言。

## 已验证

- Git 工作区概览验收通过。Web 登录后两条生产入口 `GET /api/project/git-summary` 和 `GET /api/project/git-diff` 返回预期结果。夹具覆盖暂存与未暂存差异、未跟踪文件不计入差异、按文件展开的 `context_map`、按折叠位置展开的 `fold_context_map`、工作区选择优先级、授权的外部 Git 工作树、失效绑定、无工作区和读取不改动会话文档及 Git index。两条入口在 Web 和 headless 分别校验无 Cookie 与有效 Gateway Bearer 均返回 `401`。有效 Bearer 同时能访问 `/api/host/workspaces`，验证了凭据可用。Git 结果以基于 Python 契约编写的夹具断言为准，本轮未同步启动 Python Git 监听器。[Git 回环夹具](../../test/2026-11-26_RustGit工作区概览/workspace_git_roundtrip.py)
- 清单 Node 套件的 headless 用例通过。`GET /api/status`、`GET /api/skills` 和 `GET /api/git-worktree` 在无会话和有效 Bearer 下均返回 `401`。`POST /api/git-worktree` 与 `POST /api/git-worktree/inspect` 在这两种凭据条件下均返回 `403`。用例也验证了共用入口的 Bearer 行为、计划接口的 `HEAD` 与 `OPTIONS`，以及无效计划负载的 `400`。之前五项工作区路由的 Web 与 headless 验收曾取得 2/2 通过，本轮 Node 复跑为三用例中一项通过。[清单监听器用例](../../test/2026-11-24_Rust后端/route_inventory_http.test.mjs#L101)
- 预览 Web 用例执行至第 156 行。此前断言已验证未登录拒绝、targets/bootstrap/messages 共享独立预览源及旧 Python token、独立源的 HTML 基准路径和资源字节，以及错误 token 返回 `404`。[预览监听器用例](../../test/2026-11-26_Rust预览/preview_web.test.mjs#L125)

## 分母与清单状态

| Python 生产入口 | Flask 规则 | 全路径有效方法与路径 | 全路径主要方法 | `/api` 有效方法与路径 | `/api` 主要方法 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `server.app:app`（Web 对照） | 268 | 693 | 295 | 624 | 271 |
| `server.headless_app:create_headless_app()`（headless 对照） | 90 | 221 | 93 | 215 | 91 |

主要方法分母不含 `HEAD` 和 `OPTIONS`。正式 TSV 的旧快照显示，Web `/api` 主要方法匹配 165/271、缺 106，headless 匹配 69/91、缺 22。这些是旧快照数字，当前 Rust 装配的覆盖数和逐项缺口尚待重新生成。Rust 独立预览源启动于另一个监听器，有三条路径形态，分别是 `/{token}/file/{*path}`、`/{token}/proxy/{port}/` 和 `/{token}/proxy/{port}/{*path}`。这三条不计入上述两个 Python 入口分母。[正式旧快照](../../test/2026-11-24_Rust后端/route_inventory_baseline.tsv#L2) [独立监听器装配](../src/preview/mod.rs#L124) [独立预览 Router](../src/preview/routes.rs#L52)

## 待验与恢复线索

- **清单解析。** 暂停前，静态扫描首先卡在 `preview/routes.rs` 中的 `on(PROXY_METHODS, handler).options(...)`。需要按 `MethodFilter` 声明展开方法并保留显式 `HEAD`，根据预览 handler 的端口校验对齐 Flask 整数路径，再为 Git `workspace_router` 确认 Web 与 headless 双挂载，为预览 Web Router 与独立监听器分别确认归属。继续核对近期开通的 Admin、MCP、项目记忆迁移、会话及导航装配锚点。恢复时先用 `route_inventory.py --print` 做只读重算，将注册缺口、装配未确认、辅助方法与不同监听器单列；正式 TSV 由主侧更新。[Rust 清单解析器](../../test/2026-11-24_Rust后端/route_inventory_rust.py) [Web 与 headless 装配](../src/host_web.rs#L46)
- **预览真实监听器。** 测试尝试通过 Fetch 设置 `Host: forged.invalid`，预期 `403`、实际 `200`。目前只确认测试断言失败，服务器收到的 Host 未抓取。Fetch 可能改写请求头，这一解释待证实。恢复后先用可明确发送 Host 的 Node `http.request` 核对同一回环请求。由于用例提前退出，后续独立源路由隔离、PATCH 转发和凭据剥离、会话 token 隔离、Web 文件回退，以及删除目标的 CSRF 与即时生效均未在本轮跑到。[失败断言与后续检查](../../test/2026-11-26_Rust预览/preview_web.test.mjs#L156)
- **清单 Web 监听器。** 本轮 Web 用例在启动时退出，程序返回 `Host home directory is unavailable`。因此该套件的 Web 登录、五项工作区入口与真实切换本轮未重验。Git Web 用例使用项目存储夹具的隔离 home 且成功启动，可对照两种夹具的启动环境。现有清单 Node 用例仍显式传入固定模型，本轮没有覆盖未固定模型的目录解析。[清单 Web 入口](../../test/2026-11-24_Rust后端/route_inventory_http.test.mjs#L116) [Git 复用的启动夹具](../../test/2026-11-25_Rust项目存储/project_storage_roundtrip.py#L42)

本轮监听器命令在工作树根执行，使用同一份二进制。

```sh
ASTRION_HEADLESS_BIN="$PWD/backend-rust/target-master/debug/astrion-headless" python3 -B 'test/2026-11-26_RustGit工作区概览/workspace_git_roundtrip.py'
ASTRION_HEADLESS_BIN="$PWD/backend-rust/target-master/debug/astrion-headless" node --test 'test/2026-11-26_Rust预览/preview_web.test.mjs' 'test/2026-11-24_Rust后端/route_inventory_http.test.mjs'
```
