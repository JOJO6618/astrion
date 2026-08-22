"""工作流（Workflow）对话级运行状态管理。

状态目录：`{data_dir}/workflow_states/<conversation_id>/`
- ``state.json``：运行状态（current / reject_counts / stage_rounds / history / pending_notices …）
- ``WORKFLOW.md``：激活时刻的工作流定义原样快照。运行期一切读取（下一节点详情、
  分支候选、审核 prompt、maxRejects）只读快照——库文件在运行期间被修改不影响本实例。

设计要点（定稿文档 docs/workflow_feature_plan.md）：
- 柔性原则：工作流是智能体的辅助流程，不是宿主。所有终态（completed/stopped/failed）
  都只是「摘牌」——改状态 + 停止注入 + 柔性通知，绝不掐断智能体自身的工作循环。
- review 是瞬态节点：同步审核完直接走到下一站，``current_node_id`` 只停
  stage / branch / end；review 只作为 history 记录。
- 消息游标 ``stage_start_msg_index``：进入阶段时记录 conversation_history 长度，
  审核 payload 据此截取本阶段的工作痕迹。
- ``pending_notices``：柔性通知池（用户退出等），由统一完成通知轮询器消费。

不依赖 web_terminal，只接受 data_dir 与 conversation_id，便于单测与复用。
"""

from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from modules.workflow_manager import workflow_from_markdown

WORKFLOW_STATES_DIRNAME = "workflow_states"
WORKFLOW_SNAPSHOT_FILENAME = "WORKFLOW.md"

# 状态机
STATUS_ACTIVE = "active"
STATUS_COMPLETED = "completed"
STATUS_STOPPED = "stopped"
STATUS_FAILED = "failed"

# 退出原因
REASON_USER = "user"                 # 用户主动退出（slash / 对话指令）
REASON_MODEL = "model"               # 模型自主退出
REASON_MAX_REJECTS = "max_rejects"   # 连续驳回撞上限
REASON_COMPLETED = "completed"       # 走到 end 正常完成

PathLike = Union[str, Path]

_SAFE_CONVERSATION_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_conversation_id(conversation_id: str) -> str:
    cid = str(conversation_id or "").strip()
    if not cid or not _SAFE_CONVERSATION_ID.match(cid):
        raise ValueError(f"非法 conversation_id: {conversation_id!r}")
    return cid


def _empty_state() -> Dict[str, Any]:
    return {
        "active": False,
        "workflow_name": "",
        "status": None,
        "exit_reason": None,
        "current_node_id": None,
        "stage_rounds": 0,
        "round_limit_notified": False,
        "stage_start_msg_index": 0,
        "reject_counts": {},
        "history": [],
        "pending_notices": [],
        "started_at": None,
    }


