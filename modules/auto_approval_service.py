from __future__ import annotations

from typing import Any, Dict, List, Optional

from modules.approval_agent import ApprovalAgent
from modules.approval_request import ApprovalRequest
from modules.full_access_review import run_full_access_review
from server.state import tool_approval_manager
from modules.i18n import tr


def build_auto_approval_payload(
    *, web_terminal: Any, recent_tool_actions: List[Dict[str, Any]],
    function_name: str, arguments: Dict[str, Any],
    risk_markers: Optional[List[str]] = None,
    approval_item: Optional[dict] = None, username: str = "",
) -> str:
    """Compatibility entry point; execution history comes from paired messages."""
    item = approval_item or {
        "conversation_id": getattr(web_terminal.context_manager, "current_conversation_id", None),
        "approval_type": "full_access" if arguments.get("request_full_access") else "sandbox_write",
    }
    return ApprovalRequest.capture(web_terminal, username, item).render(function_name, arguments, risk_markers)


async def run_auto_approval(
    *, web_terminal: Any, username: str, approval_id: str,
    conversation_id: Optional[str], recent_tool_actions: List[Dict[str, Any]],
    function_name: str, arguments: Dict[str, Any], risk_markers: Optional[List[str]],
    sender, stop_check=None,
) -> Dict[str, Any]:
    item = tool_approval_manager.get(approval_id)
    if not item:
        return {"decision": "error", "code": "approval_missing", "reason": "审批请求不存在。"}

    def _progress(evt: Dict[str, Any]) -> None:
        sender("auto_approval_progress", {"approval_id": approval_id, "progress": evt, "conversation_id": conversation_id})

    def _manual_takeover() -> Optional[Dict[str, Any]]:
        row = tool_approval_manager.get(approval_id)
        if stop_check is not None and stop_check():
            row = tool_approval_manager.mark_expired(approval_id)
        status = (row or {}).get("status")
        if status == "expired":
            return {"decision": "rejected", "item": row, "source": "system", "code": "approval_expired"}
        if status in {"approved", "rejected"}:
            return {"decision": status, "item": row, "source": "manual"}
        return None

    async def _review(*, progress_cb, cancel_check) -> dict:
        # One automatic re-review handles an instruction arriving during review.
        # Continuous changes fail closed rather than looping or asking the user.
        for attempt in range(2):
            try:
                request = ApprovalRequest.capture(web_terminal, username, item)
            except Exception:
                return {"decision": "error", "code": "review_context_failed", "reason": "审核上下文读取失败，当前调用未执行。"}
            agent = ApprovalAgent(web_terminal=web_terminal, conversation_id=conversation_id)
            agent.review_history = request.history
            result = await agent.review(
                payload_text=request.render(function_name, arguments, risk_markers),
                progress_cb=progress_cb, cancel_check=cancel_check,
            )
            if cancel_check and cancel_check():
                return result
            try:
                current = request.is_current()
            except Exception:
                return {"decision": "error", "code": "review_context_failed", "reason": "审核上下文校验失败，当前调用未执行。"}
            if not current:
                if attempt == 0:
                    progress_cb({"stage": "context_changed", "message": "用户要求或执行条件已变化，重新审核。"})
                    continue
                return {"decision": "error", "code": "review_context_changed", "reason": "审核期间输入持续变化，当前调用未执行，请按最新要求重新审核。"}
            tool_approval_manager.bind_review_context(approval_id, request.fingerprint)
            return result
        raise AssertionError("unreachable review loop")

    if item.get("approval_type") == "full_access":
        return await run_full_access_review(
            manager=tool_approval_manager, approval_id=approval_id, username=username,
            reviewer=_review, sender=sender, stop_check=stop_check,
        )

    _progress({"stage": "start", "message": tr("auto_approval.start")})
    result = await _review(progress_cb=_progress, cancel_check=_manual_takeover)
    takeover = _manual_takeover()
    if takeover:
        out = takeover
    elif result.get("decision") not in {"approved", "rejected"}:
        code = str(result.get("code") or "review_invalid_decision")
        reason = str(result.get("reason") or "审核未返回有效裁决，当前调用未执行。")
        row = tool_approval_manager.mark_review_failed(approval_id, code=code, reason=reason)
        out = {"decision": "error", "code": code, "reason": reason, "item": row, "source": "approval_agent"}
    else:
        decided = tool_approval_manager.decide(
            approval_id=approval_id, username=username, decision=result["decision"],
            reason=str(result.get("reason") or "").strip(), decider="approval_agent",
        )
        out = {"decision": decided.get("status"), "item": decided, "source": "approval_agent"}
    _progress({
        "stage": "done", "message": tr("auto_approval.done"), "source": out.get("source"),
        "decision": out.get("decision") if out.get("source") == "approval_agent" else None,
        "code": out.get("code"),
    })
    return out
