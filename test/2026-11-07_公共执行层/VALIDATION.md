# 公共执行层验证记录

日期：2026-11-07。宿主：macOS 15.6.1 / arm64；主侧解释器 `/opt/homebrew/bin/python3.11`。没有启动第二个 Astrion 后端，没有 commit/push，没有修改真实 `.env` 或运行中对话。

## 结论与边界

共享执行接口、真实 Local 适配器、独立资源装配与核心工具／上下文／媒体／后台／浅版本的接线已落地，并通过下述回归。独立执行进程可操作自己的工作区，不需要载入模型或 Flask 栈。这些是可复现的技术可行性证据。

当前不是已上线的 Astrion Remote。没有生产网络 Runner、RemoteExecutionBackend、Relay、配对、E2EE 或远程工作区 UI。外围文件端点、完整 Git、跨机技能／工作流归档、迁移别名和预览等剩余本机假设详见 `docs/execution_contract.md`，不能将核心工具通过等同于完整远程工作区已可用。

## 主智能体最终实跑

| 验证 | 结果 | 日志 |
| --- | --- | --- |
| 公共层 unittest 全目录 | 198 项，0 失败，9 skip | `cache/execution_plane_final.log` |
| 原生 macOS Local 后端 | 17 项全通过，0 skip | `cache/execution_local_native_final.log` |
| 原生后台进程／管道清理 | 5 项全通过，0 skip | `cache/execution_background_native_final.log` |
| 原生持久终端／管道清理 | 6 项全通过，0 skip | `cache/execution_terminal_native_final.log` |
| stdio 独立进程探针 | PASS 13 / SKIP 0 / FAIL 0 | `cache/execution_transport_final.log` |
| fresh interpreter 装配隔离 | 1 项通过（亦包含在全目录） | `cache/execution_import_final.log` |
| 目标环境与上下文 | 13 项通过（亦包含在全目录） | `cache/execution_environment_final.log` |
| 旧 standalone runtime 生命周期 | 4 项通过 | `cache/execution_runtime_final.log` |

最终还执行了 81 个改动／新增 Python 模块的 `py_compile`、内嵌 `CONTAINER_FILE_SCRIPT` 独立编译、新执行层／测试文件不超过 500 行检查，以及 `git diff --check`，全部通过。编译产物写入临时目录后清理。

普通工具沙箱下，`test_local.py` 的实际执行类用例因外层不允许嵌套 Seatbelt／进程信号而跳过。原生测试通过单独一次完全访问获准执行，对话的全局执行环境仍为 sandbox。28 项原生用例全部通过，原生三份最终日志没有 ResourceWarning／unclosed file 警告。

实际验证包含：

- 前台取消与后台取消后真实 PID 不再存活；不只断言 cancelled 状态。
- Seatbelt 只读拒绝写入、可写命令在工作区落盘；working_dir 的默认和相对目录正确，越界被拒。
- 真实持久终端打开／输入／关闭，以及自身 shell 被 wait 回收。
- 后台自然结束、超时、取消和孙进程继承输出管道；有期限收口，不在活 reader 持锁时强行 close。
- 持久终端保留大块输出尾部，外部共享描述符不被关闭；孙进程持写端时关闭调用有期限，reader 退出后再收口。
- 非本地后端失败不使用控制端资源；真实 handle_tool_call 测试将控制端资源设为访问即抛 BaseException，防止异常被吞掉后假通过。
- 真实 no-follow 二进制写和字节精确浅版本恢复，合法新建文件的撤销删除。
- Docker 环境快照不解析宿主镜像或借其大小写规则，挂载目录缺失保持未知，禁止报告容器支持单次宿主完全访问。

## 相邻回归

以下由同一主对话的 low 子智能体在临时数据和合成配置下实际执行；主侧核对了代码修改与日志／完成报告。它们不包含真实远程网络或模型调用。

| 套件 | 结果 |
| --- | --- |
| 子智能体统一 | 23 项通过 |
| MCP 浏览器对话隔离 | 9 项通过（fake stdio 服务） |
| 命令拦截 core / API / execution / routes | 18 / 7 / 11 / 4 项分别通过 |
| tool_loading | 23 项通过 |
| 旧浅版本 | 11 项通过 |
| 执行侧浅版本 | 15 项通过（亦包含公共层全目录） |
| server refactor smoke | 6 项通过 |
| 自动审核上下文 | 76 项通过 |
| 运行中压缩与状态边界 | 13 项通过 |
| Chat 模式整目录 | 51 项通过 |
| 对话迁移 | 31 项通过 |
| 对话迁移_02 上传／媒体 | 1 项通过 |
| 对话级压缩阈值 | 12 项通过 |

子实例所写的已核对日志：`cache/execution_subagent_unified_final.log`、`cache/execution_mcp_browser_final.log`、`cache/execution_command_blocking_final.log`、`cache/execution_tool_loading_final.log`、`cache/execution_shallow_versioning_final.log`。

先前主侧还执行过完全访问审批 41 项和子智能体权限／单次完全访问 35 项，均通过；后续 owner 绑定反例由本目录 scope 测试再次覆盖，不把先前测试数字当成最后一次全目录结果。

Chat 整目录有既有 `test_tasks.py` 的 pending task／跨 Context reset 清理告警；测试结果为通过，不能据此宣称该既有异步测试清理问题已解决。

## 测试修正说明

没有降低生产沙箱约束来凑通过。测试中处理了以下真实夹具问题：

1. macOS `/var` 本就属于系统可读范围，越界样本不能放默认 tempfile 根；改为独立未授权测试路径。
2. `/var` 与 `/private/var` 的同目录别名不能做字符串前缀比较；fresh interpreter 数据目录改为 resolve 后精确匹配，并清理临时目录。
3. shell stdout/stderr 混合含 `.profile` 读取拒绝噪声；cwd 测试用显式标记行且保留成功校验，不屏蔽生产 stderr。
4. Chat 对话创建时已快照压缩阈值；旧压缩夹具改为创建前通过真实配置 API 写 16000，并断言 snapshot，而非运行时 patch 失效后去改生产规则。
5. 命令路由 mock 旧签名不接收 workspace_id/update_session；只修替身签名，保留原断言。
6. standalone 生命周期测试改用当前 callback 和默认 Local 后端约定。

## 复现命令

```bash
ASTRION_IGNORE_DOTENV=1 PYTHONDONTWRITEBYTECODE=1 \
/opt/homebrew/bin/python3.11 -B -m unittest discover \
  -s test/2026-11-07_公共执行层 -p 'test_*.py'

ASTRION_IGNORE_DOTENV=1 PYTHONDONTWRITEBYTECODE=1 \
/opt/homebrew/bin/python3.11 -B test/2026-11-07_公共执行层/test_transport.py
```

原生环境按 README 分别运行 `test_local.py`、`test_background_cleanup.py`、`test_process_cleanup.py`。智能体从 OS 沙箱运行时须走单次完全访问申请；不得通过关闭沙箱或改白名单替代。

stdio 在普通工具沙箱下明确使用 direct 探针执行命令，仅验证传输与位置；不把它的取消响应当作 PID 消失证据，PID 与 Seatbelt 结论来自原生 Local 回归。

## 未验收内容

Linux 受限助手、Windows WSL2、真实 Docker 容器、真实跨机／公网协议、端到端加密、断线恢复、重连幂等、跨平台安装、前端视觉与产品整流程未在本轮验收。离线容器脚本和目标环境替身只证明对应接口／文本语义。

旧容器文本读写的先 resolve 后 open 竞态尚未重写；不能将新增二进制／元数据描述符保护外推到全部旧文本 IO。
