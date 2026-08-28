"""Backend i18n message pack: workflow 族（server/workflow_flow.py、modules/workflow_manager.py、
server/workflow_runtime_api.py、server/workflow_page.py）用户可见消息。

Pure data module — do not import anything here. Auto-discovered and merged
by modules/i18n.py at import time. zh-CN copy is verbatim from source;
en-US is concise product-level English (sentence case).
插值用 str.format 命名参数：tr("workflow_flow.<key>", name=value)。
"""

MESSAGES = {
    # ── server/workflow_flow.py（key 前缀 workflow_flow.） ──

    "workflow_flow.missing_workflow_name": {
        "zh-CN": "缺少工作流名称。",
        "en-US": "Missing workflow name.",
    },
    "workflow_flow.already_active_other": {
        "zh-CN": "当前对话已激活工作流「{name}」，同一时间只能激活一个工作流。请先调用 deactivate_workflow 退出，再激活新的。",
        "en-US": "The current conversation already has workflow \"{name}\" active; only one workflow can be active at a time. Call deactivate_workflow to exit before activating a new one.",
    },
    "workflow_flow.workflow_not_found": {
        "zh-CN": "工作流不存在：{name}",
        "en-US": "Workflow not found: {name}",
    },
    "workflow_flow.structure_invalid": {
        "zh-CN": "工作流结构校验未通过：{errors}",
        "en-US": "Workflow validation failed: {errors}",
    },
    "workflow_flow.missing_entry_node": {
        "zh-CN": "工作流缺少有效的入口节点（开始节点未连接）。",
        "en-US": "The workflow has no valid entry node (the start node is not connected).",
    },
    "workflow_flow.no_active_workflow_hint": {
        "zh-CN": "当前对话没有激活的工作流（可能已被退出）。如需重新开始，请调用 activate_workflow。",
        "en-US": "No workflow is active in this conversation (it may have been deactivated). Call activate_workflow to start a new one.",
    },
    "workflow_flow.state_node_missing": {
        "zh-CN": "工作流状态异常：当前节点不存在于定义快照中。可调用 get_workflow_status 自查。",
        "en-US": "Invalid workflow state: the current node does not exist in the definition snapshot. Call get_workflow_status to check.",
    },
    "workflow_flow.at_branch_need_choose": {
        "zh-CN": "当前停在分支点「{name}」，请先调用 choose_workflow_branch(target_node_id) 选择路径。",
        "en-US": "Currently at branch node \"{name}\"; call choose_workflow_branch(target_node_id) to pick a path first.",
    },
    "workflow_flow.not_in_stage": {
        "zh-CN": "当前不在执行阶段（位于「{name}」），无法汇报阶段完成。",
        "en-US": "Not in an execution stage (currently at \"{name}\"); cannot report stage completion.",
    },
    "workflow_flow.stage_no_next": {
        "zh-CN": "流程定义异常：当前阶段没有有效的后续节点。",
        "en-US": "Invalid workflow definition: the current stage has no valid next node.",
    },
    "workflow_flow.no_active_workflow": {
        "zh-CN": "当前对话没有激活的工作流。",
        "en-US": "No workflow is active in this conversation.",
    },
    "workflow_flow.not_at_branch": {
        "zh-CN": "当前不在分支点，无需选择路径。",
        "en-US": "Not at a branch node; no path to choose.",
    },
    "workflow_flow.target_not_in_routes": {
        "zh-CN": "「{target}」不在候选路径中。可选：{menu}",
        "en-US": "\"{target}\" is not among the candidate paths. Available: {menu}",
    },
    "workflow_flow.target_node_not_found": {
        "zh-CN": "目标节点不存在：{target}",
        "en-US": "Target node not found: {target}",
    },
    "workflow_flow.branch_selected": {
        "zh-CN": "已选择路径：{route}\n\n{text}",
        "en-US": "Path selected: {route}\n\n{text}",
    },
    "workflow_flow.stage_recorded": {
        "zh-CN": "阶段「{name}」已记录完成。",
        "en-US": "Stage \"{name}\" recorded as complete.",
    },
    "workflow_flow.completed_footnote": {
        "zh-CN": "工作流已完成",
        "en-US": "Workflow completed",
    },
    "workflow_flow.reached_end": {
        "zh-CN": "工作流「{name}」已到达终点「{end}」，全部完成。请向用户输出总结后结束。",
        "en-US": "Workflow \"{name}\" has reached its end \"{end}\" — everything is complete. Output a summary to the user and finish.",
    },
    "workflow_flow.branch_no_route": {
        "zh-CN": "流程定义异常：分支节点没有有效的出线。",
        "en-US": "Invalid workflow definition: the branch node has no valid outgoing edge.",
    },
    "workflow_flow.review_pass_no_next": {
        "zh-CN": "审核「{name}」通过，但通过路由指向不存在的节点。流程定义异常，工作流无法继续。",
        "en-US": "Review \"{name}\" passed, but the pass route points to a nonexistent node. Invalid workflow definition; the workflow cannot continue.",
    },
    "workflow_flow.review_pass": {
        "zh-CN": "审核「{name}」通过：{message}\n\n{inner}",
        "en-US": "Review \"{name}\" passed: {message}\n\n{inner}",
    },
    "workflow_flow.review_max_rejects": {
        "zh-CN": "审核「{name}」未通过（第 {count} 次，已达连续驳回上限 {max}）：{message}\n\n工作流已连续驳回超限而终止（failed）。请告知用户审核意见与终止原因；如需重新开始，可在调整流程或准备充分后重新激活。",
        "en-US": "Review \"{name}\" failed ({count} rejections, reached the consecutive rejection limit of {max}): {message}\n\nThe workflow has been terminated (failed) due to consecutive rejections. Inform the user of the review feedback and the termination reason; it can be reactivated after adjusting the workflow or when fully prepared.",
    },
    "workflow_flow.review_reject_to_missing": {
        "zh-CN": "审核「{name}」未通过：{message}\n\n但驳回路由指向不存在的节点，流程定义异常，工作流无法继续。",
        "en-US": "Review \"{name}\" failed: {message}\n\nHowever, the reject route points to a nonexistent node. Invalid workflow definition; the workflow cannot continue.",
    },
    "workflow_flow.review_rejected": {
        "zh-CN": "审核「{name}」未通过（第 {count}/{max} 次）：{message}\n\n你已回到「{target}」。请按整改意见修改后，重新调用 report_workflow_stage 汇报。",
        "en-US": "Review \"{name}\" failed ({count}/{max}): {message}\n\nYou are back at \"{target}\". Apply the corrective feedback, then call report_workflow_stage to report again.",
    },
    "workflow_flow.unknown_node_kind": {
        "zh-CN": "流程定义异常：未知节点类型 {kind}（节点「{name}」）。",
        "en-US": "Invalid workflow definition: unknown node type {kind} (node \"{name}\").",
    },
    "workflow_flow.review_start_event": {
        "zh-CN": "审核「{name}」开始",
        "en-US": "Review \"{name}\" started",
    },
    "workflow_flow.review_exec_exception": {
        "zh-CN": "审核智能体执行异常（{exc}）。本次按驳回处理：请告知用户审核服务可能异常；若属偶发，可稍后重新汇报本阶段。",
        "en-US": "The review agent raised an error ({exc}). Treated as a rejection: inform the user that the review service may be unavailable; if transient, the stage can be reported again later.",
    },
    "workflow_flow.review_no_conclusion": {
        "zh-CN": "审核未产出有效结论。本次按驳回处理：请告知用户审核服务可能异常；若属偶发，可稍后重新汇报本阶段。",
        "en-US": "The review produced no valid conclusion. Treated as a rejection: inform the user that the review service may be unavailable; if transient, the stage can be reported again later.",
    },
    "workflow_flow.no_open_conversation": {
        "zh-CN": "当前没有打开的对话。",
        "en-US": "No open conversation.",
    },
    "workflow_flow.deactivated_model": {
        "zh-CN": "工作流「{name}」已退出（{note}）。工作流状态已摘牌，你可以继续自由工作。",
        "en-US": "Workflow \"{name}\" has been deactivated ({note}). The workflow state has been cleared; you are free to continue working.",
    },
    "workflow_flow.model_auto_exited": {
        "zh-CN": "模型自主退出",
        "en-US": "exited by the model",
    },
    "workflow_flow.deactivated_by_user_notice": {
        "zh-CN": "用户已退出工作流「{name}」。工作流已摘牌，无需继续按流程推进；你可以继续自由工作。若用户之后要求恢复，可重新激活。",
        "en-US": "The user has deactivated workflow \"{name}\". The workflow state has been cleared and you no longer need to follow it; you are free to continue working. It can be reactivated if the user asks to resume it later.",
    },
    "workflow_flow.round_limit_reached": {
        "zh-CN": "工作流「{name}」的当前步骤「{step}」已进行 {rounds} 轮，达到单步轮数上限（{max_rounds}）。请立刻停下当前工作，告知用户已超过 {max_rounds} 轮，并询问是否还要继续。（工作流仍在进行中，等待用户决定；用户回复后可继续推进或退出。）",
        "en-US": "Workflow \"{name}\" has spent {rounds} rounds on the current step \"{step}\", reaching the per-step round limit ({max_rounds}). Stop the current work immediately, tell the user the {max_rounds}-round limit has been exceeded, and ask whether to continue. (The workflow stays active while waiting for the user's decision; it can be resumed or exited after the user replies.)",
    },

    # ── modules/workflow_manager.py（key 前缀 workflow_manager.） ──

    "workflow_manager.name_invalid": {
        "zh-CN": "工作流名称不合法：{name}（仅限小写字母/数字/连字符，3-64 字符）",
        "en-US": "Invalid workflow name: {name} (lowercase letters/digits/hyphens only, 3-64 characters)",
    },
    "workflow_manager.markdown_missing_frontmatter": {
        "zh-CN": "WORKFLOW.md 缺少 YAML frontmatter",
        "en-US": "WORKFLOW.md is missing YAML frontmatter",
    },
    "workflow_manager.wf_missing_name": {
        "zh-CN": "工作流缺少 name",
        "en-US": "Workflow is missing a name",
    },
    "workflow_manager.min_nodes_required": {
        "zh-CN": "至少需要一个开始节点和一个结束节点",
        "en-US": "At least one start node and one end node are required",
    },
    "workflow_manager.duplicate_node_id": {
        "zh-CN": "节点 id 重复：{nid}",
        "en-US": "Duplicate node id: {nid}",
    },
    "workflow_manager.missing_start_node": {
        "zh-CN": "缺少开始节点",
        "en-US": "Missing start node",
    },
    "workflow_manager.multiple_start_nodes": {
        "zh-CN": "开始节点只能有一个（当前 {count} 个）",
        "en-US": "There can only be one start node (currently {count})",
    },
    "workflow_manager.missing_end_node": {
        "zh-CN": "缺少结束节点",
        "en-US": "Missing end node",
    },
    "workflow_manager.ref_not_connected": {
        "zh-CN": "{label}未连接",
        "en-US": "{label} is not connected",
    },
    "workflow_manager.ref_target_missing": {
        "zh-CN": "{label}指向不存在的节点：{target}",
        "en-US": "{label} points to a nonexistent node: {target}",
    },
    "workflow_manager.ref_target_is_start": {
        "zh-CN": "{label}不能指向开始节点",
        "en-US": "{label} cannot point to the start node",
    },
    "workflow_manager.reject_limit_invalid": {
        "zh-CN": "审核「{name}」驳回上限必须 ≥ 1",
        "en-US": "Rejection limit for review \"{name}\" must be ≥ 1",
    },
    "workflow_manager.label_start": {
        "zh-CN": "开始节点",
        "en-US": "Start node",
    },
    "workflow_manager.label_stage": {
        "zh-CN": "阶段「{name}」",
        "en-US": "Stage \"{name}\"",
    },
    "workflow_manager.label_review_pass_route": {
        "zh-CN": "审核「{name}」的通过路由",
        "en-US": "Pass route of review \"{name}\"",
    },
    "workflow_manager.label_review_reject_route": {
        "zh-CN": "审核「{name}」的驳回路由",
        "en-US": "Reject route of review \"{name}\"",
    },
    "workflow_manager.label_branch_route": {
        "zh-CN": "分支「{name}」的出线",
        "en-US": "Outgoing edge of branch \"{name}\"",
    },
    "workflow_manager.structure_invalid": {
        "zh-CN": "工作流结构校验未通过：{errors}",
        "en-US": "Workflow validation failed: {errors}",
    },
    "workflow_manager.cannot_infer_workflows_dir": {
        "zh-CN": "无法确定用户工作流库目录",
        "en-US": "Cannot determine the user workflow library directory",
    },
    "workflow_manager.invalid_path": {
        "zh-CN": "非法路径",
        "en-US": "Invalid path",
    },
    "workflow_manager.builtin_not_deletable": {
        "zh-CN": "内置示例不可删除（可复制为用户工作流后删除副本）",
        "en-US": "Built-in examples cannot be deleted (copy to a user workflow and delete the copy instead)",
    },
    "workflow_manager.workflow_not_found": {
        "zh-CN": "工作流不存在：{name}",
        "en-US": "Workflow not found: {name}",
    },
    "workflow_manager.parse_failed_description": {
        "zh-CN": "（文件解析失败）",
        "en-US": "(failed to parse file)",
    },
    "workflow_manager.archive_source_not_dir": {
        "zh-CN": "source_dir 不是目录",
        "en-US": "source_dir is not a directory",
    },
    "workflow_manager.archive_missing_workflow_file": {
        "zh-CN": "目录中缺少 {filename}",
        "en-US": "Missing {filename} in the directory",
    },
    "workflow_manager.archive_parse_failed": {
        "zh-CN": "WORKFLOW.md 解析失败：{error}",
        "en-US": "Failed to parse WORKFLOW.md: {error}",
    },
    "workflow_manager.archive_name_mismatch": {
        "zh-CN": "目录名（{dir_name}）必须与 WORKFLOW.md 的 name 字段（{name}）一致",
        "en-US": "The directory name ({dir_name}) must match the name field in WORKFLOW.md ({name})",
    },
    "workflow_manager.archive_structure_invalid": {
        "zh-CN": "结构校验未通过：{errors}",
        "en-US": "Validation failed: {errors}",
    },
    "workflow_manager.archive_already_exists": {
        "zh-CN": "工作流「{name}」已存在。确认覆盖请设 overwrite=true。",
        "en-US": "Workflow \"{name}\" already exists. Set overwrite=true to confirm overwriting.",
    },
    "workflow_manager.archive_builtin_conflict": {
        "zh-CN": "与内置工作流「{name}」同名。归档后将创建用户副本遮蔽内置版本，确认请设 overwrite=true。",
        "en-US": "Same name as built-in workflow \"{name}\". Archiving will create a user copy that shadows the built-in version; set overwrite=true to confirm.",
    },
    "workflow_manager.archive_backup_failed": {
        "zh-CN": "覆盖前备份旧版本失败：{error}",
        "en-US": "Failed to back up the old version before overwriting: {error}",
    },
    "workflow_manager.archive_move_failed": {
        "zh-CN": "归档移动失败：{error}",
        "en-US": "Failed to move the archived directory: {error}",
    },

    # ── server/workflow_runtime_api.py（key 前缀 workflow_api.） ──

    "workflow_api.missing_workflow_name": {
        "zh-CN": "缺少工作流名称",
        "en-US": "Missing workflow name",
    },
    "workflow_api.conversation_manager_unavailable": {
        "zh-CN": "对话管理器不可用",
        "en-US": "Conversation manager unavailable",
    },
    "workflow_api.create_conversation_failed": {
        "zh-CN": "创建对话失败：{error}",
        "en-US": "Failed to create conversation: {error}",
    },
    "workflow_api.busy_cannot_activate": {
        "zh-CN": "智能体正在工作中，工作流仅可在空闲时激活。",
        "en-US": "The agent is busy; workflows can only be activated when idle.",
    },
    "workflow_api.activate_failed": {
        "zh-CN": "激活工作流失败：{error}",
        "en-US": "Failed to activate workflow: {error}",
    },
    "workflow_api.missing_conversation_id": {
        "zh-CN": "缺少 conversation_id",
        "en-US": "Missing conversation_id",
    },
    "workflow_api.status_read_failed": {
        "zh-CN": "读取工作流状态失败：{error}",
        "en-US": "Failed to read workflow status: {error}",
    },

    # ── server/workflow_page.py（key 前缀 workflow_page.） ──

    "workflow_page.list_failed": {
        "zh-CN": "加载工作流列表失败：{error}",
        "en-US": "Failed to load workflow list: {error}",
    },
    "workflow_page.workflow_not_found": {
        "zh-CN": "工作流不存在：{name}",
        "en-US": "Workflow not found: {name}",
    },
    "workflow_page.missing_workflow_object": {
        "zh-CN": "请求体缺少 workflow 对象",
        "en-US": "Request body is missing the workflow object",
    },
    "workflow_page.write_file_failed": {
        "zh-CN": "写入文件失败：{error}",
        "en-US": "Failed to write file: {error}",
    },
    "workflow_page.delete_failed": {
        "zh-CN": "删除失败：{error}",
        "en-US": "Deletion failed: {error}",
    },
}