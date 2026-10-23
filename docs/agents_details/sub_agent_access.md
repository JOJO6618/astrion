# 固定子权限与单次命令完全访问（完整）

> 源自 `AGENTS.md` §18；2026-10-22同步统一子智能体及四档固定权限。
> 用途：`AGENTS.md` 只保留硬约束与「改代码必须知道」，机制细节、历史沿革、逐条清单在此查阅。
> 若与代码冲突，一律以代码为准，并回来同步本文与 `AGENTS.md`。

---

## 18) 固定子权限与单次命令完全访问（2026-10-07）

- **统一恢复**：新格式注册表在 `sub_agent_runtime.json`，按工作区data_dir共享任务、实例、句柄和通信状态；构造不启动模型请求，执行资源入口按对话激活idle恢复。不读取旧传统子任务记录。查询不创建执行资源，不替换活动记录。新生命周期详见 `multi_agent.md`。
- create必填access_level：working_dir_write（仅子工作目录）、workspace_write（当前工作区）、sandbox_write（当前工作区＋沙箱授权可写路径）、full_access（宿主完全访问）。创建校验主权限上限；plan/readonly允许前两个窄档。恢复校验创建时规范化workspace_root、working_dir与固定档位，不重新resolve后扩大身份。
- 可信权限唯一入口为execution_scope.py的ContextVar；子内置工具通过execution.py使用独立FileManager/TerminalOperator/TerminalManager，不修改父对象再恢复，不继承父自定义分派，不开放后台命令或单次升级。父权限／执行环境变化不改变已有子档位。**MCP是外置插件，不进入子内置ExecutionScope，也不受本节目录写沙箱约束。**
- workspace_root始终是原项目；相对路径及默认命令cwd以working_dir解析，可读取原工作区及现行授权范围。working_dir_write的固定写根为子目录；workspace_write为原工作区。macOS临时目录放相应固定写根内，不附带宿主/tmp写授权。原生IO继续no-follow描述符／Windows句柄、链接替换／特殊文件／多硬链接检查；媒体复用同一安全读取。
- Docker不支持full_access。两个窄档必须Landlock ABI≥3＋libseccomp＋capability清理，失败即拒绝；working_dir_write只授父项目read、子目录write，HOME／TMP位于子写根。不授父full再试图收窄子目录，不回退DAC或宿主执行。Windows为父ro-bind＋子bind；Linux助手请求增加workspace_write_root，父只读＋子可写。**已有Linux助手需按安装器升级，旧助手拒绝新字段时不能静默扩大权限。** sandbox_write沿用平台授权路径。
- 主 `run_command` 的 `request_full_access=true` 必须提供非空原因；plan/readonly 拒绝，Docker/远端替身后端不伪装支持宿主机直接执行。已处于 direct 时无需额外申请。Windows direct 使用 cmd.exe，沙箱 bash 命令不得自动原样重试到宿主机。
- 单次申请复用 `ToolApprovalManager` 的 `approval_type=full_access`。开启自动审核时自动与人工独立裁决，任一拒绝即终结，两项通过才执行；人工先允许不能取消自动审核。可信记录保存执行者/根/对话/任务/工具调用/完整参数，`claim_execution` 原子领取一次；`FullAccessGrant.consume` 在原生命令入口再次核对并消费。授权失效返回正常工具失败结果，不中断历史消息配对。内置工具名禁止被自定义注册或分派覆盖。
- 前台、后台均绑定该次权限，不改变主对话全局状态。后台创建经 `asyncio.to_thread`；Popen 与取消在相同锁内握手，取消发生在 ID 分配前时通过 Event 与回调处理迟到创建。正常后台完成不等于取消全部后台任务。
- Web 工具审批挂在输入栏的 `ComposerApprovalDock.vue`：人工模式为上方滚动内容、左下固定拒绝/允许按钮；自动审核时才加右侧进度区，按钮仍在左下。整卡正常高度404px（原高度约130%），小窗口限制高度。单次完全访问允许按钮为“允许本次完全访问权限执行”。删除切无限制和执行者/cwd/执行环境/执行方式展示；同对话串行审批，不加翻页。收起用向下SVG，恢复用向上SVG，恢复入口在可见Git/状态条上方。面板使用 `/` 菜单300ms曲线向上生长/向下收回；离场保留快照，但形象、状态栏和待审批恢复按钮必须在开始收起时同时上升，不能被transitioning或after-leave锁住。完成后不自动展示记录卡片或恢复入口；`/`与`+`设置的手动入口始终能打开，有记录时只读显示最近记录，没有时显示“无记录”（空对话也可打开）。`/`、`@`、快捷菜单临时占位时保留请求。视觉由用户验收，当前迭代按用户要求只构建、不测试、不改demo。
- Web/CLI 的人工 POST 成功不提前清除请求；已结束 ID 和快照版本阻止迟到 pending 响应复活审批。内部审核进度缓冲最多30条，每条进度最多100条；已完成快照留作手动查看，与待审批列表隔离，重复终态事件不能收起用户手动打开的历史。后端任务事件流不因界面清理而改变。普通人工接管的结束事件须区分裁决来源，不能显示为自动审核通过。
- 针对性回归目录：`test/2026-10-07_子智能体权限与单次完全访问/`，Python unittest 与 Node 状态测试均不启动服务或真实 TUI。审批退场与完成清理补充回归见 `test/2026-10-07_审批面板交互/`。测试与实际平台验证范围详见相应目录 README。
