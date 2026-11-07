# 公共执行层契约

2026-11-07。本契约描述当前代码中的执行边界；实验与命令见 `test/2026-11-07_公共执行层/README.md`，验收证据见该目录 `VALIDATION.md`。

## 状态与产品方向

本轮交付的是共享执行资源、默认本地后端及核心调用接线，并验证独立进程驱动的可行性。尚无生产 RemoteExecutionBackend、Relay 或网络 Runner，不能据此启用完整远程工作区。

后续 Remote 的已确认语义：用户人工创建“远程工作区”，连接设备后填写该设备上的路径；执行目标由工作区绑定，该工作区全部对话都在该设备执行。控制端保留模型循环、审批、对话存储和上下文装配，执行节点提供文件、环境、进程、终端与原生沙箱。远端项目不必在控制端存在，也不要求双向项目同步。

网页接入使用用户自行部署、受信任的 TLS 中转；中转可见其处理的网页内容。Runner 通道后续采用端到端加密。macOS、Linux、Windows 按现有平台隔离整体设计，不把某一平台的测试当成三平台验收。

## 资源与所有权

- `modules/execution_plane/base.py` 定义 `ExecutionBackend`；`files.py`、`protocols.py` 定义显式子协议。禁止万能 `__getattr__` 代理隐藏协议缺项。
- `get_execution_backend(owner)` 返回已绑定后端；仅旧 owner 缺失后端时惰性创建 `LocalExecutionBackend(owner)` 并缓存。后端报错、能力缺失或返回拒绝时不得改用控制端资源。
- `MainTerminal` 默认绑定真实 `LocalExecutionBackend(self)`，本地 Host/Docker 同样使用公共边界。
- Local 适配器只保存 owner，按需获取资源，构造时不创建终端、进程或容器。owner 的资源可以在生命周期中更新。
- `create_local_resources(project_path=..., data_dir=...)` 可独立构造 FileManager、TerminalOperator、TerminalManager、BackgroundCommandManager，再交给 Local 适配器；无需 MainTerminal、Flask、APIClient 或模型循环。
- 此工厂复用既有执行实现，不是已经打包好的安装器。节点部署仍需提供 Python 依赖、配置启动隔离及平台沙箱助手；测试显式使用 `ASTRION_IGNORE_DOTENV=1`。

## 协议面

| 资源 | 入口与责任 |
| --- | --- |
| 命令 | `run_command` / `run_command_background`，显式 timeout、working_dir、sandbox_write_access、network_permission；后台同时绑定 conversation_id |
| 文件 | `files`：文本／二进制读写、分段／搜索／抽取、目录与文件增删改、inspect_path、resolve_path |
| 持久终端 | `terminals`：打开、关闭、重置、列举、输入、快照及全部关闭 |
| 后台 | `background`：查询、等待、取消、状态协调、通知、认领与回滚通知；精简 owner 未提供时允许为 None |
| 环境 | `get_environment()`：JSON 可序列化的执行侧 workspace、OS、架构、模式、网络、能力与容器信息 |
| 编辑备份 | `track_file_edit(...)`：从执行侧获取原内容，关联当前对话与工作段 |
| 插件 | `execute_mcp_tool` / `execute_custom_tool` / `list_mcp_servers`；能力不存在应明确报错或返回空发现结果 |

这些是 Python 内部接口，不是网络 RPC 公共协议。未来传输必须显式编码 bytes、校验请求身份、执行设备与路径归属，处理取消、超时、乱序和幂等；不得直接暴露任意 Python 属性／方法。

## 路径、元数据与内容

1. 目标路径属于执行端。工作区归属及路径授权通过执行侧 `resolve_path`／`inspect_path` 判断，控制端不得以本机 `Path.resolve/exists/stat` 代替。
2. `inspect_path` 成功且 `exists=False` 才表示合法的新路径；权限拒绝、不可达和后端错误不能当作文件不存在，尤其不能绕过覆盖前必须先读的守门。
3. 元数据返回 `success/path/relative_path/within_workspace/exists/is_file/is_dir/is_symlink/suffix/size/mtime`；拒绝时返回失败。元数据不代替实际内容访问时的安全检查。
4. Host 适配器复用 FileManager 的路径、授权和 scoped_io。Docker 元数据走容器脚本，容器不可用不回读宿主镜像。
5. `read_binary` 返回 bytes；`write_binary` 精确写原始字节，不能隐式转 UTF-8。Host 与新增容器二进制写均在截断前检查 no-follow、普通文件和硬链接限制。
6. **现有容器文本 IO 的剩余限制**：旧 read/write 路径仍有先解析再按路径打开的 TOCTOU 风险；新增 metadata/binary 的描述符实现不能被表述为所有容器文本 IO 已无竞态。

## 核心调用接线

