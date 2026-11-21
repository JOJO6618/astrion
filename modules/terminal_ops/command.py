# modules/terminal_ops/command.py - 命令校验与工作目录解析（Host-only）

from pathlib import Path
from typing import Optional, Tuple

from modules.execution_scope import current_execution_scope
from modules.scoped_execution_policy import scoped_work_path


class CommandMixin:
    """TerminalOperator command 能力 mixin。"""

    def _validate_command(self, command: str) -> Tuple[bool, str]:
        """读取执行器所属用户的最新规则，不使用部署级或内置词表。"""
        from modules.command_blocking import validate_command

        return validate_command(command, getattr(self, "data_dir", None))

    @staticmethod
    def _clamp_timeout(requested: Optional[int], default: int, max_limit: int) -> int:
        """对timeout进行默认化与上限夹紧。"""
        if not requested or requested <= 0:
            return default
        return min(int(requested), max_limit)

    def _resolve_work_path(self, working_dir: Optional[str]) -> Path:
        if getattr(self, 'execution_binding_error', None) and not working_dir:
            raise ValueError(self.execution_binding_error)
        scope = current_execution_scope()
        if scope is None or not scope.is_sub_agent:
            base = Path(getattr(self, 'execution_root', self.project_path))
            working_dir = str((base / working_dir).resolve()) if working_dir else str(base)
        return scoped_work_path(self.project_path, working_dir, scope)
