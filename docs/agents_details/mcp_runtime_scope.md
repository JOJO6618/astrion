# MCP 对话与子实例运行作用域

更新：2026-10-22。

## 归属与能力边界

`server/context/resources.py` 按用户／工作区／对话创建WebTerminal，每个terminal有独立MCPClientManager，连接池为实例属性。每个命名子实例也独立manager和客户端，复用父MCPServerRegistry、协议、别名及可见工具映射；不复制注册表、不复用父或兄弟的活动客户端。

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

## 可见性和结果

子工具目录只保留父可见且未禁用的alias，调用时重新检查MCP及工具禁用状态。list_mcp_servers只列子可用映射，不包含凭据。调用参数去掉intent后交外置manager；结构化结果和模型支持的图像回填子上下文。

## 回归和实机边界

```bash
/opt/homebrew/bin/python3.11 -B test/2026-10-19_MCP浏览器对话隔离/run_regressions.py
/opt/homebrew/bin/python3.11 -B -m unittest discover -s test/2026-10-22_子智能体统一 -p 'test_scope_mcp.py' -v
```

既有回归用真实stdio子进程和模拟持久目录锁，覆盖双主对话、重建、签名、cwd、Docker路径及显式覆盖；新增用例覆盖父／两子profile、独立manager、alias筛选、动态禁用、无内置scope调用和旧client关闭。不读取真实凭据／profile，不启动Chrome。

真实模型通过子MCP操作、Chrome页面与登录状态、Windows／Linux原生运行仍需加载新后端后验收。离线测试不替代这些验收。
