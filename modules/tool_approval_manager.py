from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from modules.i18n import tr

# 终态条目保留时长（秒）：与任务记录终态清理（3600s）对齐。
# pending 条目永不自动清理——仍属合法等待；等待方退出（超时/停止/取消）
# 时会经 mark_expired 转为 expired 终态，再由本 TTL 惰性回收。
RESOLVED_TTL_SECONDS = 3600.0


class ToolApprovalManager:
    def __init__(self):
        self._items: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _prune_resolved(self, now: Optional[float] = None) -> None:
        """惰性清理过期终态条目（锁内调用）。"""
        now = now if now is not None else time.time()
        expired_keys = [
            key
            for key, item in self._items.items()
            if item.get("status") != "pending"
            and float(item.get("decided_at") or item.get("created_at") or 0.0) + RESOLVED_TTL_SECONDS <= now
        ]
        for key in expired_keys:
            self._items.pop(key, None)

    def create_request(
        self,
        *,
        username: str,
        conversation_id: Optional[str],
        task_id: Optional[str],
        tool_call_id: Optional[str],
        tool_name: str,
        arguments: Dict[str, Any],
        preview: Dict[str, Any],
    ) -> Dict[str, Any]:
        approval_id = f"approval_{uuid.uuid4().hex}"
        item = {
            "approval_id": approval_id,
            "username": username,
            "conversation_id": conversation_id,
            "task_id": task_id,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "arguments": arguments or {},
            "preview": preview or {},
            "status": "pending",
            "created_at": time.time(),
            "decided_at": None,
            "decision": None,
        }
        with self._lock:
            self._items[approval_id] = item
        return dict(item)

    def get(self, approval_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._prune_resolved()
            item = self._items.get(approval_id)
            return dict(item) if item else None

    def mark_expired(self, approval_id: str) -> Optional[Dict[str, Any]]:
        """将 pending 条目标记为 expired 终态（等待方超时/停止/取消时调用）。

        幂等：非 pending 返回现状；不存在返回 None。过期后迟到的 decide
        因状态非 pending 返回现状（不再生效）。
        """
        with self._lock:
            self._prune_resolved()
            item = self._items.get(approval_id)
            if not item or item.get("status") != "pending":
                return dict(item) if item else None
            item["status"] = "expired"
            item["decided_at"] = time.time()
            return dict(item)

    def list_pending(self, username: str, conversation_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            self._prune_resolved()
            rows = []
            for item in self._items.values():
                if item.get("username") != username:
                    continue
                if item.get("status") != "pending":
                    continue
                if conversation_id and item.get("conversation_id") != conversation_id:
                    continue
                rows.append(dict(item))
            rows.sort(key=lambda x: x.get("created_at", 0.0))
            return rows

    def decide(
        self,
        approval_id: str,
        username: str,
        decision: str,
        *,
        reason: Optional[str] = None,
        decider: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized = str(decision or "").strip().lower()
        if normalized not in {"approved", "rejected"}:
            raise ValueError(tr("tool_approval.decision_invalid"))
        with self._lock:
            item = self._items.get(approval_id)
            if not item:
                raise KeyError(tr("tool_approval.request_not_found"))
            if item.get("username") != username:
                raise PermissionError(tr("tool_approval.no_permission"))
            if item.get("status") != "pending":
                return dict(item)
            item["status"] = normalized
            item["decision"] = normalized
            item["decided_at"] = time.time()
            if isinstance(reason, str) and reason.strip():
                item["reason"] = reason.strip()
            if isinstance(decider, str) and decider.strip():
                item["decider"] = decider.strip()
            return dict(item)
