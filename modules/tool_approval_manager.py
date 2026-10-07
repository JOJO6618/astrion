from __future__ import annotations

import threading
import time
import uuid
from copy import deepcopy
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
        approval_type: str = "sandbox_write",
        auto_review_required: bool = False,
        executor_id: Optional[int] = None,
        workspace_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        if approval_type not in {"sandbox_write", "full_access"}:
            raise ValueError("approval_type must be sandbox_write or full_access")
        if not isinstance(auto_review_required, bool):
            raise ValueError("auto_review_required must be a bool")
        approval_id = f"approval_{uuid.uuid4().hex}"
        item = {
            "approval_id": approval_id,
            "username": username,
            "conversation_id": conversation_id,
            "task_id": task_id,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "arguments": deepcopy(arguments or {}),
            "preview": deepcopy(preview or {}),
            "status": "pending",
            "created_at": time.time(),
            "decided_at": None,
            "decision": None,
            "approval_type": approval_type,
            "auto_review_required": auto_review_required,
            "executor_id": executor_id,
            "workspace_root": workspace_root,
        }
        if approval_type == "full_access":
            item.update({
                "auto_review_status": "pending",
                "human_decision": None,
                "human_reason": None,
                "auto_review_reason": None,
                "reason": None,
                "human_decided_at": None,
                "auto_review_started_at": None,
                "auto_reviewed_at": None,
            })
        with self._lock:
            self._items[approval_id] = item
        return deepcopy(item)

    def claim_execution(self, approval_id: str, expected: Dict[str, Any]) -> Dict[str, Any]:
        """原子领取可信批准记录；每个审批最多颁发一次执行权限。"""
        with self._lock:
            self._prune_resolved()
            item = self._items.get(approval_id)
            if not item or item.get("status") != "approved" or item.get("execution_claimed_at"):
                raise ValueError("审批未通过、已过期或执行授权已领取。")
            keys = ("username", "conversation_id", "task_id", "tool_call_id", "tool_name", "arguments", "approval_type", "executor_id", "workspace_root")
            if any(item.get(key) != expected.get(key) for key in keys):
                raise ValueError("执行请求与可信审批记录不一致。")
            if item.get("approval_type") == "full_access" and (
                item.get("human_decision") != "approved"
                or (item.get("auto_review_required") and item.get("auto_review_status") != "approved")
            ):
                raise ValueError("单次完全访问尚未满足全部审批条件。")
            item["execution_claimed_at"] = time.time()
            return deepcopy(item)

    def get(self, approval_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._prune_resolved()
            item = self._items.get(approval_id)
            return deepcopy(item) if item else None

    def mark_expired(self, approval_id: str) -> Optional[Dict[str, Any]]:
        """将 pending 条目标记为 expired 终态（等待方超时/停止/取消时调用）。

        幂等：非 pending 返回现状；不存在返回 None。过期后迟到的 decide
        因状态非 pending 返回现状（不再生效）。
        """
        with self._lock:
            self._prune_resolved()
            item = self._items.get(approval_id)
            if not item or item.get("status") != "pending":
                return deepcopy(item) if item else None
            item["status"] = "expired"
            item["decided_at"] = time.time()
            return deepcopy(item)

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
                rows.append(deepcopy(item))
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
                return deepcopy(item)
            if item.get("approval_type") == "full_access":
                if item.get("human_decision") == normalized:
                    return deepcopy(item)
                item["human_decision"] = normalized
                item["human_decided_at"] = time.time()
                item["human_reason"] = reason.strip() if isinstance(reason, str) and reason.strip() else None
                if item["human_reason"]:
                    item["reason"] = item["human_reason"]
                if isinstance(decider, str) and decider.strip():
                    item["decider"] = decider.strip()
                self._resolve_full_access(item)
                return deepcopy(item)
            item["status"] = normalized
            item["decision"] = normalized
            item["decided_at"] = time.time()
            if isinstance(reason, str) and reason.strip():
                item["reason"] = reason.strip()
            if isinstance(decider, str) and decider.strip():
                item["decider"] = decider.strip()
            return deepcopy(item)

    @staticmethod
    def _resolve_full_access(item: Dict[str, Any]) -> None:
        """合并两项裁决；仅在持锁且整体状态仍为 pending 时调用。"""
        human = item.get("human_decision")
        auto = item.get("auto_review_status")
        if human == "rejected" or auto == "rejected":
            status = "rejected"
            item["reason"] = (
                item.get("human_reason") if human == "rejected" else item.get("auto_review_reason")
            )
        elif human == "approved" and (not item.get("auto_review_required") or auto == "approved"):
            status = "approved"
        else:
            return
        item["status"] = status
        item["decision"] = status
        item["decided_at"] = time.time()

    def mark_auto_reviewing(self, approval_id: str) -> Optional[Dict[str, Any]]:
        """可信后端标记自动审核开始；重复调用及终态条目不变。"""
        with self._lock:
            self._prune_resolved()
            item = self._items.get(approval_id)
            if not item or item.get("status") != "pending":
                return deepcopy(item) if item else None
            if item.get("approval_type") != "full_access":
                raise ValueError("automatic review state is only available for full_access")
            if item.get("auto_review_status") == "pending":
                item["auto_review_status"] = "reviewing"
                item["auto_review_started_at"] = time.time()
            return deepcopy(item)

    def record_auto_review(
        self,
        approval_id: str,
        decision: str,
        *,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """仅供可信自动审核链路提交结果，不能由人工 decision 接口调用。

        每项自动裁决仅记录一次。拒绝、过期或已批准的整体终态不可更改，
        自动通过但人工尚未裁决时整体仍为 pending。
        """
        normalized = str(decision or "").strip().lower()
        if normalized not in {"approved", "rejected"}:
            raise ValueError(tr("tool_approval.decision_invalid"))
        with self._lock:
            self._prune_resolved()
            item = self._items.get(approval_id)
            if not item:
                raise KeyError(tr("tool_approval.request_not_found"))
            if item.get("approval_type") != "full_access":
                raise ValueError("automatic review state is only available for full_access")
            if item.get("status") != "pending" or item.get("auto_review_status") in {"approved", "rejected"}:
                return deepcopy(item)
            item["auto_review_status"] = normalized
            item["auto_reviewed_at"] = time.time()
            item["auto_review_reason"] = reason.strip() if isinstance(reason, str) and reason.strip() else None
            if item["auto_review_reason"]:
                item["reason"] = item["auto_review_reason"]
            item["decider"] = "approval_agent"
            self._resolve_full_access(item)
            return deepcopy(item)
