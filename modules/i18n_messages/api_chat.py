"""Backend i18n message pack: server/chat/* API user-visible messages.

Pure data module — do not import anything here. Auto-discovered and merged
by modules/i18n.py at import time.
"""

MESSAGES = {
    # ── server/chat/permission.py ──
    "chat_permission.invalid_permission_mode": {
        "zh-CN": "无效权限模式，仅支持 readonly / approval / auto_approval / unrestricted",
        "en-US": "Invalid permission mode; only readonly / approval / auto_approval / unrestricted are supported",
    },
    "chat_permission.update_failed": {
        "zh-CN": "更新权限模式失败",
        "en-US": "Failed to update permission mode",
    },
    "chat_permission.pending_effective": {
        "zh-CN": "权限模式将在当前工具执行完成后生效",
        "en-US": "Permission mode will take effect after the current tool finishes",
    },
    "chat_permission.updated_immediately": {
        "zh-CN": "权限模式已更新并立即生效",
        "en-US": "Permission mode updated and now in effect",
    },
    "chat_permission.execution_host_admin_only": {
        "zh-CN": "仅宿主机管理员可切换执行环境",
        "en-US": "Only the host admin can switch the execution environment",
    },
    "chat_permission.invalid_execution_mode": {
        "zh-CN": "无效执行环境，仅支持 sandbox / direct",
        "en-US": "Invalid execution environment; only sandbox / direct are supported",
    },
    "chat_permission.execution_update_failed": {
        "zh-CN": "更新执行环境失败",
        "en-US": "Failed to update the execution environment",
    },
    "chat_permission.execution_pending_effective": {
        "zh-CN": "执行环境将在当前工具执行完成后生效",
        "en-US": "Execution environment will take effect after the current tool finishes",
    },
    "chat_permission.execution_updated_immediately": {
        "zh-CN": "执行环境已更新并立即生效",
        "en-US": "Execution environment updated and now in effect",
    },
    "chat_permission.network_host_admin_only": {
        "zh-CN": "仅宿主机管理员可切换网络权限",
        "en-US": "Only the host admin can change network permissions",
    },
    "chat_permission.invalid_network_permission": {
        "zh-CN": "无效网络权限，仅支持 restricted / full",
        "en-US": "Invalid network permission; only restricted / full are supported",
    },
    "chat_permission.network_update_failed": {
        "zh-CN": "更新网络权限失败",
        "en-US": "Failed to update network permissions",
    },
    "chat_permission.network_pending_effective": {
        "zh-CN": "网络权限将在当前工具执行完成后生效",
        "en-US": "Network permission will take effect after the current tool finishes",
    },
    "chat_permission.network_updated_immediately": {
        "zh-CN": "网络权限已更新并立即生效",
        "en-US": "Network permission updated and now in effect",
    },
    "chat_permission.invalid_work_mode": {
        "zh-CN": "无效运行模式，仅支持 plan / ask / execute",
        "en-US": "Invalid work mode; only plan / ask / execute are supported",
    },
    "chat_permission.work_mode_running_refused": {
        "zh-CN": "对话运行中，运行模式只能在空闲时切换",
        "en-US": "The conversation is running; work mode can only be changed while idle",
    },
    "chat_permission.work_mode_switch_failed": {
        "zh-CN": "切换运行模式失败",
        "en-US": "Failed to switch work mode",
    },
    "chat_permission.work_mode_updated_immediately": {
        "zh-CN": "运行模式已更新并立即生效",
        "en-US": "Work mode updated and now in effect",
    },
    "chat_permission.path_auth_host_admin_only": {
        "zh-CN": "仅宿主机管理员可管理路径授权",
        "en-US": "Only the host admin can manage path authorization",
    },
    "chat_permission.paths_must_be_arrays": {
        "zh-CN": "writable_paths/readable_extra_paths 必须为数组",
        "en-US": "writable_paths/readable_extra_paths must be arrays",
    },
    "chat_permission.root_path_forbidden": {
        "zh-CN": "禁止授权根目录 /",
        "en-US": "Authorizing the root directory / is forbidden",
    },
    "chat_permission.drive_root_forbidden": {
        "zh-CN": "禁止授权驱动器根目录（如 C:\\）",
        "en-US": "Authorizing a drive root (e.g. C:\\) is forbidden",
    },
    "chat_permission.deny_sensitive_path": {
        "zh-CN": "禁止授权敏感路径: {path}",
        "en-US": "Authorizing a sensitive path is forbidden: {path}",
    },
    "chat_permission.deny_sensitive_file": {
        "zh-CN": "禁止授权敏感文件: {path}",
        "en-US": "Authorizing a sensitive file is forbidden: {path}",
    },

    # ── server/chat/files.py ──
    "chat_files.upload_admin_disabled": {
        "zh-CN": "文件上传已被管理员禁用",
        "en-US": "File upload has been disabled by the admin",
    },
    "chat_files.upload_admin_disabled_message": {
        "zh-CN": "被管理员禁用上传",
        "en-US": "Upload disabled by admin",
    },
    "chat_files.file_not_found_in_request": {
        "zh-CN": "未找到文件",
        "en-US": "No file found",
    },
    "chat_files.missing_file_field": {
        "zh-CN": "请求中缺少文件字段",
        "en-US": "The request is missing the file field",
    },
    "chat_files.missing_file_content": {
        "zh-CN": "请求中缺少文件内容",
        "en-US": "The request is missing file content",
    },
    "chat_files.empty_filename": {
        "zh-CN": "文件名为空",
        "en-US": "File name is empty",
    },
    "chat_files.choose_file": {
        "zh-CN": "请选择要上传的文件",
        "en-US": "Please choose a file to upload",
    },
    "chat_files.invalid_filename": {
        "zh-CN": "非法文件名",
        "en-US": "Invalid file name",
    },
    "chat_files.unsupported_filename_chars": {
        "zh-CN": "文件名包含不支持的字符",
        "en-US": "The file name contains unsupported characters",
    },
    "chat_files.file_manager_uninitialized": {
        "zh-CN": "文件管理器未初始化",
        "en-US": "File manager is not initialized",
    },
    "chat_files.create_upload_dir_failed": {
        "zh-CN": "创建上传目录失败: {error}",
        "en-US": "Failed to create upload directory: {error}",
    },
    "chat_files.path_resolve_failed": {
        "zh-CN": "路径解析失败: {error}",
        "en-US": "Failed to resolve path: {error}",
    },
    "chat_files.save_failed": {
        "zh-CN": "保存文件失败: {error}",
        "en-US": "Failed to save file: {error}",
    },
    "chat_files.file_too_large": {
        "zh-CN": "文件过大",
        "en-US": "File too large",
    },
    "chat_files.file_too_large_message": {
        "zh-CN": "单个文件大小不可超过 {size_mb:.1f} MB",
        "en-US": "A single file cannot exceed {size_mb:.1f} MB",
    },
    "chat_files.missing_path_param": {
        "zh-CN": "缺少路径参数",
        "en-US": "Missing path parameter",
    },
    "chat_files.path_validation_failed": {
        "zh-CN": "路径校验失败",
        "en-US": "Path validation failed",
    },
    "chat_files.file_not_found": {
        "zh-CN": "文件不存在",
        "en-US": "File not found",
    },
    "chat_files.folder_not_found": {
        "zh-CN": "文件夹不存在",
        "en-US": "Folder not found",
    },

    # ── server/chat/misc.py ──
    "chat_misc.invalid_memory_type": {
        "zh-CN": "type 必须是 main 或 task",
        "en-US": "type must be main or task",
    },
    "chat_misc.missing_execution_id": {
        "zh-CN": "缺少 executionId 参数",
        "en-US": "Missing executionId parameter",
    },
    "chat_misc.snapshot_not_found": {
        "zh-CN": "未找到对应快照",
        "en-US": "Snapshot not found",
    },
    "chat_misc.missing_category": {
        "zh-CN": "缺少类别参数",
        "en-US": "Missing category parameter",
    },
    "chat_misc.missing_category_field": {
        "zh-CN": "请求体需要提供 category 字段",
        "en-US": "The request body must provide a category field",
    },
    "chat_misc.missing_enabled": {
        "zh-CN": "缺少启用状态",
        "en-US": "Missing enabled state",
    },
    "chat_misc.missing_enabled_field": {
        "zh-CN": "请求体需要提供 enabled 字段",
        "en-US": "The request body must provide an enabled field",
    },
    "chat_misc.tool_toggle_admin_disabled": {
        "zh-CN": "工具开关已被管理员禁用",
        "en-US": "Tool toggles have been disabled by the admin",
    },
    "chat_misc.admin_forced_disabled": {
        "zh-CN": "被管理员强制禁用",
        "en-US": "Force-disabled by admin",
    },
    "chat_misc.category_forced": {
        "zh-CN": "该工具类别已被管理员强制为启用/禁用，无法修改",
        "en-US": "This tool category is force-enabled or force-disabled by the admin and cannot be changed",
    },
    "chat_misc.admin_forced_state": {
        "zh-CN": "被管理员强制启用/禁用",
        "en-US": "Force-enabled or force-disabled by admin",
    },

    # ── server/chat/settings.py ──
    "chat_settings.thinking_mode_exception": {
        "zh-CN": "切换思考模式时发生异常",
        "en-US": "An error occurred while switching thinking mode",
    },
    "chat_settings.reasoning_effort_exception": {
        "zh-CN": "设置推理强度时发生异常",
        "en-US": "An error occurred while setting reasoning effort",
    },
    "chat_settings.missing_model_key": {
        "zh-CN": "缺少 model_key",
        "en-US": "Missing model_key",
    },
    "chat_settings.model_admin_disabled": {
        "zh-CN": "该模型已被管理员禁用",
        "en-US": "This model has been disabled by the admin",
    },
    "chat_settings.admin_forced_disabled": {
        "zh-CN": "被管理员强制禁用",
        "en-US": "Force-disabled by admin",
    },
    "chat_settings.personal_space_admin_disabled": {
        "zh-CN": "个人空间已被管理员禁用",
        "en-US": "Personal space has been disabled by the admin",
    },

    # ── server/chat/approval.py ──
    "chat_approval.question_not_found": {
        "zh-CN": "问题不存在",
        "en-US": "Question not found",
    },
    "chat_approval.plan_approval_not_found": {
        "zh-CN": "计划批准请求不存在",
        "en-US": "Plan approval request not found",
    },
    "chat_approval.approval_not_found": {
        "zh-CN": "审批请求不存在",
        "en-US": "Approval request not found",
    },

    # ── server/chat/terminal.py ──
    "chat_terminal.realtime_terminal_admin_disabled": {
        "zh-CN": "实时终端已被管理员禁用",
        "en-US": "The realtime terminal has been disabled by the admin",
    },
}