class WorkflowStateManager:
    """对话级工作流状态。一个实例对应一个对话的 workflow_states/<conversation_id>/ 目录。"""

    def __init__(self, data_dir: PathLike, conversation_id: str):
        self.data_dir = Path(data_dir).expanduser()
        self.conversation_id = _validate_conversation_id(conversation_id)
        self._definition_cache: Optional[Dict[str, Any]] = None
        self.state: Dict[str, Any] = self.load()

    # ------------------------------------------------------------------ 路径/持久化

    def _dir(self) -> Path:
        return self.data_dir / WORKFLOW_STATES_DIRNAME / self.conversation_id

    def _state_path(self) -> Path:
        return self._dir() / "state.json"

    def _snapshot_path(self) -> Path:
        return self._dir() / WORKFLOW_SNAPSHOT_FILENAME

    @classmethod
    def load_from(cls, data_dir: PathLike, conversation_id: str) -> "WorkflowStateManager":
        return cls(data_dir, conversation_id)

    def load(self) -> Dict[str, Any]:
        path = self._state_path()
        if not path.exists():
            self.state = _empty_state()
            return self.state
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh) or {}
            merged = _empty_state()
            if isinstance(raw, dict):
                merged.update(raw)
            if not isinstance(merged.get("history"), list):
                merged["history"] = []
            if not isinstance(merged.get("pending_notices"), list):
                merged["pending_notices"] = []
            if not isinstance(merged.get("reject_counts"), dict):
                merged["reject_counts"] = {}
            self.state = merged
        except (OSError, json.JSONDecodeError, ValueError):
            self.state = _empty_state()
        return self.state

    def save(self) -> None:
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, ensure_ascii=False, indent=2)
        tmp.replace(path)

    # ------------------------------------------------------------------ 定义快照（只读）

    def load_definition(self) -> Optional[Dict[str, Any]]:
        """解析 WORKFLOW.md 快照为 camelCase 定义 dict（带缓存）。"""
        if self._definition_cache is not None:
            return self._definition_cache
        path = self._snapshot_path()
        if not path.exists():
            return None
        try:
            self._definition_cache = workflow_from_markdown(path.read_text(encoding="utf-8"), "snapshot")
        except Exception:
            self._definition_cache = None
        return self._definition_cache

    def get_node(self, node_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not node_id:
            return None
        definition = self.load_definition() or {}
        for node in definition.get("nodes") or []:
            if isinstance(node, dict) and node.get("id") == node_id:
                return node
        return None

    def entry_node(self) -> Optional[Dict[str, Any]]:
        """入口节点：start 节点的 next 指向。"""
        definition = self.load_definition() or {}
        for node in definition.get("nodes") or []:
            if isinstance(node, dict) and node.get("kind") == "start":
                return self.get_node(node.get("next"))
        return None

    # ------------------------------------------------------------------ 生命周期

    def activate(
        self,
        *,
        workflow_name: str,
        definition_markdown: str,
        entry_node_id: str,
        stage_start_msg_index: int = 0,
    ) -> Dict[str, Any]:
        """激活：复制定义快照 + 初始化状态（覆盖式，重新激活即从头开始）。"""
        target_dir = self._dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        self._snapshot_path().write_text(definition_markdown, encoding="utf-8")
        self._definition_cache = None
        self.state = _empty_state()
        self.state.update(
            {
                "active": True,
                "workflow_name": str(workflow_name or "").strip(),
                "status": STATUS_ACTIVE,
                "current_node_id": entry_node_id,
                "stage_start_msg_index": max(0, int(stage_start_msg_index or 0)),
                "started_at": time.time(),
            }
        )
        self.save()
        return deepcopy(self.state)

    def is_active(self) -> bool:
        return bool(self.state.get("active")) and self.state.get("status") == STATUS_ACTIVE

    def deactivate(self, *, status: str, reason: str) -> Dict[str, Any]:
        """摘牌：标记终态。状态目录保留供追溯，重新 activate 时整体重置。"""
        if status not in (STATUS_COMPLETED, STATUS_STOPPED, STATUS_FAILED):
            status = STATUS_STOPPED
        self.state["active"] = False
        self.state["status"] = status
        self.state["exit_reason"] = reason
        self.save()
        return deepcopy(self.state)

    # ------------------------------------------------------------------ 推进

    def get_current_node_id(self) -> Optional[str]:
        nid = self.state.get("current_node_id")
        return str(nid) if nid else None

    def record_stage_completion(self, *, summary: str, rounds: int) -> None:
        """把当前 stage 记入 history（不前移）。只在「确定不再被驳回」的落地分支调用：
        审核驳回时当前 stage 不算完成，不记录。"""
        finished_node_id = self.get_current_node_id()
        finished_node = self.get_node(finished_node_id) or {}
        history = self.state.get("history")
        if not isinstance(history, list):
            history = []
        history.append(
            {
                "node_id": finished_node_id,
                "kind": "stage",
                "name": str(finished_node.get("name") or finished_node_id or ""),
                "summary": str(summary or ""),
                "rounds": max(0, int(rounds or 0)),
                "at": time.time(),
            }
        )
        self.state["history"] = history
        self.save()

    def move_to(self, node_id: str, *, msg_index: int) -> None:
        """当前节点前移（不记 history）：重置阶段计数并更新消息游标。"""
        self.state["current_node_id"] = str(node_id)
        self.state["stage_rounds"] = 0
        self.state["round_limit_notified"] = False
        self.state["stage_start_msg_index"] = max(0, int(msg_index or 0))
        self.save()

    def advance_to(
        self,
        node_id: str,
        *,
        summary: str,
        rounds: int,
        msg_index: int,
    ) -> None:
        """组合便捷方法：记 stage 完成 + 前移（等价 record_stage_completion + move_to）。"""
        self.record_stage_completion(summary=summary, rounds=rounds)
        self.move_to(node_id, msg_index=msg_index)

    def record_review(self, *, node_id: str, name: str, decision: str, message: str) -> None:
        """审核（瞬态节点）记入 history。"""
        history = self.state.get("history")
        if not isinstance(history, list):
            history = []
        history.append(
            {
                "node_id": str(node_id),
                "kind": "review",
                "name": str(name or node_id),
                "decision": str(decision or ""),
                "message": str(message or ""),
                "at": time.time(),
            }
        )
        self.state["history"] = history
        self.save()

    def increment_reject(self, node_id: str) -> int:
        counts = self.state.get("reject_counts")
        if not isinstance(counts, dict):
            counts = {}
        counts[node_id] = int(counts.get(node_id) or 0) + 1
        self.state["reject_counts"] = counts
        self.save()
        return counts[node_id]

    def get_reject_count(self, node_id: str) -> int:
        counts = self.state.get("reject_counts")
        if not isinstance(counts, dict):
            return 0
        try:
            return int(counts.get(node_id) or 0)
        except (TypeError, ValueError):
            return 0

    # ------------------------------------------------------------------ 阶段轮数（跨任务累计）

    def increment_stage_rounds(self) -> int:
        self.state["stage_rounds"] = int(self.state.get("stage_rounds") or 0) + 1
        self.save()
        return self.state["stage_rounds"]

    def reset_stage_rounds(self) -> None:
        """用户新消息到达（知情交互）时清零，撞限询问后可再次计数。"""
        self.state["stage_rounds"] = 0
        self.state["round_limit_notified"] = False
        self.save()

    def get_stage_rounds(self) -> int:
        try:
            return int(self.state.get("stage_rounds") or 0)
        except (TypeError, ValueError):
            return 0

    def round_limit_notified(self) -> bool:
        return bool(self.state.get("round_limit_notified"))

    def mark_round_limit_notified(self) -> None:
        self.state["round_limit_notified"] = True
        self.save()

    # ------------------------------------------------------------------ 消息游标

    def get_stage_start_msg_index(self) -> int:
        try:
            return int(self.state.get("stage_start_msg_index") or 0)
        except (TypeError, ValueError):
            return 0

    # ------------------------------------------------------------------ 柔性通知池

    def push_notice(self, *, notice_type: str, message: str) -> None:
        notices = self.state.get("pending_notices")
        if not isinstance(notices, list):
            notices = []
        notices.append(
            {
                "type": str(notice_type or "workflow"),
                "message": str(message or ""),
                "created_at": time.time(),
            }
        )
        self.state["pending_notices"] = notices
        self.save()

    def has_pending_notices(self) -> bool:
        notices = self.state.get("pending_notices")
        return isinstance(notices, list) and len(notices) > 0

    def poll_notices(self) -> List[Dict[str, Any]]:
        """取出全部待通知项（取出即清除，确保不被重复消费）。"""
        notices = self.state.get("pending_notices")
        if not isinstance(notices, list) or not notices:
            return []
        out = [n for n in notices if isinstance(n, dict)]
        self.state["pending_notices"] = []
        self.save()
        return out

    def restore_notices(self, notices: List[Dict[str, Any]]) -> None:
        """派发失败时把通知放回池（回滚，避免静默丢失）。"""
        if not notices:
            return
        existing = self.state.get("pending_notices")
        if not isinstance(existing, list):
            existing = []
        self.state["pending_notices"] = list(notices) + existing
        self.save()

    # ------------------------------------------------------------------ 前端快照

    def progress_snapshot(self) -> Dict[str, Any]:
        """对齐前端 stores/workflow.ts 的 WorkflowSnapshot。"""
        if not self.is_active():
            return {"active": False}
        history = [
            {"name": str(h.get("name") or ""), "rounds": h.get("rounds")}
            for h in (self.state.get("history") or [])
            if isinstance(h, dict) and h.get("kind") == "stage"
        ]
        current_node = self.get_node(self.get_current_node_id())
        current = None
        next_name: Optional[str] = None
        if current_node:
            current = {"name": str(current_node.get("name") or ""), "rounds": self.get_stage_rounds()}
            if current_node.get("kind") == "stage":
                nxt = self.get_node(current_node.get("next"))
                if nxt:
                    next_name = str(nxt.get("name") or "")
            # branch（待选择）/ 其他：next 为 None（未来不可知）
        return {
            "active": True,
            "name": str(self.state.get("workflow_name") or ""),
            "status": self.state.get("status"),
            "history": history,
            "current": current,
            "next": next_name,
            "reviewing": False,
            "footnote": None,
        }
