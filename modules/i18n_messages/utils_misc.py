"""Backend i18n message pack: utils 组 misc 用户可见消息。

覆盖：utils/api_client/chat_mixin.py、utils/tool_result_formatter/{file,terminal,agent_context,common}.py、
utils/context_manager/{compression_mixin,conversation_mixin}.py、
utils/conversation_manager/index_mixin.py。
纯数据模块 — 禁止任何 import；由 modules/i18n.py import 时自动聚合。
zh-CN 文案逐字复制自源码；en-US 为简洁的英文翻译（sentence case）。
"""

MESSAGES = {
    # ── api_client/chat_mixin.py（API 错误返回 error_message 字段） ──
    "api_client.connect_failed": {
        "zh-CN": "无法连接到API服务器: {error}",
        "en-US": "Cannot connect to the API server: {error}",
    },
    "api_client.request_timeout": {
        "zh-CN": "API请求超时",
        "en-US": "API request timed out",
    },
    "api_client.connection_lost": {
        "zh-CN": "API服务器连接断开: {error}",
        "en-US": "Disconnected from the API server: {error}",
    },

    # ── tool_result_formatter/file.py（操作结果确认文案） ──
    "fmt_file.unsupported_read_mode": {
        "zh-CN": "不支持的读取模式",
        "en-US": "Unsupported read mode",
    },
    "fmt_file.unknown_path": {
        "zh-CN": "未知路径",
        "en-US": "unknown path",
    },
    "fmt_file.empty_file_created": {
        "zh-CN": "已创建空文件: {path}",
        "en-US": "Created empty file: {path}",
    },
    "fmt_file.file_action_done": {
        "zh-CN": "已{action}文件: {path}",
        "en-US": "File {action}: {path}",
    },
    "fmt_file.renamed": {
        "zh-CN": "已重命名: {old_path} -> {new_path}",
        "en-US": "Renamed: {old_path} -> {new_path}",
    },
    "fmt_file.folder_created": {
        "zh-CN": "已创建文件夹: {path}",
        "en-US": "Created folder: {path}",
    },

    # ── tool_result_formatter/terminal.py ──
    "fmt_terminal.action_done": {
        "zh-CN": "{tag} 操作已完成。",
        "en-US": "{tag} operation completed.",
    },

    # ── tool_result_formatter/agent_context.py ──
    "fmt_agent.todo_update_default": {
        "zh-CN": "任务状态已更新",
        "en-US": "Task status updated",
    },

    # ── tool_result_formatter/common.py ──
    "fmt_common.unknown_error": {
        "zh-CN": "未知错误",
        "en-US": "Unknown error",
    },

    # ── context_manager/compression_mixin.py + conversation_mixin.py ──
    "ctx_mgr.conversation_not_found": {
        "zh-CN": "对话不存在: {conversation_id}",
        "en-US": "Conversation not found: {conversation_id}",
    },
    "ctx_mgr.nothing_to_compress": {
        "zh-CN": "当前对话没有可压缩的内容",
        "en-US": "The current conversation has no compressible content",
    },
    "ctx_mgr.duplicate_title_suffix": {
        "zh-CN": "{original_title} 的副本",
        "en-US": "Copy of {original_title}",
    },

    # ── conversation_manager/index_mixin.py ──
    "conv_mgr.untitled_conversation": {
        "zh-CN": "未命名对话",
        "en-US": "Untitled conversation",
    },
}