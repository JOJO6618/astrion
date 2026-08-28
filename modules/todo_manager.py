# modules/todo_manager.py - TODO 列表管理

from __future__ import annotations

from copy import deepcopy
from typing import Dict, List, Any, Optional

try:
    from config import (
        TODO_MAX_TASKS,
        TODO_MAX_OVERVIEW_LENGTH,
        TODO_MAX_TASK_LENGTH,
    )
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import (  # type: ignore
        TODO_MAX_TASKS,
        TODO_MAX_OVERVIEW_LENGTH,
        TODO_MAX_TASK_LENGTH,
    )

from modules.i18n import tr


class TodoManager:
    """负责创建、更新和结束 TODO 列表"""

    MAX_TASKS = 8  # 固定为8，覆盖配置
    MAX_OVERVIEW_LENGTH = TODO_MAX_OVERVIEW_LENGTH
    MAX_TASK_LENGTH = TODO_MAX_TASK_LENGTH

    def __init__(self, context_manager):
        self.context_manager = context_manager

    def _get_current(self) -> Optional[Dict[str, Any]]:
        todo = getattr(self.context_manager, "todo_list", None)
        return deepcopy(todo) if todo else None

    def _save(self, todo: Optional[Dict[str, Any]]):
        self.context_manager.set_todo_list(todo)

    def _normalize_tasks(self, tasks: List[Any]) -> List[str]:
        normalized = []
        for item in tasks:
            title = ""
            if isinstance(item, dict):
                title = item.get("title", "")
            else:
                title = str(item)
            title = title.strip()
            if not title:
                continue
            normalized.append(title)
            if len(normalized) >= self.MAX_TASKS:
                break
        return normalized

    def create_todo_list(self, overview: str, tasks: List[Any]) -> Dict[str, Any]:
        # 若已有列表，直接覆盖
        current = None

        overview = (overview or "").strip()
        if not overview:
            return {"success": False, "error": tr("todo.overview_empty")}
        if len(overview) > self.MAX_OVERVIEW_LENGTH:
            return {
                "success": False,
                "error": tr("todo.overview_too_long", count=len(overview), max_length=self.MAX_OVERVIEW_LENGTH),
            }

        normalized_tasks = self._normalize_tasks(tasks or [])
        if not normalized_tasks:
            return {"success": False, "error": tr("todo.no_tasks")}
        if len(tasks or []) > self.MAX_TASKS:
            return {
                "success": False,
                "error": tr("todo.too_many_tasks", max_count=self.MAX_TASKS),
            }

        for title in normalized_tasks:
            if len(title) > self.MAX_TASK_LENGTH:
                return {
                    "success": False,
                    "error": tr("todo.task_too_long", title=title, max_length=self.MAX_TASK_LENGTH),
                }

        todo = {
            "overview": overview,
            "tasks": [
                {
                    "index": idx,
                    "title": title,
                    "status": "pending"
                }
                for idx, title in enumerate(normalized_tasks, start=1)
            ],
            "status": "active",
            "forced_finish": False,
            "forced_reason": None
        }

        self._save(todo)
        return {
            "success": True,
            "message": tr("todo.created"),
            "todo_list": todo
        }

    def update_task_status(self, task_indices: Any, completed: bool) -> Dict[str, Any]:
        todo = self._get_current()
        if not todo:
            return {"success": False, "error": tr("todo.no_todo_list")}
        if todo.get("status") in {"completed", "closed"}:
            return {"success": False, "error": tr("todo.list_closed")}

        # 兼容单个/多个序号
        if isinstance(task_indices, int):
            indices = [task_indices]
        elif isinstance(task_indices, list):
            indices = []
            for idx in task_indices:
                if isinstance(idx, int):
                    indices.append(idx)
        else:
            return {"success": False, "error": tr("todo.invalid_indices_type")}

        if not indices:
            return {"success": False, "error": tr("todo.no_indices")}

        valid_max = len(todo["tasks"])
        invalid = [i for i in indices if i < 1 or i > valid_max]
        if invalid:
            return {"success": False, "error": tr("todo.index_out_of_range", valid_max=valid_max, invalid=invalid)}

        # 去重保持顺序
        seen = set()
        normalized = []
        for i in indices:
            if i not in seen:
                seen.add(i)
                normalized.append(i)

        new_status = "done" if completed else "pending"
        for task_index in normalized:
            task = todo["tasks"][task_index - 1]
            task["status"] = new_status

        self._save(todo)
        all_done = all(t["status"] == "done" for t in todo["tasks"])
        if all_done:
            return {
                "success": True,
                "message": tr("todo.all_done"),
                "todo_list": todo
            }
        if len(normalized) == 1:
            msg = (
                tr("todo.task_done", index=normalized[0])
                if completed
                else tr("todo.task_undone", index=normalized[0])
            )
        else:
            joined = ", ".join(str(i) for i in normalized)
            msg = (
                tr("todo.tasks_all_done", indices=joined)
                if completed
                else tr("todo.tasks_all_undone", indices=joined)
            )
        return {
            "success": True,
            "message": msg,
            "todo_list": todo
        }

    def get_snapshot(self) -> Optional[Dict[str, Any]]:
        return self._get_current()
