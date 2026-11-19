# 私有工作区记忆

常驻 `MEMORY.md` 与索引式专题记忆共用工作区的数据目录。`MEMORY.md` 全文注入，专题记忆通过 `search_project_memory`、`recall_project_memory` 和 `update_project_memory` 维护。主 Agent 的固定规则见 `prompts/main_agent/static.txt`，专题索引和常驻正文由各自的动态模板装配。

## 存储与身份

- Host：`<当前 DATA_DIR>/memory/<项目目录名>-<规范化绝对路径 SHA256 前16位>/MEMORY.md`。目录名保留可读部分、限制长度；摘要避免斜杠替换冲突。工作区路径由可信资源装配提供，不取子实例工作目录。
- Docker：`<当前用户当前工作区 data_dir>/memory/MEMORY.md`；独立 bind 到 `<配置的容器项目根>/.astrion/astrion-memory/`。提示词与文件工具使用 `.astrion/astrion-memory/MEMORY.md`。不能把整个 data_dir 挂进容器，也不把正文复制到代码树。
- Docker 记忆目录自动带 `.gitignore`（`*`），它随目录挂载进入容器，防止普通 Git 添加收录私有正文；不修改公开仓库的根 `.gitignore`。容器重建使用同一外置源目录。缺少或不匹配挂载的活动缓存容器明确报错，不为接入记忆强杀正在运行的容器。
- 源目录和挂载点拒绝符号链接；目录不会因单纯查询授权集合而创建。绑定来源必须为受信任的项目根和 data_dir，不能由模型工具参数提供。
- Chat 无工作区，继续使用其独立记忆，不注入 Agent 的项目记忆。

## 快照生命周期（方案 A）

- 新 Agent 对话带 `workspace_memory_enabled=true`，首个模型请求前读取文件，正文作为完整 `frozen_system_prompt` 的一部分，与 `workspace_memory_snapshot`（路径、版本、是否存在）及写入版本一次性原子保存；主 Agent 不再单独保存正文段。旧 `frozen_workspace_memory_prompt` 仅在恢复执行前原序合并迁移。见 `docs/system_prompt_snapshot.md`。
- 正常发送、恢复、重启后的继续均使用持久化快照，不检查正文变化、不追加实时更新通知。即使文件原先为空，提示词仍为非空冻结段，不会因空字符串而反复加载。
- 主对话深压缩／手动压缩成功提交时失效完整 system 及私有记忆快照；实际发生自动浅压缩时同样失效，下一次构建整体重建。不因失败或取消的深压缩提前清除已提交快照，没有发生浅压缩时保持冻结。
- 已有老对话不在恢复时补注入；下一次压缩后启用。
- 子实例新建时注入所属工作区的最新正文，复用、idle 唤醒和恢复沿用原 `system_prompt.txt`。子压缩仅替换私有记忆段并保存新系统提示词，不重建其他冻结内容。
- 主子实例、不同对话可在同一时刻持有不同版本，这是按各自新建／压缩边界快照的预期行为。

## 权限与写入

- `modules/workspace_memory.py` 只注册当前工作区的精确外置记忆目录。`host_sandbox_policy` 在用户配置与环境授权之外并入此内置资源能力，不回写全局或工作区手动授权设置，也不授权共享 memory/data 根。
- 三平台 Host 沙箱计划及原生 FileManager 共用现有授权入口；readonly／plan 的写入限制不变。子实例获得 Host 记忆读根，不追加此隐式写根。
- “主写子读”是共享记忆维护策略：子提示词要求向主汇报，不自行修改。**不将该策略宣称为比现有固定权限更强的安全隔离**：例如 full_access 或 Docker 内工作区写权限仍沿用其原权限边界。
- 主 `write_file`／`edit_file` 仅对已成功全文注入的那个 MEMORY.md 免预读；其它同名文件、同目录文件及未注入的老对话不豁免。auto_approval 下该文件按内置工作区资源免区外文件审批；人工审批仍按原规则。
- 维护仍用普通文件工具。写前在同进程按目标串行，比对模型已知版本；其它会话修改后返回 `workspace_memory_conflict` 与最新正文，模型合并后重试。此工具结果不替换冻结提示词。冲突和成功写入后的已知版本持久保存。
- 锁保护本后端进程内的协作工具写入，不宣称与外部编辑器、shell 直接写或多后端进程实现跨进程 CAS；原有单后端进程约束保持。底层普通文本文件写入仍使用既有执行后端，不宣称全链路新增原子落盘保证。

## 执行边界与代码入口

- 路径和 Host 绑定：`modules/workspace_memory.py`、`modules/file_manager/base.py`、`modules/host_sandbox_policy.py`。
- 快照与模板：`modules/workspace_memory_context.py`、`core/main_terminal_parts/context/messages.py`。
- 免预读和版本保护：`modules/workspace_memory_tools.py`、`core/main_terminal_parts/tools_execution.py`。
- Docker 挂载：`modules/user_container_manager.py`、`server/context/resources.py`；专属源目录由可信 workspace.data_dir 传入，旧启动入口按现有 Docker 布局取 project 同级 data。
- 主压缩：`server/deep_compression.py`、`utils/context_manager/compression_mixin.py`。
- 子创建与压缩：`modules/sub_agent/named.py`、`modules/sub_agent/history.py`。
- 内容与元数据通过已绑定后端文件接口读取，后端拒绝不得改读服务端源码镜像。当前外置位置解析仅支持受信任 Local 后端，绑定其它后端会明确报不支持，不能暗中回落本地。
- 冻结正文会像其它系统提示词一样进入对话记录；“私有”指不存入代码仓库，不意味着不向模型发送或自动从对话导出中剔除。

## 索引式专题记忆与存量迁移

- Host 的专题放在 `MEMORY.md` 同级的 `topics/` 子目录，复用该工作区精确授权。Docker 的后端目录是 `<当前用户当前工作区 data_dir>/memory/topics/`，迁移核验后绑定到容器项目根的 `.astrion/memory/`。两个环境均按工作区身份定位，Git 工作树及子智能体目录只影响执行位置。主子索引用相同的专题名称，Docker 从容器视图读取索引。
- Host 启动检查该用户的已登记工作区，每个后端进程至多主动提示一次。暂缓后可在「设置 → 上下文与记忆」继续。Docker 仅在「设置 → 管理员」提供「迁移所有用户的项目记忆」，由登录管理员确认，范围包含普通 Web 用户和 API 用户。只枚举现有路径，无需启动容器。
- Host 运行时直接使用新位置。Docker 待迁移工作区保留原容器视图，管理员执行迁移后才切换。工作区有活动任务、后台工作或持久终端时，保留旧容器并报告可重试项。
- 每个工作区先无覆盖复制并保留修改时间，再逐文件核验内容和源身份。目标同名同内容可复用，内容不同则按内容摘要另存。Host 在清理前写入 `00_READ_ME_项目记忆已迁移.md`，Docker 在清理前激活同路径挂载。只清理已核验的一级 Markdown 记忆；遇到变更、权限错误或中断，可对剩余文件重试。
- 迁移不更新已有对话的 `frozen_system_prompt` 或子智能体已保存的系统提示词。专题名称读取在运行时解析，本轮不会要求用户重建对话。
- 实现入口为 `modules/project_memory.py`、`modules/project_memory_migration.py`、`server/project_memory_migration.py` 和 `ProjectMemoryMigrationDialog.vue`。

## 验证

回归及有限原生验收脚本见 `test/2026-11-07_私有项目记忆/README.md`。macOS 实测不等于 Linux／Windows 原生验收，Docker 参数装配测试不等于真实 Docker 挂载验证。
