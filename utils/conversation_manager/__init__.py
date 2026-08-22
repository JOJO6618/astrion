# utils/conversation_manager.py - 对话管理器
# 本文件为兼容入口，实际实现已拆分到 utils/conversation_manager/ 目录下。

from utils.conversation_manager.base import ConversationManagerBase
from utils.conversation_manager.path_mixin import PathMixin
from utils.conversation_manager.index_mixin import IndexMixin
from utils.conversation_manager.metadata_mixin import MetadataMixin
from utils.conversation_manager.crud_mixin import CrudMixin
from utils.conversation_manager.token_mixin import TokenMixin
from utils.conversation_manager.list_search_mixin import ListSearchMixin
from utils.conversation_manager.stats_mixin import StatsMixin

__all__ = ["ConversationManager"]


class ConversationManager(ConversationManagerBase, PathMixin, IndexMixin, MetadataMixin, CrudMixin, TokenMixin, ListSearchMixin, StatsMixin):
    """对话管理器：组合各功能 mixin 的对外统一入口。"""
    pass
