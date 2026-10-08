# 运行模式（work_mode）系统（完整）

> 本文由 `AGENTS.md` §10.6 小节**原样下沉**而来（2026-10-08 压缩 AGENTS.md 时导出，内容未改写）。
> 用途：`AGENTS.md` 只保留硬约束与「改代码必须知道」，机制细节、历史沿革、逐条清单在此查阅。
> 若与代码冲突，一律以代码为准，并回来同步本文与 `AGENTS.md`。

---

## 10.6) 运行模式（work_mode）系统（2026-08-13 新增）

运行模式（**work_mode**：`plan` / `ask` / `execute`）控制**与用户的交互节奏**，与权限模式（限制模型能力）正交。命名注意：`run_mode` 已被 fast/thinking 思考模式占用，严禁混用。

### 三档语义

- **plan（计划）**：只制定计划并讨论；权限**锁定为只读**且执行环境**锁定为沙箱**（UI 禁用 + 后端 `set_permission_mode`/`set_execution_mode` 双重强制——只读在宿主机依赖 OS 沙箱硬限制，direct 下无沙箱形同虚设）；唯一写例外是 `.astrion/plan/*.md` 计划文档（权限层判定，提示词要求用 write_file/edit_file 直写，不走沙箱）；计划完成后调 `submit_plan` 工具提请批准，**批准后自动切换到 execute**（恢复进入 plan 前的权限与执行环境，无记录时权限回落个性化 `default_permission_mode`、执行环境保持沙箱安全默认）
- **ask（询问）**：先讨论后开工；**禁用 ask_user 工具**（开放式讨论必须直接写在回复里）；与 execute 无执行层差异，仅提示词不同
- **execute（执行）**：自行梳理计划、脑补细节直接开工；仅硬阻塞才提问

### 实现要点（改代码必须知道）

- **后端核心**：`core/main_terminal_parts/tools_policy.py`（WORK_MODES、`get/set/switch_work_mode`、plan 锁；`RESTRICTED_PERMISSION_MODES` 受限档集合 + `set_permission_mode` 内 `_apply_restricted_execution_mode_link` 处理受限档⇄沙箱联动，`pre_readonly_execution_mode` 存对话 metadata——键名保留，语义已泛化为受限档共用）；`switch_work_mode` 处理 plan⇄只读+沙箱联动，`pre_plan_permission_mode`/`pre_plan_execution_mode` 存对话 metadata；执行环境 plan 锁与受限档锁同在 `core/main_terminal.py::set_execution_mode`（只拦 direct）
- **创建对话的模式继承（work_mode + permission_mode 同一原则）**：三条创建路径（prefer_defaults / 显式模式 / safe_navigation）一律沿用 terminal 当前值，**不用个性化默认值覆盖**——/new 页面切换器显示什么新对话就是什么（切换经 `_sync_workspace_terminal_mode` 同步到工作区级 terminal）；个性化 `default_work_mode` / `default_permission_mode` 仅在 terminal 首次构造时生效（tools_policy 加载）。多智能体创建路径（`server/multi_agent.py`）与工作流创建路径（`workflow_runtime_api.py`）同样遵守。plan 锁存在于 `set_permission_mode`，任何创建路径不得在其之前调用非只读 set（曾因此 500）
  - 历史教训：permission_mode 曾长期例外（`a2a04b95` 引入「个性化默认优先」，彼时还没有 /new 切换器同步机制），导致 /new 切只读后新建对话回落无限制（2026-08-30 修复，与 work_mode 对齐）
- **提示词**：`prompts/work_mode.txt` 模板 + `mode.py::_build_work_mode_rules`（三档规则唯一来源，冻结注入与切换通知共用）；冻结注入在 `messages.py`（执行环境之后）；切换走现有 drift 机制（`_RUNTIME_MODE_KINDS` 第四种），通知携带完整新规则文本
- **API**：`GET/POST /api/work-mode`（`server/chat/permission.py`）；**仅空闲可切换，运行中 409**（无 pending 队列）；`plan-approvals` pending/answer 端点在 `server/chat/approval.py`
- **submit_plan 链路**：工具全模式注入（沟通类工具不过滤，非 plan 调用由 handler 运行时兜延返回引导）；`PlanApprovalManager`（`modules/plan_approval_manager.py`）+ `chat_flow_tool_loop.py::_handle_submit_plan` 阻塞等待 → 前端 `PlanApprovalDialog.vue` 弹窗 → 批准则工具循环内切 execute 并静默更新 baseline（避免误发 drift 通知）
- **个性化**：`default_work_mode`（默认 plan），后端 `personalization_manager.py` 白名单 + 前端 `stores/personalization.ts` 三处（接口/默认值/sanitize）
- **前端**：输入栏 `work-mode-switcher`（InputComposer，streaming 时锁定）；plan 下权限菜单内「权限」「执行环境」两组选项禁用+锁定标记（**网络权限组保持可调**，不整体锁死菜单），slash 菜单同步禁用；`get_status` 带 `work_mode` 供多标签页同步
