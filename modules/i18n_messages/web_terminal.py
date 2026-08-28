"""Message pack: web_terminal (core/web_terminal.py).

WebTerminal 对话管理方法返回 dict 的 error/message（到达前端显示）。
纯数据模块：禁止 import modules.i18n；由 modules/i18n.py import 时自动聚合。
插值用 str.format 命名参数：tr("web_terminal.<key>", name=value)。
"""

MESSAGES = {
    # ── create_new_conversation ──
    "web_terminal.conversation_created": {
        "zh-CN": "已创建新对话: {conversation_id}",
        "en-US": "New conversation created: {conversation_id}",
    },
    "web_terminal.conversation_create_failed": {
        "zh-CN": "创建新对话失败: {error}",
        "en-US": "Failed to create a new conversation: {error}",
    },

    # ── load_conversation ──
    "web_terminal.conversation_data_missing_error": {
        "zh-CN": "对话数据缺失",
        "en-US": "Conversation data is missing",
    },
    "web_terminal.conversation_data_missing_message": {
        "zh-CN": "对话数据缺失: {conversation_id}",
        "en-US": "Conversation data is missing: {conversation_id}",
    },
    "web_terminal.unknown_conversation_title": {
        "zh-CN": "未知对话",
        "en-US": "Unknown conversation",
    },
    "web_terminal.conversation_loaded": {
        "zh-CN": "对话已加载: {conversation_id}",
        "en-US": "Conversation loaded: {conversation_id}",
    },
    "web_terminal.conversation_not_found_error": {
        "zh-CN": "对话不存在或加载失败",
        "en-US": "Conversation does not exist or failed to load",
    },
    "web_terminal.conversation_load_failed": {
        "zh-CN": "对话加载失败: {conversation_id}",
        "en-US": "Failed to load conversation: {conversation_id}",
    },
    "web_terminal.conversation_load_exception": {
        "zh-CN": "加载对话异常: {error}",
        "en-US": "Error loading conversation: {error}",
    },

    # ── get_conversations_list ──
    "web_terminal.conversation_list_failed": {
        "zh-CN": "获取对话列表失败: {error}",
        "en-US": "Failed to get the conversation list: {error}",
    },

    # ── delete_conversation ──
    "web_terminal.conversation_deleted": {
        "zh-CN": "对话已删除: {conversation_id}",
        "en-US": "Conversation deleted: {conversation_id}",
    },
    "web_terminal.conversation_delete_failed_error": {
        "zh-CN": "删除失败",
        "en-US": "Delete failed",
    },
    "web_terminal.conversation_delete_failed": {
        "zh-CN": "对话删除失败: {conversation_id}",
        "en-US": "Failed to delete conversation: {conversation_id}",
    },
    "web_terminal.conversation_delete_exception": {
        "zh-CN": "删除对话异常: {error}",
        "en-US": "Error deleting conversation: {error}",
    },

    # ── search_conversations ──
    "web_terminal.conversation_search_failed": {
        "zh-CN": "搜索对话失败: {error}",
        "en-US": "Failed to search conversations: {error}",
    },
}