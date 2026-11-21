# 私有工作区记忆

常驻 `MEMORY.md` 与索引式专题记忆共用工作区的数据目录。`MEMORY.md` 全文注入，专题记忆通过 `search_project_memory`、`recall_project_memory` 和 `update_project_memory` 维护。主 Agent 的固定规则见 `prompts/main_agent/static.txt`，专题索引和常驻正文由各自的动态模板装配。

## 存储与身份

- `MEMORY.md` 位于 `<当前 DATA_DIR>/memory/<项目目录名>-<规范化绝对路径 SHA256 前16位>/`。目录名保留限长的可读部分，摘要区分名称相同的不同工作区。路径由可信资源装配提供，子智能体使用所属工作区的记忆。
- 外置目标拒绝符号链接。查询授权集合不会创建目录，路径来源限定为已登记的项目根与可信 `data_dir`。
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
- 子智能体按提示词向主智能体汇报记忆更新。工具操作仍按实例的固定权限执行。
- 主 `write_file`／`edit_file` 仅对已成功全文注入的那个 MEMORY.md 免预读；其它同名文件、同目录文件及未注入的老对话不豁免。auto_approval 下该文件按内置工作区资源免区外文件审批；人工审批仍按原规则。
- 维护仍用普通文件工具。写前在同进程按目标串行，比对模型已知版本；其它会话修改后返回 `workspace_memory_conflict` 与最新正文，模型合并后重试。此工具结果不替换冻结提示词。冲突和成功写入后的已知版本持久保存。
- 锁保护本后端进程内的协作工具写入，不宣称与外部编辑器、shell 直接写或多后端进程实现跨进程 CAS；原有单后端进程约束保持。底层普通文本文件写入仍使用既有执行后端，不宣称全链路新增原子落盘保证。

## 执行边界与代码入口

- 路径和 Host 绑定：`modules/workspace_memory.py`、`modules/file_manager/base.py`、`modules/host_sandbox_policy.py`。
- 快照与模板：`modules/workspace_memory_context.py`、`core/main_terminal_parts/context/messages.py`。
- 免预读和版本保护：`modules/workspace_memory_tools.py`、`core/main_terminal_parts/tools_execution.py`。
- 主压缩：`server/deep_compression.py`、`utils/context_manager/compression_mixin.py`。
- 子创建与压缩：`modules/sub_agent/named.py`、`modules/sub_agent/history.py`。
- 内容与元数据通过已绑定后端文件接口读取，后端拒绝不得改读服务端源码镜像。当前外置位置解析仅支持受信任 Local 后端，绑定其它后端会明确报不支持，不能暗中回落本地。
- 冻结正文会像其它系统提示词一样进入对话记录；“私有”指不存入代码仓库，不意味着不向模型发送或自动从对话导出中剔除。

## 索引式专题记忆与存量迁移

- 专题位于 `MEMORY.md` 同级的 `topics/`，复用该工作区的精确路径授权。主子索引按同一工作区身份解析专题名称。Git 工作树和子智能体目录决定执行位置。
- 启动检查已登记的工作区，每个后端进程至多主动提示一次。暂缓后可在「设置 → 上下文与记忆」继续。运行时直接使用外置位置。
- 每个工作区先无覆盖复制并保留修改时间，再逐文件核验内容和源身份。目标同名同内容可复用，内容不同则按内容摘要另存。清理前写入 `00_READ_ME_项目记忆已迁移.md`。只清理已核验的一级 Markdown 记忆。遇到变更、权限错误或中断，可对剩余文件重试。
- 迁移不更新已有对话的 `frozen_system_prompt` 或子智能体已保存的系统提示词。专题名称读取在运行时解析，本轮不会要求用户重建对话。
- 实现入口为 `modules/project_memory.py`、`modules/project_memory_migration.py`、`server/project_memory_migration.py` 和 `ProjectMemoryMigrationDialog.vue`。

## 验证

回归和各平台的原生验收范围见 `test/2026-11-07_私有项目记忆/README.md` 与 `test/2026-11-19_项目记忆迁移/README.md`。
