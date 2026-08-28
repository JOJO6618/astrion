"""Message pack: core_commands (core/main_terminal_parts/commands.py).

斜杠命令 handler 输出的命令结果文本（显示在聊天/CLI，结果即给用户看）。
纯数据模块：禁止 import modules.i18n；由 modules/i18n.py import 时自动聚合。
插值用 str.format 命名参数：tr("commands.<key>", name=value)。
"""

MESSAGES = {
    # ── 命令分发 / 任务处理 ──
    "commands.unknown_command": {
        "zh-CN": "未知命令: {cmd}",
        "en-US": "Unknown command: {cmd}",
    },
    "commands.context_over_limit": {
        "zh-CN": "当前对话上下文已达 {current_tokens} tokens，超过模型上限 {max_context_tokens}，请先压缩或清理上下文后再试。",
        "en-US": "The conversation context has reached {current_tokens} tokens, exceeding the model limit of {max_context_tokens}. Please compress or clean up the context and try again.",
    },
    "commands.context_warning": {
        "zh-CN": "当前上下文约占 {usage_percent:.1f}%（{current_tokens}/{max_context_tokens}），建议使用压缩功能。",
        "en-US": "The context is at about {usage_percent:.1f}% ({current_tokens}/{max_context_tokens}); compression is recommended.",
    },
    "commands.task_done_placeholder": {
        "zh-CN": "已完成操作。",
        "en-US": "Operation completed.",
    },

    # ── /conversations ──
    "commands.no_conversations": {
        "zh-CN": "暂无对话记录",
        "en-US": "No conversations yet",
    },
    "commands.conversations_list_failed": {
        "zh-CN": "获取对话列表失败: {error}",
        "en-US": "Failed to get the conversation list: {error}",
    },
    "commands.invalid_count_default": {
        "zh-CN": "无效数量，使用默认值10",
        "en-US": "Invalid count; using the default of 10",
    },

    # ── /load ──
    "commands.load_need_id": {
        "zh-CN": "请指定对话ID",
        "en-US": "Please specify a conversation ID",
    },
    "commands.load_usage_hint": {
        "zh-CN": "使用方法: /load <对话ID>",
        "en-US": "Usage: /load <conversation ID>",
    },
    "commands.conversation_loaded": {
        "zh-CN": "对话已加载: {conversation_id}",
        "en-US": "Conversation loaded: {conversation_id}",
    },
    "commands.messages_count": {
        "zh-CN": "消息数量: {count}",
        "en-US": "Message count: {count}",
    },
    "commands.conversation_load_failed": {
        "zh-CN": "对话加载失败",
        "en-US": "Failed to load conversation",
    },
    "commands.conversation_load_exception": {
        "zh-CN": "加载对话异常: {error}",
        "en-US": "Error loading conversation: {error}",
    },

    # ── /new ──
    "commands.conversation_created": {
        "zh-CN": "已创建新对话: {conversation_id}",
        "en-US": "New conversation created: {conversation_id}",
    },
    "commands.conversation_create_failed": {
        "zh-CN": "创建新对话失败: {error}",
        "en-US": "Failed to create a new conversation: {error}",
    },
    "commands.new_conversation_started": {
        "zh-CN": "已开始新对话",
        "en-US": "New conversation started",
    },

    # ── /save / 状态保存 ──
    "commands.conversation_saved": {
        "zh-CN": "对话已保存",
        "en-US": "Conversation saved",
    },
    "commands.conversation_save_failed": {
        "zh-CN": "对话保存失败",
        "en-US": "Failed to save conversation",
    },
    "commands.conversation_save_exception": {
        "zh-CN": "保存对话异常: {error}",
        "en-US": "Error saving conversation: {error}",
    },
    "commands.state_saved": {
        "zh-CN": "状态已保存",
        "en-US": "State saved",
    },
    "commands.state_save_failed": {
        "zh-CN": "状态保存失败: {error}",
        "en-US": "Failed to save state: {error}",
    },

    # ── /terminals /files ──
    "commands.no_active_terminals": {
        "zh-CN": "当前没有活动的终端会话",
        "en-US": "No active terminal sessions",
    },
    "commands.file_tree_unavailable_host": {
        "zh-CN": "⚠️ 宿主机模式下文件树不可用",
        "en-US": "⚠️ File tree is unavailable in host mode",
    },

    # ── /exit ──
    "commands.exiting": {
        "zh-CN": "正在退出...",
        "en-US": "Exiting...",
    },

    # ── /memory ──
    "commands.memory_backup_saved": {
        "zh-CN": "备份保存到: {path}",
        "en-US": "Backup saved to: {path}",
    },

    # ── /mode ──
    "commands.invalid_mode": {
        "zh-CN": "无效模式: {mode}。可选: fast / thinking / deep",
        "en-US": "Invalid mode: {mode}. Available: fast / thinking / deep",
    },
    "commands.mode_already": {
        "zh-CN": "当前已是 {label}",
        "en-US": "Already in {label}",
    },
    "commands.mode_switched": {
        "zh-CN": "已切换到: {label}",
        "en-US": "Switched to: {label}",
    },
}