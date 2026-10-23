"""Safely bind child working directories without changing the project identity."""
from __future__ import annotations

from pathlib import Path


class SubAgentCreationMixin:
    project_path: Path

    def _resolve_working_dir(self, value: str | None, *, agent_id: int) -> Path:
        if value is None or value == "":
            return self._new_working_dir(agent_id)
        if not isinstance(value, str):
            raise ValueError("working_dir必须为目录路径。")
        root = self.project_path
        candidate = root / value
        resolved = candidate.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("working_dir必须位于当前工作区内。") from exc
        if candidate.absolute() != resolved:
            raise ValueError("working_dir不能包含符号链接或不规范的路径。")
        from modules.file_manager.scoped_io import mkdir_checked
        mkdir_checked(resolved, create_roots=[root], exist_ok=True)
        if not resolved.is_dir():
            raise ValueError("working_dir不是目录。")
        return resolved

    def _new_working_dir(self, agent_id: int) -> Path:
        base = self.project_path / ".astrion" / "sub_agent_work"
        from modules.file_manager.scoped_io import mkdir_checked
        number = 1
        while True:
            candidate = base / (f"agent_{agent_id}" + (f"_{number}" if number > 1 else ""))
            try:
                mkdir_checked(candidate, create_roots=[self.project_path], exist_ok=False)
                return candidate
            except FileExistsError:
                number += 1