- 工具执行：前台／后台命令、文件／目录、终端、后台 sleep、媒体检查、网页保存、项目记忆产物及对话回顾产物走所选后端。
- 读取：read/search/extract、技能文本、项目记忆与已读内容缓存走文件资源。已经读到全文时将 `content` 交给 ContextManager，避免隐式再从本机打开。
- 上下文：工作区 AGENTS/CLAUDE、mtime、目录结构与深树走执行端；仍按既有机制冻结历史。树读取失败不冻结为正常空树。
- 环境：执行侧未报告的平台、路径和配额明确未知；容器 OS 未探测不拿 Host OS 冒充。远端失败不回退控制端平台探测。
- 媒体：原始路径字节、图片转换、OCR 与上下文媒体读取使用后端文件；控制端已认证 MediaStore 内容寻址缓存仍属于控制端可信数据。
- 服务端：后台 REST、停止、活动判定与通知经 `backend.background`；审批预览和计划读取经 `backend.files`。

## 权限与取消

公共层不替代原有权限裁决。编排端传递已确定的只读、网络及执行模式；执行端仍须用实际 OS 沙箱强制约束，不能只相信调用方传入字符串。

单次完全访问只认可**精确 LocalExecutionBackend 类型且 backend.owner 就是当前 terminal**；Fake、远端声明 `is_local=True`、Local 子类或其他 owner 的适配器均不得获得当前终端的 grant。原有任务／工具指纹／单次领取校验保持。Docker 不支持此升级。

前台取消必须回收所拥有的真实进程，不能仅取消等待；后台创建、登记与取消保持原锁握手。成功取消既要验证状态，也要验证 PID 消失。持久终端只关闭本实例持有的进程与管道，不停止其附着的用户容器。

本地子实例独立装配文件／进程／终端／后台资源，并使用 `LocalExecutionBackend(child)`；不能复用父的 owner 资源或清空后端。当前非本地父实例明确拒绝创建子执行上下文，未来需受限 `fork_child(scope)` 及持久化／恢复身份契约，不能悄悄在控制端启动子实例。

## 后台通知与版本内容

- 等待、列表、取消、认领和通知都属于同一个执行后端。
- 通知派发失败调用 `unmark_notified`，恢复后仍能重新获取待通知记录。
- 停止清理通过 `mark_all_notified(conversation_id)` 在 manager 锁下全量标记终态，不得因列表上限留下第 1001 条之后的幽灵通知；运行态记录不能提前标已通知。
- 浅版本通过注入 `filesystem` 获取执行侧当前字节、写回或删除；合法新文件记录 None，撤销创建时删除。备份本体、对话与检查点记录仍存控制端 data_dir，不要求把对话存储搬到节点。
- 完整 Git 备份仍未接入执行后端，见后续边界，不能宣称 full 备份已远程化。

## MCP 与自定义工具

插件调用位置通过后端入口收敛，启用、管理员和目录规则仍由原有编排判定。MCP 是外置服务，继续使用自己的 cwd、连接与权限语义；接入公共执行层不等于 MCP 获得了原生目录沙箱保护。独立资源工厂不自动创建 MCP 服务。

## 正式 Remote 上线前的剩余边界

以下是审计确认的剩余工作，不是已经支持的能力。尚无生产远端后端，因此不得只注入一个远端对象就宣称完整工作区可用：

- 设备配对、Relay、TLS／端到端加密、设备身份、重连、租约、幂等及失联取消；正式 RemoteExecutionBackend 与 Runner 安装／升级。
- 远程工作区的创建、持久化身份和路径验证；该工作区所有查询／运行入口按同一目标解析。
- 非本地子实例的受限派生、恢复和独立 MCP 归属。
- create_skill/save_workflow 的源目录跨机归档：当前实现仍按控制端 Path 操作源，需安全传输归档内容后才可用于远端。
- 对话迁移的工具路径别名仍使用 `conversation_transfer.assets.tool_path` 的控制端 safe_file；媒体正文读取已有字符串映射，但工具别名路径需保留无链接约束地改为执行侧验证。
- 完整 Git baseline/checkpoint/restore，以及服务端文件浏览、上传／下载与预览等外围工作区访问仍含控制端本地假设，需逐入口接线或明确拒绝，不能回落宿主镜像。
- 远程预览端口转发、跨设备媒体导入／导出与资源复制。
- Linux 受限助手、Windows WSL2 与真实 Docker 的实机回归，三平台安装和生命周期验收。

当前 stdio 实验仅证明这套资源可以在独立进程驱动，且文件／命令产物落在执行侧；不验证生产公网协议、网络安全、跨机延迟或断线恢复。

## 私有工作区记忆（2026-11-07）

MEMORY.md 全文读取、目标元数据与普通写工具经已绑定后端文件接口，不回读服务端源码镜像。当前外置位置解析仅信任 Local 适配器；非本地执行器明确拒绝此能力，后续 Remote 必须另行定义其私有记忆存储与授权协议。Host 的数据目录绑定属于可信资源装配，Docker 仅挂载当前工作区专属记忆目录。不能因增加记忆功能而扩大子实例固定写根或 readonly/plan 能力。详见 `docs/workspace_memory.md`。
