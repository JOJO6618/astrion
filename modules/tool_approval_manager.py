from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, List, Optional


class ToolApprovalManager:
    def __init__(self):
        self._items: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

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
            item = self._items.get(approval_id)
            return dict(item) if item else None

    def list_pending(self, username: str, conversation_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
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
            raise ValueError("decision 仅支持 approved / rejected")
        with self._lock:
            item = self._items.get(approval_id)
            if not item:
                raise KeyError("审批请求不存在")
            if item.get("username") != username:
                raise PermissionError("无权限操作该审批请求")
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
