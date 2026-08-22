# 本项目新增 MCP 工具配置指南

> 最后更新：2026-08-22

本文面向后续维护者，说明如何在当前项目里新增并接入一个 MCP 工具（MCP Server）。

---

## 1. 先了解本项目里的 MCP 架构

当前实现是「**本项目作为 MCP 客户端桥接层**」：

1. 在后台配置一个或多个 MCP Server（stdio / streamable_http）
2. 系统通过 `tools/list` 拉取远端工具
3. 本地自动生成工具别名：`mcp__<server_id>__<tool_name>`
4. 模型调用该别名时，桥接层转发到对应服务的 `tools/call`
5. 对于同一服务，桥接层会复用长连接会话（避免每次调用都重启 MCP 进程），并处理常见 Server→Client 请求（如 `ping`、`roots/list`）

内置了一个原生工具：

- `list_mcp_servers`：查看服务、缓存工具和别名映射（可选刷新）

---

## 2. 配置入口（如果你是个人类）

### 2.1 管理后台入口（推荐）

- 打开：`/admin/policy`
- 页面中的「**MCP 服务配置（统一工具扩展）**」区域可做增删改查、同步工具

### 2.2 个人空间入口（宿主机管理员）

- 个人空间里有单独的「**MCP 配置**」入口
- 仅在 **宿主机模式 + 管理员** 时显示
- 点击后会跳转到 `/admin/policy`

> 管理 API 受管理员权限和二级密码校验保护，建议优先使用页面配置，不建议直接手改请求。

---

## 3. 第一步：先写一个可运行的 MCP Server

仓库已有可直接参考的测试服务：

- `scripts/mcp_calculator_server.py`

这个示例使用 stdio，支持：

- `initialize`
- `tools/list`
- `tools/call`

如果你写新服务，至少要保证上述 3 个方法可用。

---

## 4. 第二步：在后台新增服务配置

在 `/admin/policy` 的 MCP 区域点击“新增 MCP 服务”，填写核心字段：

- `id`：必须以字母开头，仅允许字母/数字/`_`/`-`
- `transport`：`stdio` 或 `streamable_http`
- `enabled`：是否启用
- `timeout(s)`：调用超时

### 4.1 stdio 示例

- `transport`: `stdio`
- `command`: `python3`（或绝对路径）
- `args`: 每行一个参数（常见是服务脚本绝对路径）
- `cwd`: 可选
- `env`: 可选 JSON

示例：

```json
{
  "id": "calc_stdio",
  "name": "Calculator MCP",
  "enabled": true,
  "transport": "stdio",
  "command": "python3",
  "args": ["/abs/path/scripts/mcp_calculator_server.py"],
  "timeout_seconds": 12
}
```

### 4.2 streamable_http 示例

```json
{
  "id": "my_http_mcp",
  "name": "My HTTP MCP",
  "enabled": true,
  "transport": "streamable_http",
  "url": "http://127.0.0.1:8000/mcp",
  "headers": {
    "Authorization": "Bearer <token>"
  },
  "timeout_seconds": 25
}
```

### 4.3 可选过滤

- `include_tools`：仅放行这些工具名
- `exclude_tools`：屏蔽这些工具名

---

## 5. 第三步：同步工具缓存

保存后点击：

- “同步工具”（单服务）
- 或“同步全部工具”

同步成功后会写入缓存字段（页面可见）：

- `tools_cache_count`
- `tools_cache_updated_at`
- `last_error`

---

## 6. 第四步：在对话里验证是否接入成功

让模型先调用 `list_mcp_servers`（可 `refresh=true`），确认：

1. 目标服务在列表中
2. `tools_cache_names` 有你新工具
3. `tool_aliases` 已生成（通常是 `mcp__...`）

再让模型调用对应别名工具即可。

---

## 7. 环境变量（可选）

见 `.env.example`：

- `MCP_TOOLS_ENABLED=1`
- `MCP_SERVERS_FILE=./data/mcp_servers.json`
- `MCP_PROTOCOL_VERSION=2025-06-18`
- `MCP_DEFAULT_TIMEOUT_SECONDS=25`

（AI 维护者请直接改这个文件）

默认配置文件是运行态文件（`DATA_DIR` 解析见 `config/paths.py`）：

- `~/.astrion/astrion/<mode>/data/mcp_servers.json`

---

## 8. Docker 模式下的 stdio MCP（重要）

当前策略已收敛为：**MCP 仅在宿主机模式支持**。

当会话运行在 docker 模式时：

1. 调用 `list_mcp_servers` 会直接返回：`当前为docker模式，MCP仅支持宿主机模式`。
2. 调用任意 `mcp__...` 工具别名也会返回同样提示。
3. 不再在 docker 模式执行 MCP 服务同步与调用。

如果需要使用 MCP，请先切换到宿主机模式后再进行配置/调用。

---

## 9. 常见问题排查

1. **服务保存失败**
   - 检查 `id` 格式、transport 必填项（stdio 要 `command`，HTTP 要 `url`）

2. **同步失败 / 无工具**
   - 看 `last_error`
   - 检查服务是否能独立启动
   - 检查 `include_tools/exclude_tools`

3. **工具在页面有，但模型看不到**
   - 确认服务 `enabled=true`
   - 确认管理策略没禁用 MCP 分类
   - 重新“同步工具”后再试

4. **调用时报超时**
   - 提高 `timeout_seconds`
   - 优化 MCP Server 响应速度

---

## 10. 关键代码位置（便于二次开发）

- MCP 配置：`config/mcp.py`
- 服务注册表：`modules/mcp_server_registry.py`
- MCP 客户端桥接：`modules/mcp_client_manager/`（子包）
- 管理 API：`server/admin.py`
- 工具定义/执行：
  - `core/main_terminal_parts/tools_definition/`（子包，`tools_definition.py` 为兼容入口）
  - `core/main_terminal_parts/tools_execution.py`
- 管理端页面：`static/src/admin/PolicyApp.vue`
- 测试示例服务：`scripts/mcp_calculator_server.py`
