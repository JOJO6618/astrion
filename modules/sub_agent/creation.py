"""子智能体创建、查询与会话槽位管理工具。"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from config import SUB_AGENT_DEFAULT_TIMEOUT, SUB_AGENT_MAX_ACTIVE

from modules.i18n import tr


class SubAgentCreationMixin:
    """提供子智能体创建参数校验、任务ID生成、交付目录解析与槽位管理能力。"""

    tasks: Dict[str, Dict[str, Any]]
    conversation_agents: Dict[str, List[int]]
    project_path: Path

    def _select_task(
        self,
        task_id: Optional[str],
        agent_id: Optional[int],
        *,
        include_idle: bool = False,
    ) -> Optional[Dict]:
        self.reconcile_task_states()
        if task_id:
            return self.tasks.get(task_id)
        if agent_id is None:
            return None
        # 多智能体模式下子智能体常驻 idle（等待唤醒），terminate/stop 需要能选中它们
        allowed = {"pending", "running"}
        if include_idle:
            allowed = allowed | {"idle"}
        candidates = [
            task for task in self.tasks.values()
            if task.get("agent_id") == agent_id and task.get("status") in allowed
        ]
        if candidates:
            candidates.sort(key=lambda item: item.get("created_at", 0), reverse=True)
            return candidates[0]
        return None

    def _active_task_count(self, conversation_id: Optional[str] = None) -> int:
        self.reconcile_task_states(conversation_id=conversation_id)
        active = [t for t in self.tasks.values() if t.get("status") in {"pending", "running"}]
        if conversation_id:
            active = [t for t in active if t.get("conversation_id") == conversation_id]
        return len(active)

    def _ensure_agent_slot_available(self, conversation_id: str, agent_id: int) -> bool:
        used = self.conversation_agents.setdefault(conversation_id, [])
        return agent_id not in used

    def next_free_agent_id(self, conversation_id: Optional[str], extra_used: Optional[Set[int]] = None) -> int:
        """分配对话级全局最小空闲 agent_id。

        agent_id 是对话全局唯一的内部编号（不对外暴露：模型与用户只看到
        角色内编号显示名）。多智能体模式下创建实例时由系统自动分配，
        不接受调用方指定。
        """
        used: Set[int] = set()
        if conversation_id:
            for aid in self.conversation_agents.get(conversation_id, []) or []:
                try:
                    used.add(int(aid))
                except (TypeError, ValueError):
                    continue
        for task in self.tasks.values():
            if conversation_id and task.get("conversation_id") != conversation_id:
                continue
            try:
                aid = task.get("agent_id")
                if aid is not None:
                    used.add(int(aid))
            except (TypeError, ValueError):
                continue
        if extra_used:
            used.update(extra_used)
        n = 1
        while n in used:
            n += 1
        return n

    def _mark_agent_id_used(self, conversation_id: str, agent_id: int):
        used = self.conversation_agents.setdefault(conversation_id, [])
        if agent_id not in used:
            used.append(agent_id)

    def _validate_create_params(self, agent_id: Optional[int], summary: str, task: str, target_dir: Optional[str], *, multi_agent_mode: bool = False) -> Optional[str]:
        if agent_id is None:
            return "子智能体代号不能为空"
        try:
            agent_id = int(agent_id)
        except ValueError:
            return "子智能体代号必须是整数"
        if agent_id <= 0:
            return "子智能体代号必须为正整数"
        if not summary or not summary.strip():
            return "任务摘要不能为空"
        if not task or not task.strip():
            return "任务详情不能为空"
        # 多智能体模式不需要交付目录
        if not multi_agent_mode and target_dir is None:
            return "指定文件夹不能为空"
        return None

    def _generate_task_id(self, agent_id: int) -> str:
        suffix = uuid.uuid4().hex[:6]
        return f"sub_{agent_id}_{int(time.time())}_{suffix}"

    def _resolve_deliverables_dir(self, relative_dir: Optional[str], *, multi_agent_mode: bool = False) -> Path:
        relative_dir = (relative_dir or "").strip()
        # 多智能体模式：没有交付目录概念，直接使用项目根目录
        if multi_agent_mode and not relative_dir:
            return self.project_path.resolve()
        if not relative_dir:
            raise ValueError(tr("sub_agent_creation.deliverables_dir_required"))
        deliverables_path = (self.project_path / relative_dir).resolve()
        if not str(deliverables_path).startswith(str(self.project_path)):
            raise ValueError(tr("sub_agent_creation.deliverables_dir_outside"))
        if deliverables_path.exists():
            raise ValueError(tr("sub_agent_creation.deliverables_dir_not_new"))
        deliverables_path.mkdir(parents=True, exist_ok=True)
        return deliverables_path
