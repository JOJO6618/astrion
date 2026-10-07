from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, Optional

from modules.i18n import tr
from modules.tool_approval_manager import ToolApprovalManager


# 模型请求沿用审核智能体配置，并限制整轮审核等待时间。
DEFAULT_FULL_ACCESS_REVIEW_TIMEOUT_SECONDS = 3600.0


async def run_full_access_review(
    *,
    manager: ToolApprovalManager,
    approval_id: str,
    username: str,
    reviewer: Callable[..., Awaitable[Dict[str, Any]]],
    sender: Callable[[str, Dict[str, Any]], Any],
    timeout_seconds: Optional[float] = DEFAULT_FULL_ACCESS_REVIEW_TIMEOUT_SECONDS,
    stop_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """运行双重审核的自动部分；pending 结果仍须由调用方等待人工裁决。

    reviewer 接收 progress_cb / cancel_check，与 ApprovalAgent.review 兼容。
    不授予执行权限，不创建第二个审批；事件会话归属取自原审批条目。
    """
    item = manager.get(approval_id)
    if not item:
        raise KeyError(tr("tool_approval.request_not_found"))
    if item.get("username") != username:
        raise PermissionError(tr("tool_approval.no_permission"))
    if item.get("approval_type") != "full_access":
        raise ValueError("run_full_access_review requires a full_access request")
    conversation_id = item.get("conversation_id")

    def _progress(evt: Dict[str, Any]) -> None:
        sender("auto_approval_progress", {
            "approval_id": approval_id,
            "progress": evt,
            "conversation_id": conversation_id,
        })

    def _out(row: Optional[Dict[str, Any]], source: str) -> Dict[str, Any]:
        status = (row or {}).get("status")
        result = {
            "decision": status if status in {"pending", "approved", "rejected"} else "rejected",
            "item": row,
            "source": source,
        }
        if status == "expired":
            result["code"] = "approval_expired"
        elif not row:
            result["code"] = "approval_missing"
        return result

    def _takeover() -> Optional[Dict[str, Any]]:
        row = manager.get(approval_id)
        if stop_check is not None and stop_check():
            row = manager.mark_expired(approval_id)
            return _out(row, "system")
        if not row or row.get("status") == "expired":
            return _out(row, "system")
        if row.get("status") in {"approved", "rejected"}:
            source = "manual" if row.get("human_decision") == "rejected" else "approval_agent"
            return _out(row, source)
        # 自动项已经裁决的重复调用无需再次请求模型。人工先通过不能取消审核。
        if row.get("auto_review_status") in {"approved", "rejected"}:
            return _out(row, "approval_agent")
        return None

    def _finish(out: Dict[str, Any]) -> Dict[str, Any]:
        row = out.get("item")
        if row:
            sender("tool_approval_required", {"approval": row, "conversation_id": conversation_id})
        _progress({"stage": "done", "message": tr("auto_approval.done"), "decision": out.get("decision")})
        return out

    _progress({"stage": "start", "message": tr("auto_approval.start")})
    takeover = _takeover()
    if takeover:
        return _finish(takeover)
    if not item.get("auto_review_required"):
        return _finish(_out(manager.get(approval_id), "manual"))

    reviewing = manager.mark_auto_reviewing(approval_id)
    if reviewing:
        sender("tool_approval_required", {"approval": reviewing, "conversation_id": conversation_id})
    takeover = _takeover()
    if takeover:
        return _finish(takeover)
    async def _review_until_resolved() -> Dict[str, Any]:
        review_task = asyncio.create_task(reviewer(progress_cb=_progress, cancel_check=_takeover))
        try:
            while True:
                done, _ = await asyncio.wait({review_task}, timeout=0.2)
                if done:
                    return await review_task
                if _takeover():
                    return {}
        finally:
            if not review_task.done():
                review_task.cancel()
            await asyncio.gather(review_task, return_exceptions=True)

    try:
        result = await asyncio.wait_for(_review_until_resolved(), timeout=timeout_seconds)
    except asyncio.CancelledError:
        expired = manager.mark_expired(approval_id)
        _finish(_out(expired, "system"))
        raise
    except Exception as exc:
        result = {
            "decision": "rejected",
            "reason": tr("approval_agent.request_failed", status=str(exc) or type(exc).__name__),
        }

    takeover = _takeover()
    if takeover:
        return _finish(takeover)
    if not isinstance(result, dict):
        result = {}
    decision = str(result.get("decision") or "").strip().lower()
    if decision not in {"approved", "rejected"}:
        decision = "rejected"
        reason = tr("approval_agent.invalid_decision")
    else:
        reason = str(result.get("reason") or "").strip() or tr("approval_agent.no_reason")
    decided = manager.record_auto_review(approval_id, decision=decision, reason=reason)
    return _finish(_out(decided, "approval_agent"))
