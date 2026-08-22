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
            return {"success": False, "error": "任务概述不能为空。"}
        if len(overview) > self.MAX_OVERVIEW_LENGTH:
            return {
                "success": False,
                "error": f"任务概述过长（当前 {len(overview)} 字），请精简至 {self.MAX_OVERVIEW_LENGTH} 字以内。"
            }

        normalized_tasks = self._normalize_tasks(tasks or [])
        if not normalized_tasks:
            return {"success": False, "error": "需要至少提供一个任务。"}
        if len(tasks or []) > self.MAX_TASKS:
            return {
                "success": False,
                "error": f"任务数量过多，最多允许 {self.MAX_TASKS} 个任务。"
            }

        for title in normalized_tasks:
            if len(title) > self.MAX_TASK_LENGTH:
                return {
                    "success": False,
                    "error": f"任务「{title}」过长，请控制在 {self.MAX_TASK_LENGTH} 字以内。"
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
            "message": "待办列表已创建（覆盖之前的列表）。",
            "todo_list": todo
        }

    def update_task_status(self, task_indices: Any, completed: bool) -> Dict[str, Any]:
        todo = self._get_current()
        if not todo:
            return {"success": False, "error": "当前没有待办列表，请先创建。"}
        if todo.get("status") in {"completed", "closed"}:
            return {"success": False, "error": "待办列表已结束，无法继续修改。"}

        # 兼容单个/多个序号
        if isinstance(task_indices, int):
            indices = [task_indices]
        elif isinstance(task_indices, list):
            indices = []
            for idx in task_indices:
                if isinstance(idx, int):
                    indices.append(idx)
        else:
            return {"success": False, "error": "task_index 或 task_indices 必须是数字或数字数组。"}

        if not indices:
            return {"success": False, "error": "请提供至少一个任务序号。"}

        valid_max = len(todo["tasks"])
        invalid = [i for i in indices if i < 1 or i > valid_max]
        if invalid:
            return {"success": False, "error": f"任务序号超出范围（1-{valid_max}）：{invalid}"}

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
                "message": "所有任务已完成。",
                "todo_list": todo
            }
        if len(normalized) == 1:
            msg = f"任务 {normalized[0]}{'完成' if completed else '取消完成'}。"
        else:
            joined = ", ".join(str(i) for i in normalized)
            msg = f"任务 {joined}{'全部完成' if completed else '已取消完成'}。"
        return {
            "success": True,
            "message": msg,
            "todo_list": todo
        }

    def get_snapshot(self) -> Optional[Dict[str, Any]]:
        return self._get_current()
