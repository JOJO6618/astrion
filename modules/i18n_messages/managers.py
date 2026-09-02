"""Backend i18n message pack: manager-family user-visible error messages.

Covers modules/user_manager.py, modules/host_workspace_manager.py,
modules/api_user_manager.py, modules/user_question_manager.py,
modules/plan_approval_manager.py and modules/tool_approval_manager.py.

Pure data module — do not import anything here.
Auto-discovered and merged by modules/i18n.py at import time.
zh-CN copy is verbatim from source; en-US is concise product-level
English (sentence case).
"""

MESSAGES = {
    # ── modules/user_manager.py ──
    "user_mgr.password_min_length": {
        "zh-CN": "密码长度至少 8 位。",
        "en-US": "Password must be at least 8 characters.",
    },
    "user_mgr.username_registered": {
        "zh-CN": "该用户名已被注册。",
        "en-US": "This username is already registered.",
    },
    "user_mgr.email_registered": {
        "zh-CN": "该邮箱已被注册。",
        "en-US": "This email is already registered.",
    },
    "user_mgr.workspace_id_rule": {
        "zh-CN": "项目 ID 只能包含字母、数字、点、下划线或连字符，长度 1-40。",
        "en-US": "Project ID may only contain letters, digits, dots, underscores or hyphens, 1-40 characters.",
    },
    "user_mgr.workspace_id_reserved": {
        "zh-CN": "该项目名称已被系统保留，请换一个名称。",
        "en-US": "This project name is reserved by the system. Please choose another name.",
    },
    "user_mgr.workspace_name_empty": {
        "zh-CN": "项目名称不能为空",
        "en-US": "Project name cannot be empty",
    },
    "user_mgr.workspace_not_found": {
        "zh-CN": "项目不存在",
        "en-US": "Project not found",
    },
    "user_mgr.workspace_path_invalid": {
        "zh-CN": "项目路径不合法",
        "en-US": "Invalid project path",
    },
    "user_mgr.user_not_found": {
        "zh-CN": "用户不存在",
        "en-US": "User not found",
    },
    "user_mgr.user_not_found_dot": {
        "zh-CN": "用户不存在。",
        "en-US": "User not found.",
    },
    "user_mgr.invite_code_empty": {
        "zh-CN": "邀请码不能为空。",
        "en-US": "Invite code cannot be empty.",
    },
    "user_mgr.invite_code_too_long": {
        "zh-CN": "邀请码长度不能超过 64 个字符。",
        "en-US": "Invite code cannot exceed 64 characters.",
    },
    "user_mgr.remaining_must_be_integer_or_null": {
        "zh-CN": "remaining 必须是整数或 null。",
        "en-US": "remaining must be an integer or null.",
    },
    "user_mgr.remaining_not_negative": {
        "zh-CN": "remaining 不能小于 0。",
        "en-US": "remaining cannot be less than 0.",
    },
    "user_mgr.username_rule": {
        "zh-CN": "用户名需为 3-32 位小写字母、数字、下划线或连字符。",
        "en-US": "Username must be 3-32 characters using lowercase letters, digits, underscores or hyphens.",
    },
    "user_mgr.email_invalid": {
        "zh-CN": "邮箱格式不正确。",
        "en-US": "Invalid email format.",
    },
    "user_mgr.users_file_parse_failed": {
        "zh-CN": "无法解析用户数据文件: {file_path}",
        "en-US": "Failed to parse user data file: {file_path}",
    },
    "user_mgr.invite_file_parse_failed": {
        "zh-CN": "无法解析邀请码文件: {file_path}",
        "en-US": "Failed to parse invite codes file: {file_path}",
    },
    "user_mgr.invite_code_invalid": {
        "zh-CN": "邀请码不存在或已失效。",
        "en-US": "Invite code is invalid or has expired.",
    },
    "user_mgr.invite_code_used": {
        "zh-CN": "邀请码已被使用。",
        "en-US": "Invite code has already been used.",
    },

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

    # ── modules/api_user_manager.py ──
    "api_user_mgr.user_exists": {
        "zh-CN": "该 API 用户已存在",
        "en-US": "This API user already exists",
    },
    "api_user_mgr.user_not_found": {
        "zh-CN": "用户不存在",
        "en-US": "User not found",
    },
    "api_user_mgr.token_not_found": {
        "zh-CN": "未找到 token 记录",
        "en-US": "Token record not found",
    },
    "api_user_mgr.missing_token_secret": {
        "zh-CN": "缺少 API_TOKEN_SECRET 且无可用明文 token",
        "en-US": "API_TOKEN_SECRET is missing and no plaintext token is available",
    },
    "api_user_mgr.username_empty": {
        "zh-CN": "用户名不能为空",
        "en-US": "Username cannot be empty",
    },
    "api_user_mgr.invalid_workspace_id": {
        "zh-CN": "workspace_id 只能包含字母、数字、点、下划线或连字符（1-40 位，以字母或数字开头）",
        "en-US": "workspace_id may only contain letters, digits, dots, underscores or hyphens (1-40 chars, starting with a letter or digit)",
    },
    "api_user_mgr.users_file_parse_failed": {
        "zh-CN": "无法解析 API 用户文件: {file_path} ({error})",
        "en-US": "Failed to parse API user file: {file_path} ({error})",
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
}