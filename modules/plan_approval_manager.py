from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from modules.i18n import tr

# 计划文档内容送进弹窗/记录的最大字符数（超出截断，完整内容始终在计划文件里）
PLAN_CONTENT_MAX_CHARS = 20000

# 终态条目保留时长（秒）：与任务记录终态清理（3600s）对齐。
# pending 条目永不自动清理；等待方退出时经 mark_expired 转为终态后惰性回收。
RESOLVED_TTL_SECONDS = 3600.0


class PlanApprovalManager:
    """In-memory manager for plan-mode plan approval requests (submit_plan 工具).

    与 UserQuestionManager 同构：模型在计划模式下调用 submit_plan 后阻塞等待，
    前端弹窗展示计划文档内容，用户批准（可附意见）或拒绝（附意见）。
    """

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
            and float(item.get("resolved_at") or item.get("created_at") or 0.0) + RESOLVED_TTL_SECONDS <= now
        ]
        for key in expired_keys:
            self._items.pop(key, None)

    def mark_expired(self, approval_id: str) -> Optional[Dict[str, Any]]:
        """将 pending 条目标记为 expired 终态（等待方超时/停止/取消时调用，幂等）。"""
        with self._lock:
            self._prune_resolved()
            item = self._items.get(approval_id)
            if not item or item.get("status") != "pending":
                return dict(item) if item else None
            item["status"] = "expired"
            item["resolved_at"] = time.time()
            return dict(item)

    def create_request(
        self,
        *,
        username: str,
        conversation_id: Optional[str],
        task_id: Optional[str],
        tool_call_id: Optional[str],
        plan_file: str,
        plan_content: str,
        summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        approval_id = f"plan_approval_{uuid.uuid4().hex}"
        content = str(plan_content or "")
        truncated = False
        if len(content) > PLAN_CONTENT_MAX_CHARS:
            content = content[:PLAN_CONTENT_MAX_CHARS]
            truncated = True
        item = {
            "approval_id": approval_id,
            "username": username,
            "conversation_id": conversation_id,
            "task_id": task_id,
            "tool_call_id": tool_call_id,
            "plan_file": str(plan_file or "").strip(),
            "plan_content": content,
            "plan_content_truncated": truncated,
            "summary": str(summary or "").strip()[:300],
            "status": "pending",
            "created_at": time.time(),
            "resolved_at": None,
            "comment": "",
        }
        with self._lock:
            self._items[approval_id] = item
        return dict(item)

    def get(self, approval_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._prune_resolved()
            item = self._items.get(approval_id)
            return dict(item) if item else None

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

    def answer(
        self,
        *,
        approval_id: str,
        username: str,
        approved: bool,
        comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        clean_comment = str(comment or "").strip()
        with self._lock:
            item = self._items.get(approval_id)
            if not item:
                raise KeyError(tr("plan_approval.request_not_found"))
            if item.get("username") != username:
                raise PermissionError(tr("plan_approval.no_permission"))
            if item.get("status") != "pending":
                return dict(item)
            item["status"] = "approved" if approved else "rejected"
            item["resolved_at"] = time.time()
            item["comment"] = clean_comment[:2000]
            return dict(item)
