# 统一子智能体回归

最后验证：2026-10-23。测试使用临时目录、合成模型响应和MCP客户端，不发送真实模型请求，不重启后端，不修改真实对话目录。

## 当前结果

| 检查 | 结果 | 执行环境或范围 |
| --- | --- | --- |
| 本目录 `test_*.py` | 23项通过 | 项目 `.venv/bin/python`，Python 3.9.6 |
| 本目录 `test_*.py` | 23项通过 | `/opt/homebrew/bin/python3.11`，Python 3.11.13 |
| 固定权限／单次完全访问 | 35项通过 | Python 3.11 |
| 工具动态加载 | 23项通过 | Python 3.11 |
| 运行对话快照 | 整套通过 | 既有 `run_regressions.py` |
| 工作区切换重构 | 整套通过 | Python 3.11、Node，`PYTHONPATH=.` |
| MCP浏览器对话作用域 | 9项通过 | 既有作用域回归，非真实子模型端到端 |
| Linux host适配 | 21项，3项平台跳过，其余通过 | macOS开发机，不等同Linux实机 |
| 改动TS/Vue文件定向ESLint | 28个文件通过，0 warning | 包括新建的子工具渲染器 |
| 正式前端构建 | 通过 | 最后一次7.36秒；有现有大chunk提示 |
| Python语法与注解审计 | 82个改动／新增Python文件通过 | Python 3.9内存编译，不写系统缓存；子模块无立即求值的联合类型注解 |
| Git差异空白检查 | 通过 | `git diff --check` |
| 全量 `npm run lint` | 未通过 | 0 errors、171 warnings；项目设零warning，未做无关全仓格式化 |

最新Python 3.9回归日志在本地 `.astrion/unification_python39_checks.log`；Python 3.11三组回归在 `.astrion/unification_final_checks.log`。日志属于本地验证材料，不作为仓库依赖。

## 覆盖内容

- `test_rounds.py`：名称与预算校验、工具签名、自然结束、完整三工具批次到期后暂停、原上下文续接；最终回复通知不夹中间输出。
- `test_manager.py`：共享实时记录、进程重启后新格式恢复、模型与目录保持、终结吸收态、不读取旧 `sub_agents.json`。
- `test_notifications.py`：部分持久化失败只回队未写尾段、通知顺序、待处理工作恢复后只消费一次。
- `test_communication.py`：主子问答、子子名称寻址与早到答案、向主汇报。
- `test_dispatch.py`：任务受理前不写历史、不乐观回显，受理失败不丢消息。
- `test_scope_mcp.py`：真实原生文件的窄目录写限制、父项目读取、Linux请求／挂载结构、独立MCP管理器和可见映射、默认浏览器profile区分、显式配置优先。
- `test_migration.py`：预览只读、主对话迁移与原始备份、冲突全批拒绝、异常回滚、链接拒绝。
- `test_python39_creation.py`：真实公开 `handle_named_tool → create_named → resolve_intelligence_model → manager` 调用链；模型解析、后台创建、自然idle、名称续接、第一轮上下文保留。仅模型响应和外部配置使用mock。

Python 3.9实测曾在加载 `intelligence.py` 的 `str | None` 注解时报 `unsupported operand type(s) for |: 'type' and 'NoneType'`，现改用 `Optional[str]`。新增回归同时验证导入和 `get_type_hints`，避免只做语法编译遗漏运行时注解错误。

## 复现命令

项目根目录执行：

```bash
ASTRION_IGNORE_DOTENV=1 .venv/bin/python -B -m unittest discover \
  -s 'test/2026-10-22_子智能体统一' -p 'test_*.py' -v

/opt/homebrew/bin/python3.11 -B -m unittest discover \
  -s 'test/2026-10-22_子智能体统一' -p 'test_*.py' -v

/opt/homebrew/bin/python3.11 -B -m unittest discover \
  -s 'test/2026-10-07_子智能体权限与单次完全访问' -p 'test_*.py' -v

/opt/homebrew/bin/python3.11 -B test/历史测试_日期不明/test_tool_loading.py

/opt/homebrew/bin/python3.11 -B test/2026-10-07_运行对话快照/run_regressions.py

PYTHONPATH=. /opt/homebrew/bin/python3.11 -B \
  test/2026-10-12_工作区切换重构/run_regressions.py

/opt/homebrew/bin/python3.11 -B test/2026-10-19_MCP浏览器对话隔离/run_regressions.py

/opt/homebrew/bin/python3.11 -B -m unittest discover \
  -s 'test/2026-10-08_Linux_host沙箱适配' -p 'test_*.py' -v

set -o pipefail
npm run build --silent 2>&1 | tail -n 5
```

## 本机数据副本验证

`check_local_data.py` 和 `check_migrated_views.py` 是按用户要求进行本机真实数据验证的独立辅助脚本，不在unittest收集内。

已对本机 `~/.astrion/astrion/host/` 数据只读预览，并在 `.astrion/local_data_subagent_merge/` 隔离副本迁入217条旧多智能体主对话、7工作区。校验原文备份bytes、消息／待办／用量、已有主文件，以及真实源／目标树前后hash；生产普通 `ConversationManager` 能列出并读取全部217条。

真实原目录没有执行 `--apply`。现有隔离目录包含私有对话，不提交或发布。辅助脚本要求该目录尚不存在，重复执行会拒绝；不要为了重跑删除已有材料。

正式迁移步骤、停服务前提及失败回滚见 `docs/sub_agent_migration.md`。旧传统子任务不迁移恢复。

## 验证边界

上述测试不等同真实模型／子MCP调用、UI视觉、实际Chrome或Windows／Docker／Linux原生权限验收。没有启动第二后端、重启正在运行的服务、迁移真实原目录或执行Git提交／推送。分发技能修改的是仓库 `agentskills/` 源文件，非运行态 `.astrion/skills/` 副本。
