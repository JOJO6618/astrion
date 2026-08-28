"""server 杂项域的后端 i18n 消息包（context / conversation_bootstrap /
app_legacy / socket_handlers / approval_agent / auto_approval_service）。

由 modules/i18n.py 自动聚合。纯数据，禁止 import。
"""

MESSAGES = {
    # ── server/context.py ──
    "context.no_workspace": {
        "zh-CN": "尚未创建任何工作区，请先在工作区管理界面中手动创建工作区",
        "en-US": "No workspace has been created yet. Please create one manually in the workspace management UI.",
    },
    "context.missing_workspace_id": {
        "zh-CN": "API 调用缺少 workspace_id",
        "en-US": "API call is missing workspace_id",
    },
    "context.create_conversation_failed": {
        "zh-CN": "创建对话失败",
        "en-US": "Failed to create the conversation",
    },
    "context.load_conversation_failed": {
        "zh-CN": "对话加载失败",
        "en-US": "Failed to load the conversation",
    },

    # ── server/conversation_bootstrap.py ──
    "bootstrap.missing_conversation_id": {
        "zh-CN": "缺少 conversation_id",
        "en-US": "Missing conversation_id",
    },
    "bootstrap.terminal_unavailable": {
        "zh-CN": "终端上下文不可用",
        "en-US": "Terminal context unavailable",
    },
    "bootstrap.conversation_not_found": {
        "zh-CN": "对话 {id} 不存在",
        "en-US": "Conversation {id} not found",
    },
    "bootstrap.unknown_conversation": {
        "zh-CN": "未知对话",
        "en-US": "Unknown conversation",
    },

    # ── server/app_legacy.py ──
    "legacy.rate_limited": {
        "zh-CN": "请求过于频繁，请稍后再试。",
        "en-US": "Too many requests. Please try again later.",
    },
    "legacy.admin_required": {
        "zh-CN": "需要管理员权限",
        "en-US": "Administrator permission required",
    },
    "legacy.create_conversation_failed": {
        "zh-CN": "创建对话失败",
        "en-US": "Failed to create the conversation",
    },
    "legacy.load_conversation_failed": {
        "zh-CN": "对话加载失败",
        "en-US": "Failed to load the conversation",
    },
    "legacy.tool_result_header": {
        "zh-CN": "[工具结果] {tool}",
        "en-US": "[Tool result] {tool}",
    },
    "legacy.tool_result_empty": {
        "zh-CN": "（无附加输出）",
        "en-US": "(no additional output)",
    },

    # ── server/socket_handlers.py ──
    "socket.not_logged_in": {
        "zh-CN": "未登录或连接凭证无效",
        "en-US": "Not logged in or invalid connection credentials",
    },
    "socket.stop_received": {
        "zh-CN": "停止请求已接收，正在停止任务...",
        "en-US": "Stop request received; stopping the task...",
    },
    "socket.terminal_disabled": {
        "zh-CN": "实时终端已被管理员禁用",
        "en-US": "The real-time terminal has been disabled by the administrator",
    },
    "socket.ws_chat_deprecated": {
        "zh-CN": "WebSocket 聊天已废弃，请使用 REST API (/api/tasks)",
        "en-US": "WebSocket chat is deprecated; please use the REST API (/api/tasks)",
    },
    "socket.ws_migration_guide": {
        "zh-CN": "前端已切换到轮询模式，请刷新页面",
        "en-US": "The frontend has switched to polling mode; please refresh the page",
    },
    "socket.empty_message": {
        "zh-CN": "消息不能为空",
        "en-US": "Message cannot be empty",
    },
    "socket.image_not_supported": {
        "zh-CN": "当前模型不支持图片，请切换到支持图片的模型",
        "en-US": "The current model does not support images; please switch to a model that supports images",
    },
    "socket.video_not_supported": {
        "zh-CN": "当前模型不支持视频，请切换到支持视频的模型",
        "en-US": "The current model does not support videos; please switch to a model that supports videos",
    },
    "socket.image_video_separate": {
        "zh-CN": "图片和视频请分开发送",
        "en-US": "Please send images and videos in separate messages",
    },

    # ── modules/approval_agent.py ──
    "approval_agent.config_missing": {
        "zh-CN": "审批智能体配置缺失",
        "en-US": "Approval agent configuration is missing",
    },
    "approval_agent.request_failed": {
        "zh-CN": "审批智能体请求失败({status})",
        "en-US": "Approval agent request failed ({status})",
    },
    "approval_agent.no_decision": {
        "zh-CN": "审批智能体未产出可执行决策",
        "en-US": "The approval agent did not produce an executable decision",
    },
    "approval_agent.no_reason": {
        "zh-CN": "无理由",
        "en-US": "no reason given",
    },
    "approval_agent.invalid_decision": {
        "zh-CN": "审批智能体返回非法决策",
        "en-US": "The approval agent returned an invalid decision",
    },
    "approval_agent.max_rounds": {
        "zh-CN": "审批智能体超出最大轮数",
        "en-US": "The approval agent exceeded the maximum number of rounds",
    },
    "approval_agent.round_progress": {
        "zh-CN": "审批轮次 {rounds}",
        "en-US": "Approval round {rounds}",
    },

    # ── modules/auto_approval_service.py ──
    "auto_approval.start": {
        "zh-CN": "自动审批开始",
        "en-US": "Auto-approval started",
    },
    "auto_approval.done": {
        "zh-CN": "自动审批结束",
        "en-US": "Auto-approval finished",
    },
    "auto_approval.takeover_failed": {
        "zh-CN": "人工接管失败",
        "en-US": "Manual takeover failed",
    },
}
