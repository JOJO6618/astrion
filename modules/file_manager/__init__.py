# modules/file_manager.py - 文件管理器
# 本文件为兼容入口，实际实现已拆分到 modules/file_manager/ 目录下。

from modules.file_manager.base import FileManagerBase
from modules.file_manager.path_mixin import PathMixin
from modules.file_manager.crud_mixin import CrudMixin
from modules.file_manager.read_mixin import ReadMixin
from modules.file_manager.patch_mixin import PatchMixin
from modules.file_manager.replace_mixin import ReplaceMixin
from modules.file_manager.list_mixin import ListMixin

__all__ = ["FileManager"]


class FileManager(FileManagerBase, PathMixin, CrudMixin, ReadMixin, PatchMixin, ReplaceMixin, ListMixin):
    """文件管理器：组合各功能 mixin 的对外统一入口。"""
    pass
