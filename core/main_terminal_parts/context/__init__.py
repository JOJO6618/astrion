# core/main_terminal_parts/context.py - 主终端上下文构建
# 本文件为兼容入口，实际实现已拆分到 core/main_terminal_parts/context/ 目录下。

from core.main_terminal_parts.context.base import MainTerminalContextBase
from core.main_terminal_parts.context.mode import ModeMixin
from core.main_terminal_parts.context.memory import MemoryMixin
from core.main_terminal_parts.context.conversation import ConversationMixin
from core.main_terminal_parts.context.messages import MessagesMixin
from core.main_terminal_parts.context.prompt import PromptMixin

__all__ = ["MainTerminalContextMixin"]


class MainTerminalContextMixin(MainTerminalContextBase, ModeMixin, MemoryMixin, ConversationMixin, MessagesMixin, PromptMixin):
    """主终端上下文构建：组合各功能 mixin 的对外统一入口。"""
    pass
