# 固定子权限与单次命令完全访问（完整）

> 本文由 `AGENTS.md` §18 小节**原样下沉**而来（2026-10-08 压缩 AGENTS.md 时导出，内容未改写）。
> 用途：`AGENTS.md` 只保留硬约束与「改代码必须知道」，机制细节、历史沿革、逐条清单在此查阅。
> 若与代码冲突，一律以代码为准，并回来同步本文与 `AGENTS.md`。

---

## 18) 固定子权限与单次命令完全访问（2026-10-07）

- `create_sub_agent`（普通/多智能体）必填 `access_level`：`workspace_write`、`sandbox_write`、`full_access`。创建时校验主权限上限；plan/readonly 仍可创建最低档并交付工作区文件。每个新实例保存创建时规范化的 `workspace_root` 与固定档位，恢复时核对原路径身份，不重新 resolve 后扩大授权。旧记录缺少档位不迁移、不恢复。
- 可信权限唯一入口为 `modules/execution_scope.py` 的 ContextVar；子工具通过 `modules/sub_agent/execution.py` 使用独立 FileManager/TerminalOperator/TerminalManager，不修改父对象再恢复。路径授权每次读取现行配置；父权限和执行环境切换不改变已有子档位，也不发送误导环境通知。子工具只开放自身白名单，不继承父自定义分派，不开放后台命令或单次升级参数。
- `workspace_write` 的写根始终为创建时工作区，命令 cwd 只能在其内。macOS 临时文件放 `.astrion/sub_agent_runtime/<actor哈希>/tmp`，不附带宿主 `/tmp` 或额外路径写授权。原生 IO 使用 POSIX no-follow 描述符或 Windows 句柄，拒绝替换链接、特殊文件和多硬链接写入；媒体读取复用安全二进制接口。交付目录按路径组件检查并安全新建，不使用字符串 startswith。
- Docker 不支持 `full_access`。最低档通过 `modules/docker_scoped_launcher.py` 强制 Landlock ABI≥3 + libseccomp + capability 清理；无法安装隔离则命令失败，不使用 DAC 降级或宿主机回退。系统工具目录可读、持久写根仅容器工作区（另允许 `/dev/null`），临时文件也在工作区。Landlock 不覆盖的权限/归属/扩展属性等元数据系统调用由 seccomp 拒绝。`sandbox_write` 沿用现有容器边界。Docker/Windows 的真实运行行为仍需对应平台验收；Linux 首批适配与验证范围见 §20。
- 主 `run_command` 的 `request_full_access=true` 必须提供非空原因；plan/readonly 拒绝，Docker/远端替身后端不伪装支持宿主机直接执行。已处于 direct 时无需额外申请。Windows direct 使用 cmd.exe，沙箱 bash 命令不得自动原样重试到宿主机。
- 单次申请复用 `ToolApprovalManager` 的 `approval_type=full_access`。开启自动审核时自动与人工独立裁决，任一拒绝即终结，两项通过才执行；人工先允许不能取消自动审核。可信记录保存执行者/根/对话/任务/工具调用/完整参数，`claim_execution` 原子领取一次；`FullAccessGrant.consume` 在原生命令入口再次核对并消费。授权失效返回正常工具失败结果，不中断历史消息配对。内置工具名禁止被自定义注册或分派覆盖。
- 前台、后台均绑定该次权限，不改变主对话全局状态。后台创建经 `asyncio.to_thread`；Popen 与取消在相同锁内握手，取消发生在 ID 分配前时通过 Event 与回调处理迟到创建。正常后台完成不等于取消全部后台任务。
- Web 工具审批挂在输入栏的 `ComposerApprovalDock.vue`：人工模式为上方滚动内容、左下固定拒绝/允许按钮；自动审核时才加右侧进度区，按钮仍在左下。整卡正常高度404px（原高度约130%），小窗口限制高度。单次完全访问允许按钮为“允许本次完全访问权限执行”。删除切无限制和执行者/cwd/执行环境/执行方式展示；同对话串行审批，不加翻页。收起用向下SVG，恢复用向上SVG，恢复入口在可见Git/状态条上方。面板使用 `/` 菜单300ms曲线向上生长/向下收回；离场保留快照，但形象、状态栏和待审批恢复按钮必须在开始收起时同时上升，不能被transitioning或after-leave锁住。完成后不自动展示记录卡片或恢复入口；`/`与`+`设置的手动入口始终能打开，有记录时只读显示最近记录，没有时显示“无记录”（空对话也可打开）。`/`、`@`、快捷菜单临时占位时保留请求。视觉由用户验收，当前迭代按用户要求只构建、不测试、不改demo。
- Web/CLI 的人工 POST 成功不提前清除请求；已结束 ID 和快照版本阻止迟到 pending 响应复活审批。内部审核进度缓冲最多30条，每条进度最多100条；已完成快照留作手动查看，与待审批列表隔离，重复终态事件不能收起用户手动打开的历史。后端任务事件流不因界面清理而改变。普通人工接管的结束事件须区分裁决来源，不能显示为自动审核通过。
- 针对性回归目录：`test/2026-10-07_子智能体权限与单次完全访问/`，Python unittest 与 Node 状态测试均不启动服务或真实 TUI。审批退场与完成清理补充回归见 `test/2026-10-07_审批面板交互/`。测试与实际平台验证范围详见相应目录 README。
