# MCP 对话与子实例运行作用域

更新：2026-11-21。

## 归属与能力边界

`server/context/resources.py` 按用户、工作区、对话装配 WebTerminal，每个 terminal 和每个命名子实例都有自己的 MCPClientManager 与连接池。子实例复用服务注册表和协议，按子智能体的名单生成工具定义和别名映射。

MCP为外置插件，不进入子内置ExecutionScope，不套子工作目录写权限沙箱。连接分开不等于外部资源分开，远端HTTP服务及管理员显式共享的浏览器仍依照服务配置。

## 唯一启动适配入口

`modules/mcp_client_manager/runtime_scope.py::prepare_stdio_server`。

- MCPRuntimeScope含可信workspace_path、data_dir、conversation_id，可选actor_id、working_dir。父用terminal惰性resolver；子用稳定task_id和已验证目录。身份不得来自模型工具参数。
- 每次取客户端先解析scope，产生内存有效配置并计算连接签名；签名改变先关闭旧连接再建新连接。不写回MCP服务配置。
- 子stdio服务没有显式cwd时，以子working_dir为cwd；显式cwd保持。父服务cwd不变，不能把对话ID当工作目录。
- 只识别本地stdio官方@playwright/mcp、包内脚本及playwright-mcp命令；不凭任意shell包装器的显示名猜测类型。
- 默认本地持久浏览器追加 --user-data-dir。父哈希输入仍为规范化workspace_path、data_dir、对话ID及server ID；仅子追加actor_id，因此既有父profile保持原键，父／各子互异，同一子恢复复用目录。

## 数据位置与生命周期

Host：`<工作区 data_dir>/mcp_profiles/<作用域哈希>/`。

Docker：`<容器 mount_path>/.astrion/mcp_profiles/<作用域哈希>/`，沿用工作区bind，不增加宿主挂载。主对话的Docker MCP禁用策略不因子实例接入而放开。

Playwright实际启动时创建目录。关闭客户端只关闭进程，不删除profile；不会自动恢复浏览器页面。旧工作区共享profile不复制／迁移，因此首次使用新scope可能需要重新登录。目录含私有运行数据，不得提交。

子idle保留所属执行实例和连接；实例退出时关闭自己的manager；重建工具目录先关闭旧manager，防止连接泄漏。

## 显式配置优先

以下配置保持管理员语义，不自动注入profile：

- --isolated、--user-data-dir、--config、--extension、--cdp-endpoint、--endpoint，含 --key=value。
- 对应PLAYWRIGHT_MCP_*变量；Host检查继承环境，Docker只检查显式传入容器的环境。
- 没有对话ID的管理／发现实例及HTTP服务。

显式指定同一目录仍可能占用冲突，显式外接同一浏览器仍可能互相操作。不能宣称所有有状态MCP都隔离了；其它服务需要为真实资源分别适配。

## 设置与可见性

- 入口为「设置 → 系统 → MCP」。`server/mcp_settings.py` 使用已认证的 Host 会话和常规 CSRF 校验。全局服务的增删改、同步、名单读取和写入无需管理员二级密码，也不装配对话执行实例。
- `mcp_servers.json` 保留 `servers`，新增 `access.defaults` 与 `access.workspaces`，两个范围各有独立的 `main` 和 `sub`。工作区按注册 ID 绑定。工作区 `inherit=true` 使用全局名单并保留自定义草稿，`inherit=false` 使用 `server_ids`，显式空名单表示停用这一角色的所有 MCP。全局停用的服务对两个角色都不可用。
- 老配置缺少对应全局名单时，使用旧的全局 MCP 类别禁用规则作为默认。主对话的临时开关只能收紧当前工作区主智能体的可用范围。子智能体独立使用自己的设置。设置页的名单规则与子智能体原生工具权限档位正交。
- `modules/mcp_access.py` 解析当前角色的有效服务。发现工具、注入、`list_mcp_servers` 以及调用共同过滤。调用按模型可用别名分派并重新检查当下的服务名单，已送出的 MCP 请求按原连接完成。主、子智能体为后续模型请求更新工具定义。
- 注册表的服务、名单和发现缓存共用文件锁与原子写。发现期间服务连接配置变化时丢弃旧结果。调用参数去掉 `intent` 后交外置 manager，结构化结果和模型支持的图像回填子上下文。

## 回归和实机边界

```bash
/opt/homebrew/bin/python3.11 -B test/2026-10-19_MCP浏览器对话隔离/run_regressions.py
/opt/homebrew/bin/python3.11 -B -m unittest discover -s test/2026-10-22_子智能体统一 -p 'test_scope_mcp.py' -v
```

既有回归用真实stdio子进程和模拟持久目录锁，覆盖双主对话、重建、签名、cwd、Docker路径及显式覆盖；新增用例覆盖父／两子profile、独立manager、alias筛选、动态禁用、无内置scope调用和旧client关闭。不读取真实凭据／profile，不启动Chrome。

真实模型通过子MCP操作、Chrome页面与登录状态、Windows／Linux原生运行仍需加载新后端后验收。离线测试不替代这些验收。
