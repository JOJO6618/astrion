# utils/context_manager.py - 上下文管理器（集成对话持久化和Token统计）
# 本文件为兼容入口，实际实现已拆分到 utils/context_manager/ 目录下。

from utils.context_manager.base import (
    ContextManagerBase,
    AUTO_SHALLOW_PLACEHOLDER,
    AUTO_SHALLOW_TOOL_WHITELIST,
)
from utils.context_manager.runtime_mixin import RuntimeMixin
from utils.context_manager.token_mixin import TokenMixin
from utils.context_manager.todo_annotation_mixin import TodoAnnotationMixin
from utils.context_manager.compression_mixin import CompressionMixin
from utils.context_manager.conversation_mixin import ConversationMixin
from utils.context_manager.project_mixin import ProjectMixin
from utils.context_manager.media_mixin import MediaMixin
from utils.context_manager.message_mixin import MessageMixin
from utils.context_manager.file_mixin import FileMixin

__all__ = [
    "ContextManager",
    "AUTO_SHALLOW_PLACEHOLDER",
    "AUTO_SHALLOW_TOOL_WHITELIST",
]


class ContextManager(ContextManagerBase, RuntimeMixin, TokenMixin, TodoAnnotationMixin, CompressionMixin, ConversationMixin, ProjectMixin, MediaMixin, MessageMixin, FileMixin):
    """上下文管理器：组合各功能 mixin 的对外统一入口。"""
    pass
