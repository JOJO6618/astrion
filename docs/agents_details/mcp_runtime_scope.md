# MCP 对话运行作用域与 Playwright 浏览器隔离

更新：2026-10-19。

## 归属与边界

- `server/context/resources.py` 按 `用户::工作区::对话ID` 创建 WebTerminal，每个 terminal 在 `core/main_terminal.py` 创建独立 MCPClientManager；连接池是实例属性。本地 stdio 服务的进程已经按对话分开。
- MCP 配置仍由 MCPServerRegistry 统一管理，工具别名与定义缓存保持原有语义。不能为了隔离浏览器而复制注册表或改 MCP 工作目录。
- MCP 连接隔离不等于外部资源隔离。Playwright 默认持久 profile 按工作区区分，同工作区两个进程仍会争用目录。远端 HTTP 服务是否隔离状态由服务端决定。

## 启动适配

唯一适配入口：`modules/mcp_client_manager/runtime_scope.py::prepare_stdio_server`。

- `MCPRuntimeScope` 携带可信工作区路径、工作区 data_dir、对话 ID；由 terminal 的惰性 resolver 提供，Web 使用绑定 ID，CLI 使用当前对话 ID。不能从模型工具参数读取身份。
- 每次取客户端前解析作用域，再生成有效启动配置、计算连接签名。这样构造时尚无对话、后续切换对话、重建客户端都能使用正确目录。作用域改变后有效签名变化，旧客户端先关闭再启动新客户端。
- 只识别本地 stdio 的官方 `@playwright/mcp` 包、该包内的脚本路径和 `playwright-mcp` 命令；任意 shell 包装器不会靠服务显示名猜测类型。
- 默认本地持久浏览器追加 `--user-data-dir <作用域目录>`。只修改内存中的配置副本，不写回 MCP 配置，不修改 cwd，不把对话身份作为项目路径传给 Playwright。
- 目录键为 SHA-256，输入包括规范化工作区路径、data_dir、完整对话 ID 和 MCP server ID。不同用户数据目录、工作区、对话及服务实例分开；同一身份重建连接复用目录。

## 数据位置与生命周期

- Host：`<工作区 data_dir>/mcp_profiles/<作用域哈希>/`，默认不落源码树。
- Docker：`<容器 mount_path>/.astrion/mcp_profiles/<作用域哈希>/`，使用现有工作区 bind 挂载，支持容器重建后继续读取，不增加宿主挂载。Docker 无法使用未挂载的 Host data_dir。
- Playwright 自己在实际启动浏览器时创建目录。terminal 回收／关闭客户端只关闭进程，不删除 profile，支持同一对话恢复自己的登录数据。
- 不迁移或复制原来的工作区共享 profile，因此首次使用新目录时需要按各对话重新登录。浏览器页面本身不会因重建进程自动恢复。
- Profile 属于运行态私有数据，不能提交到 Git；本仓库已忽略 `.astrion/`。

## 显式配置优先

以下配置保持管理员指定的语义，不自动注入 profile 目录：

- CLI 参数 `--isolated`、`--user-data-dir`、`--config`、`--extension`、`--cdp-endpoint`、`--endpoint`（支持 `--key=value`）。
- 对应 `PLAYWRIGHT_MCP_*` 环境变量；Host 同时检查继承的环境，Docker 只检查显式传入 docker exec 的环境。
- 没有对话 ID 的管理／工具发现实例，以及 HTTP 服务。

显式指定同一个持久目录仍可能冲突，外接同一个浏览器仍可能互相操作；本适配不声称改变这些显式共享行为，也不声称所有有状态 MCP 均已隔离。其它服务未来可以复用 MCPRuntimeScope，但应为其真实资源单独适配。

## 验证

回归目录：`test/2026-10-19_MCP浏览器对话隔离/`。

```bash
/opt/homebrew/bin/python3.11 -B test/2026-10-19_MCP浏览器对话隔离/run_regressions.py
```

用真实 stdio 子进程和模拟的持久目录排他锁，验证同工作区双对话并发、页面／存储状态分离、连接重建、作用域切换、cwd、配置不变性、Docker 路径与显式配置边界。不加载真实 `.env`、不读真实 profile、不启动 Chrome。

真实 Playwright 需重启加载修改后的后端，再在同工作区两个对话分别导航不同页面，交替读取 snapshot，确认没有占用错误和串页。离线回归不等同真实浏览器及各平台实机验收。
