# modules/persistent_terminal.py - 持久终端
# 本文件为兼容入口，实际实现已拆分到 modules/persistent_terminal/ 目录下。

from modules.persistent_terminal.base import PersistentTerminalBase
from modules.persistent_terminal.start import StartMixin
from modules.persistent_terminal.io import IoMixin
from modules.persistent_terminal.command import CommandMixin
from modules.persistent_terminal.lifecycle import LifecycleMixin

__all__ = ["PersistentTerminal"]


class PersistentTerminal(PersistentTerminalBase, StartMixin, IoMixin, CommandMixin, LifecycleMixin):
    """持久终端：组合各功能 mixin 的对外统一入口。"""
    pass
