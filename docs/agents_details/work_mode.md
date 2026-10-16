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
- **Web 新对话初始化（2026-10-16）**：新建按钮（顶部、工作区分组、桌面新标签）统一进入 `/new`，不创建文件；旧 `new_chat_button_behavior` 已移除。`WebTerminal._ensure_conversation()` 仅恢复绑定对话，工作区级 terminal 不自动加载最近对话、不自动建文件。
  - **默认与当前选择分开**：`server/context/new_conversation.py::remember_workspace_defaults` 缓存工作区个人默认模型、run_mode、reasoning_effort、work_mode、permission_mode；首次装配及个人设置保存/重新加载时更新。`POST /api/new-conversation/settings` 每次进入空页按这份默认配置准备，不用上次临时选择覆盖默认。推理强度 `null` 明确表示 default，不得用 `or` 回退旧值。
  - **继承边界**：无个人默认的执行环境与网络，只能从导航刚离开的、同工作区正在查看的显式 `previous_conversation_id` 读取。切换到另一个工作区的 `/new`、直接打开、未找到来源均为 `sandbox + restricted`；不得读目标工作区最近历史。plan 强制 readonly+sandbox，受限权限强制 sandbox，并保留恢复记录。
  - **前端拥有本次选择**：`ui/newConversation.ts` 一次应用初始配置；空页上的模型/思考/强度/工作/权限/执行/网络选择仅更新前端；轮询与旧对话迟到响应不可覆盖。导航准备完成前不受理发送。
  - **首消息创建**：普通与多智能体 POST 同交完整 `settings`（model_key/run_mode/thinking_mode/reasoning_effort/work_mode/permission_mode/execution_mode/network_permission/restore_modes），`create_from_settings` 共同校验并落盘，不再读取个人默认覆盖快照。无效/缺失快照拒绝，不做旧请求格式兼容。模型能力、管理员模型禁用、direct 身份及模式联动均校验。
  - **范围**：本轮统一 Web 普通/多智能体首消息创建；CLI/Gateway 与工作流激活仍有各自入口。任何创建路径不得在 plan 锁之前调用非只读 set（曾因此 500）。回归见 `test/2026-10-16_新对话初始化/README.md`。
- **提示词**：`prompts/work_mode.txt` 模板 + `mode.py::_build_work_mode_rules`（三档规则唯一来源，冻结注入与切换通知共用）；冻结注入在 `messages.py`（执行环境之后）；切换走现有 drift 机制（`_RUNTIME_MODE_KINDS` 第四种），通知携带完整新规则文本
- **API**：`GET/POST /api/work-mode`（`server/chat/permission.py`）；**仅空闲可切换，运行中 409**（无 pending 队列）；`plan-approvals` pending/answer 端点在 `server/chat/approval.py`
- **submit_plan 链路**：工具全模式注入（沟通类工具不过滤，非 plan 调用由 handler 运行时兜延返回引导）；`PlanApprovalManager`（`modules/plan_approval_manager.py`）+ `chat_flow_tool_loop.py::_handle_submit_plan` 阻塞等待 → 前端 `PlanApprovalDialog.vue` 弹窗 → 批准则工具循环内切 execute 并静默更新 baseline（避免误发 drift 通知）
- **个性化**：`default_work_mode`（默认 plan），后端 `personalization_manager.py` 白名单 + 前端 `stores/personalization.ts` 三处（接口/默认值/sanitize）
- **前端**：输入栏 `work-mode-switcher`（InputComposer，streaming 时锁定）；plan 下权限菜单内「权限」「执行环境」两组选项禁用+锁定标记（**网络权限组保持可调**，不整体锁死菜单），slash 菜单同步禁用；`get_status` 带 `work_mode` 供多标签页同步
