# modules/terminal_ops.py - 终端操作器
# 本文件为兼容入口，实际实现已拆分到 modules/terminal_ops/ 目录下。

from modules.terminal_ops.base import TerminalOperatorBase
from modules.terminal_ops.python import PythonMixin
from modules.terminal_ops.container import ContainerMixin
from modules.terminal_ops.command import CommandMixin
from modules.terminal_ops.run import RunMixin
from modules.terminal_ops.misc import MiscMixin

__all__ = ["TerminalOperator"]


class TerminalOperator(TerminalOperatorBase, PythonMixin, ContainerMixin, CommandMixin, RunMixin, MiscMixin):
    """终端操作器：组合各功能 mixin 的对外统一入口。"""
    pass
