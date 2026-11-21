"""Backend i18n message pack: manager-family user-visible error messages.

Covers modules/host_workspace_manager.py, modules/user_question_manager.py,
modules/plan_approval_manager.py and modules/tool_approval_manager.py.

旧多用户账号体系（modules/user_manager.py 的注册/邀请码/多用户项目）与
API 用户（modules/api_user_manager.py）已随 Docker fork 移出主仓。

Pure data module — do not import anything here.
Auto-discovered and merged by modules/i18n.py at import time.
zh-CN copy is verbatim from source; en-US is concise product-level
English (sentence case).
"""

MESSAGES = {
    # ── modules/host_workspace_manager.py ──
    "host_ws.config_format_error": {
        "zh-CN": "host_workspaces 配置格式错误（非 JSON 对象），已停止写入以避免覆盖原文件",
        "en-US": "host_workspaces config format is invalid (not a JSON object); write stopped to avoid overwriting the original file",
    },
    "host_ws.config_parse_failed": {
        "zh-CN": "host_workspaces 配置解析失败，已停止写入以避免覆盖原文件: {error}",
        "en-US": "Failed to parse host_workspaces config; write stopped to avoid overwriting the original file: {error}",
    },
    "host_ws.path_empty": {
        "zh-CN": "工作区路径不能为空",
        "en-US": "Workspace path cannot be empty",
    },
    "host_ws.workspace_id_missing": {
        "zh-CN": "缺少 workspace_id",
        "en-US": "Missing workspace_id",
    },
    "host_ws.name_empty": {
        "zh-CN": "工作区名称不能为空",
        "en-US": "Workspace name cannot be empty",
    },
    "host_ws.workspace_not_found": {
        "zh-CN": "工作区不存在",
        "en-US": "Workspace not found",
    },
    "host_ws.default_workspace_label": {
        "zh-CN": "默认工作区",
        "en-US": "Default workspace",
    },

    # ── modules/user_question_manager.py ──
    "user_question.answer_empty": {
        "zh-CN": "回答不能为空",
        "en-US": "Answer cannot be empty",
    },
    "user_question.question_not_found": {
        "zh-CN": "问题不存在",
        "en-US": "Question not found",
    },
    "user_question.no_permission": {
        "zh-CN": "无权限回答该问题",
        "en-US": "No permission to answer this question",
    },
    "user_question.option_not_found": {
        "zh-CN": "选项不存在",
        "en-US": "Option not found",
    },

    # ── modules/plan_approval_manager.py ──
    "plan_approval.request_not_found": {
        "zh-CN": "计划批准请求不存在",
        "en-US": "Plan approval request not found",
    },
    "plan_approval.no_permission": {
        "zh-CN": "无权限处理该计划批准请求",
        "en-US": "No permission to handle this plan approval request",
    },

    # ── modules/tool_approval_manager.py ──
    "tool_approval.decision_invalid": {
        "zh-CN": "decision 仅支持 approved / rejected",
        "en-US": "decision must be 'approved' or 'rejected'",
    },
    "tool_approval.request_not_found": {
        "zh-CN": "审批请求不存在",
        "en-US": "Approval request not found",
    },
    "tool_approval.no_permission": {
        "zh-CN": "无权限操作该审批请求",
        "en-US": "No permission to operate on this approval request",
    },
    "tool_approval.human_not_required": {
        "zh-CN": "该请求由 AI 自动审核，无需人工审批",
        "en-US": "This request is reviewed automatically; no human decision is required",
    },
}