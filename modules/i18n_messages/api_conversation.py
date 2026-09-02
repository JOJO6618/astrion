"""API conversation message pack.

User-visible messages produced by server/conversation.py (REST/WebSocket responses).
Pure data only — do not import anything here; merged into modules.i18n at import time.
Keys are prefixed with ``conversation.`` (the core table already owns
``conversation.default_title`` — no key below duplicates it).
"""

MESSAGES = {
    # ── _get_conversation_file_path（对话 ID 白名单校验） ──
    "conversation.invalid_conversation_id": {
        "zh-CN": "对话 ID 不合法",
        "en-US": "Invalid conversation ID",
    },
    # ── _build_safe_load_result（安全导航加载结果，返还前端） ──
    "conversation.load_failed_not_found": {
        "zh-CN": "对话不存在或加载失败",
        "en-US": "Conversation not found or failed to load",
    },
    "conversation.load_failed_detail": {
        "zh-CN": "对话加载失败: {conversation_id}",
        "en-US": "Failed to load conversation: {conversation_id}",
    },
    "conversation.unknown_title": {
        "zh-CN": "未知对话",
        "en-US": "Unknown chat",
    },
    "conversation.loaded_detail": {
        "zh-CN": "对话已加载: {conversation_id}",
        "en-US": "Conversation loaded: {conversation_id}",
    },

    # ── 输入草稿（GET/POST /api/input-draft） ──
    "conversation.read_input_draft_failed": {
        "zh-CN": "读取输入草稿失败: {error}",
        "en-US": "Failed to read input draft: {error}",
    },
    "conversation.input_draft_too_long": {
        "zh-CN": "输入草稿过长（最大 40000 字符）",
        "en-US": "Input draft is too long (max 40000 characters)",
    },
    "conversation.save_input_draft_failed": {
        "zh-CN": "保存输入草稿失败: {error}",
        "en-US": "Failed to save input draft: {error}",
    },

    # ── _resolve_target_terminal_for_workspace（异常消息经 str(exc) 直达前端 error 字段） ──
    "conversation.workspace_not_found": {
        "zh-CN": "工作区不存在",
        "en-US": "Workspace not found",
    },
    "conversation.project_not_found": {
        "zh-CN": "项目不存在",
        "en-US": "Project not found",
    },
    "conversation.system_not_initialized": {
        "zh-CN": "系统未初始化",
        "en-US": "System not initialized",
    },

    # ── GET /api/conversations ──
    "conversation.get_list_failed": {
        "zh-CN": "获取对话列表失败",
        "en-US": "Failed to get the conversation list",
    },
    "conversation.get_list_exception": {
        "zh-CN": "获取对话列表时发生异常",
        "en-US": "An error occurred while getting the conversation list",
    },

    # ── POST /api/conversations ──
    "conversation.manager_not_initialized": {
        "zh-CN": "对话管理器未初始化",
        "en-US": "Conversation manager not initialized",
    },
    "conversation.created_detail": {
        "zh-CN": "已创建新对话: {conversation_id}",
        "en-US": "New conversation created: {conversation_id}",
    },
    "conversation.create_exception": {
        "zh-CN": "创建对话时发生异常",
        "en-US": "An error occurred while creating the conversation",
    },

    # ── GET /api/conversations/<id> 与其后多处共用 ──
    "conversation.not_found_detail": {
        "zh-CN": "对话 {conversation_id} 不存在",
        "en-US": "Conversation {conversation_id} not found",
    },
    "conversation.get_info_exception": {
        "zh-CN": "获取对话信息时发生异常",
        "en-US": "An error occurred while getting conversation info",
    },

    # ── PUT /api/conversations/<id>/load ──
    "conversation.load_exception": {
        "zh-CN": "加载对话时发生异常",
        "en-US": "An error occurred while loading the conversation",
    },

    # ── DELETE /api/conversations/<id> ──
    "conversation.delete_exception": {
        "zh-CN": "删除对话时发生异常",
        "en-US": "An error occurred while deleting the conversation",
    },

    # ── GET /api/conversations/search ──
    "conversation.search_query_required": {
        "zh-CN": "请提供搜索关键词",
        "en-US": "Please provide a search keyword",
    },
    "conversation.search_exception": {
        "zh-CN": "搜索对话时发生异常",
        "en-US": "An error occurred while searching conversations",
    },

    # ── GET /api/conversations/<id>/messages ──
    "conversation.get_messages_exception": {
        "zh-CN": "获取对话消息时发生异常",
        "en-US": "An error occurred while getting conversation messages",
    },

    # ── GET /api/conversations/media/<media_id> ──
    "conversation.media_store_unavailable": {
        "zh-CN": "media_store 不可用",
        "en-US": "media_store is unavailable",
    },
    "conversation.media_id_required": {
        "zh-CN": "media_id 不能为空",
        "en-US": "media_id must not be empty",
    },
    "conversation.media_not_found": {
        "zh-CN": "媒体不存在",
        "en-US": "Media not found",
    },
    "conversation.media_file_not_found": {
        "zh-CN": "媒体文件不存在",
        "en-US": "Media file not found",
    },

    # ── 版本控制（/api/conversations/<id>/versioning*） ──
    "conversation.update_versioning_failed": {
        "zh-CN": "更新对话版本配置失败",
        "en-US": "Failed to update conversation versioning config",
    },
    "conversation.versioning_workspace_point_unsupported": {
        "zh-CN": "当前模式不支持工作区版本点，请切换到宿主机模式",
        "en-US": "Workspace version points are not supported in this mode; please switch to host mode",
    },
    "conversation.versioning_workspace_detail_unsupported": {
        "zh-CN": "当前模式不支持工作区版本详情，请切换到宿主机模式",
        "en-US": "Workspace version details are not supported in this mode; please switch to host mode",
    },
    "conversation.checkpoint_not_found": {
        "zh-CN": "未找到对应版本点",
        "en-US": "Version point not found",
    },
    "conversation.versioning_workspace_restore_unsupported": {
        "zh-CN": "当前模式不支持工作区回溯，请切换到宿主机模式",
        "en-US": "Workspace restore is not supported in this mode; please switch to host mode",
    },
    "conversation.restore_save_failed": {
        "zh-CN": "保存恢复后的对话失败",
        "en-US": "Failed to save the restored conversation",
    },
    "conversation.seq_required": {
        "zh-CN": "缺少有效 seq",
        "en-US": "A valid seq is required",
    },
    "conversation.versioning_not_enabled": {
        "zh-CN": "当前对话未开启版本管理",
        "en-US": "Versioning is not enabled for this conversation",
    },
    "conversation.duplicate_failed": {
        "zh-CN": "复制对话失败",
        "en-US": "Failed to duplicate conversation",
    },
    "conversation.version_restored_title": {
        "zh-CN": "版本回溯对话",
        "en-US": "Version-restored chat",
    },

    # ── 压缩（/api/conversations/<id>/compress*） ──
    "conversation.compress_blocked_by_admin": {
        "zh-CN": "压缩对话已被管理员禁用",
        "en-US": "Conversation compression has been disabled by the admin",
    },
    "conversation.compress_exception": {
        "zh-CN": "压缩对话时发生异常",
        "en-US": "An error occurred while compressing the conversation",
    },
    "conversation.compression_cancelled_error": {
        "zh-CN": "用户切换对话导致压缩取消",
        "en-US": "Compression cancelled because the user switched conversations",
    },
    "conversation.not_found_or_cancel_failed": {
        "zh-CN": "对话不存在或取消失败",
        "en-US": "Conversation not found or failed to cancel",
    },

    # ── 子智能体（/api/sub_agents*） ──
    "conversation.sub_agent_manager_unavailable": {
        "zh-CN": "子智能体管理器不可用",
        "en-US": "Sub-agent manager unavailable",
    },
    "conversation.sub_agent_task_not_found": {
        "zh-CN": "未找到对应子智能体任务",
        "en-US": "Sub-agent task not found",
    },
    "conversation.read_progress_failed": {
        "zh-CN": "读取进度失败: {error}",
        "en-US": "Failed to read progress: {error}",
    },
    "conversation.unsupported_stop_mode": {
        "zh-CN": "mode 只支持 terminate/soft_stop",
        "en-US": "mode only supports terminate/soft_stop",
    },
    "conversation.sub_agent_stop_forbidden": {
        "zh-CN": "无权限停止该子智能体任务",
        "en-US": "Not allowed to stop this sub-agent task",
    },
    "conversation.sub_agent_stop_failed": {
        "zh-CN": "停止子智能体失败",
        "en-US": "Failed to stop sub-agent",
    },

    # ── 后台命令（/api/background_commands*） ──
    "conversation.background_command_manager_unavailable": {
        "zh-CN": "后台命令管理器不可用",
        "en-US": "Background command manager unavailable",
    },
    "conversation.background_command_not_found": {
        "zh-CN": "未找到对应后台命令",
        "en-US": "Background command not found",
    },
    "conversation.background_command_access_forbidden": {
        "zh-CN": "无权限访问该后台命令",
        "en-US": "Not allowed to access this background command",
    },
    "conversation.background_command_stop_forbidden": {
        "zh-CN": "无权限停止该后台命令",
        "en-US": "Not allowed to stop this background command",
    },
    "conversation.background_command_stop_failed": {
        "zh-CN": "停止后台命令失败",
        "en-US": "Failed to stop background command",
    },

    # ── 复制（POST /api/conversations/<id>/duplicate） ──
    "conversation.duplicated_title": {
        "zh-CN": "复制的对话",
        "en-US": "Duplicated chat",
    },
    "conversation.duplicate_exception": {
        "zh-CN": "复制对话时发生异常",
        "en-US": "An error occurred while duplicating the conversation",
    },

    # ── 对话回顾（review_preview / review） ──
    "conversation.review_blocked_by_admin": {
        "zh-CN": "对话引用已被管理员禁用",
        "en-US": "Conversation review has been disabled by the admin",
    },
    "conversation.cannot_review_current_conversation": {
        "zh-CN": "无法引用当前对话",
        "en-US": "Cannot review the current conversation",
    },
    "conversation.review_preview_exception": {
        "zh-CN": "生成预览时发生异常",
        "en-US": "An error occurred while generating the preview",
    },
    "conversation.review_generate_exception": {
        "zh-CN": "生成对话回顾时发生异常",
        "en-US": "An error occurred while generating the conversation review",
    },

    # ── 统计 / 当前对话 ──
    "conversation.get_statistics_exception": {
        "zh-CN": "获取对话统计时发生异常",
        "en-US": "An error occurred while getting conversation statistics",
    },
    "conversation.not_found": {
        "zh-CN": "对话不存在",
        "en-US": "Conversation not found",
    },

    # ── 系统命令（WebSocket command_result / POST /api/commands） ──
    "conversation.cleared": {
        "zh-CN": "对话已清除",
        "en-US": "Conversation cleared",
    },
    "conversation.terminal_system_not_initialized": {
        "zh-CN": "终端系统未初始化",
        "en-US": "Terminal system not initialized",
    },
    "conversation.unknown_command": {
        "zh-CN": "未知命令: {command}",
        "en-US": "Unknown command: {command}",
    },
    "conversation.command_required": {
        "zh-CN": "命令不能为空",
        "en-US": "Command must not be empty",
    },
    "conversation.command_execution_exception": {
        "zh-CN": "命令执行异常: {error}",
        "en-US": "An error occurred while executing the command: {error}",
    },

    # ── token 统计 ──
    "conversation.get_token_statistics_exception": {
        "zh-CN": "获取token统计时发生异常",
        "en-US": "An error occurred while getting token statistics",
    },
}