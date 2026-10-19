# MCP 浏览器对话隔离回归

## 离线回归

在仓库根目录执行（需要项目已安装的 httpx）：

```bash
/opt/homebrew/bin/python3.11 -B test/2026-10-19_MCP浏览器对话隔离/run_regressions.py
```

其它平台可换用已安装项目依赖的 Python 3.11+。

运行器禁用真实 `.env`，把配置和数据根指向测试目录下自动清理的临时目录。不会读取真实浏览器数据、启动浏览器、访问网络或重启服务。

- 覆盖对话、工作区、用户数据目录与服务 ID 的隔离和稳定性。
- 覆盖官方 npm 包、直接 node 包路径与 playwright-mcp 命令的识别。
- 覆盖原始配置和工作目录不变、Docker 使用现有挂载下的 profile 路径。
- 覆盖显式 profile／配置／临时模式／外接浏览器、其它 MCP、HTTP 服务不被改写。
- 使用两个真实 MCP stdio 子进程模拟浏览器排他目录，验证同工作区双对话并发、独立页面／存储状态以及同一对话重建连接后的存储恢复。
- 对照用例显式共享目录，必须出现 `Browser is already in use`，确保 fixture 确实能发现目录冲突。

模拟服务只验证 MCP 调用链、配置传播和隔离逻辑，不等同 Playwright/Chrome 实机验收。

## 真实运行验收

1. 重启加载修改后的后端。无需重新构建前端。
2. 使用默认本地 `@playwright/mcp` 配置（未指定 `--user-data-dir`、`--config`、外接浏览器等覆盖项）。
3. 同一工作区打开两个对话，分别让 Playwright 导航至不同页面，再交替读取页面 snapshot。
4. 确认无浏览器目录占用错误，且各自页面没有被另一个对话更改。
5. 各对话首次使用新 profile 时重新登录。关闭并重建该对话的 MCP 浏览器后，验证可持久化的登录存储仍属于本对话；页面不承诺自动恢复。

维护契约：`docs/agents_details/mcp_runtime_scope.md`。
