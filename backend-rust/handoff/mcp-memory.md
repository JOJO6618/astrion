# 项目记忆迁移与 MCP 交接

## 2026-11-27 续接验证

主对话和具名子实例已各自使用独立 MCP 管理器，模型每轮只获得当轮授权的工具别名，调用再次核对授权。隔离生产入口测试已通过主、子两个真实 loopback MCP 调用和结构化结果回填。主会话删除、工作区解绑、子终结和后端退出接入连接关闭。MCP 图片保留在持久结果中，视觉模型经后续请求的图片内容接收，纯文本模型的投影滤除图片字节。

## 项目记忆迁移

[迁移模块](../src/knowledge/migration/mod.rs)保留三个 Host 接口。

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | `/api/project-memory-migration` | 返回待迁移概览或活动任务。`?job=<id>` 查询指定任务。 |
| POST | `/api/project-memory-migration/startup` | 每个后端进程至多提供一次启动提示。 |
| POST | `/api/project-memory-migration` | 启动后台任务或返回正在执行的任务。 |

[文件迁移](../src/knowledge/migration/files.rs)从已登记工作区的 `.astrion/memory` 枚举一级 Markdown，使用现有 Rust 专题命名空间定位目标。复制保留修改时间和已有同名文件，冲突按内容摘要另存。清理前复核文件清单、来源身份和内容摘要，并写入 `00_READ_ME_项目记忆已迁移.md`。文件操作使用已打开的目录，拒绝符号链接和有硬链接别名的旧文件。

已核实 `src/knowledge/migration/files.rs` 中 `#[path = "../../../tests/fixtures/migration_files.rs"]` 实际解析到 [migration_files.rs](../tests/fixtures/migration_files.rs)。该 fixture 已落盘，覆盖冲突与 mtime、源变更时保留旧文件和拒绝硬链接。发生 trait 歧义的位置已使用从 `cap_fs_ext` 导入的 `MetadataExt::nlink(meta)`。这两项已检查源文件和路径，最新源码的编译与测试结果待主侧统一验证。

## MCP 管理与运行模块

[管理路由](../src/mcp/routes.rs)提供 `/api/mcp/settings`、`/api/mcp/access/{actor}[/{workspace_id}]`、`/api/mcp/servers[/{server_id}]` 和 `/api/mcp/sync`。注册表沿用 `mcp_servers.json`，用进程内锁与原子写维护服务、角色名单和发现缓存。旧配置回退到 MCP 类别，工作区继承允许保留自定义草稿，显式空名单停用该角色的全部服务。同步使用管理连接，无对话作用域。配置更新时丢弃过期发现结果。

[运行模块](../src/mcp/runtime.rs)包含 stdio 与 Streamable HTTP 握手、分页发现、模型工具别名、当前名单复核和真实调用。HTTP 支持协商协议、会话头、SSE 响应和会话过期重建。工具调用移除 `intent`，保留结构化输出与图像。默认本地 Playwright profile 使用可信工作区、数据根、对话和服务身份计算，子实例额外包含稳定任务 ID。子实例可采用已验证 cwd，显式 cwd 和显式浏览器参数优先。

已在 [host_web.rs](../src/host_web.rs)确认两个管理路由均已由主侧合并进经过 Host Web 认证与 CSRF 校验的 API。主侧曾报告 MCP 管理路由通过离线二进制构建。该记录早于最后一轮模块调整和独立测试落盘，当前没有这些改动的编译或运行结果。共享依赖已包含 Tokio 的 `process` 和 `io-util`。

### 生产运行时接线

主侧已要求接入主、子运行入口，收到暂停指令时共享运行时改动尚未开始。按下面 API 接线，并与 `runtime/agent.rs` 上的代理运行生命周期、提问计划改动协调。

- `mcp::Registry::for_data(data: &Path)` 加载部署配置。`mcp::Runtime::new(registry, scope)` 创建连接管理器。
- `mcp::RuntimeScope::main(&workspace, data, conversation_id)` 和 `mcp::RuntimeScope::child(&workspace, data, parent_conversation_id, task_id, &validated_child_cwd)` 返回 `io::Result<RuntimeScope>`。`workspace` 为 `RegisteredWorkspace`。主对话与各命名子实例各保留自己的管理器。
- 每轮模型请求前调用 `manager.prepare_tools().await`，将 `snapshot.schemas()` 合入模型工具定义。该轮用 `snapshot.allows(name)` 校验，再调用 `manager.execute(&snapshot, name, arguments).await`。`mcp::is_tool(name)` 可识别 `list_mcp_servers` 与 MCP 别名。
- 主对话使用 `manager.set_category_overrides(BTreeMap<String, bool>).await` 应用临时开关。子实例使用独立 `sub` 名单。子上下文结果可用 `mcp::model_content(&result, supports_images)` 生成文本或带图片的内容块。
- 对话资源释放、子退出或重建工具目录时调用 `manager.close().await`。现有 Rust 子调用路径中的结果回填和模型图像能力需随正常入口一起验收。

## 待验收

主侧统一使用 `backend-rust/target-master` 构建。以下两条命令已交主侧，暂停时未收到结果。

```sh
cargo test --offline --locked --manifest-path backend-rust/Cargo.toml --target-dir backend-rust/target-master --lib knowledge::migration
cargo test --offline --locked --manifest-path backend-rust/Cargo.toml --target-dir backend-rust/target-master --test mcp_contract
```

[MCP 独立测试](../tests/mcp_contract.rs)已落盘。它使用工作树下的临时数据根、[合成 stdio 服务](../tests/fixtures/mcp_mock.py)和绑定 IPv4 loopback 的 HTTP mock。用例计划验证管理 CRUD、显式与继承名单、动态撤销、stdio 握手与分页、真实工具调用、主与两个子 profile、cwd、连接复用、HTTP 会话协商、SSE 调用以及会话过期恢复和关闭。Python 解释器可用 `ASTRION_MCP_TEST_PYTHON` 指定。验证范围限合成数据和本地服务，未调用任何用户外置 MCP、真实凭据或真实浏览器。

迁移接口的完整 HTTP 冒烟，以及 MCP 经生产主对话、子实例送入模型 schema、派发、状态恢复和关闭的合成端到端用例尚待补齐。迁移文件原生 Windows 运行仍待验收。

契约来源见 [项目记忆](../../docs/workspace_memory.md)和 [MCP 运行作用域](../../docs/agents_details/mcp_runtime_scope.md)。
