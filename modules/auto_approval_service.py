from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from modules.approval_agent import ApprovalAgent
from server.state import tool_approval_manager


def build_auto_approval_payload(
    *,
    web_terminal: Any,
    recent_tool_actions: List[Dict[str, Any]],
    function_name: str,
    arguments: Dict[str, Any],
    risk_markers: Optional[List[str]] = None,
) -> str:
    history = list(getattr(getattr(web_terminal, "context_manager", None), "conversation_history", []) or [])
    user_input = ""
    for msg in reversed(history):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        metadata = msg.get("metadata") or {}
        if bool(metadata.get("is_auto_generated")):
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            user_input = content.strip()
            break

    recent = recent_tool_actions[-3:] if isinstance(recent_tool_actions, list) else []
    lines = [
        "请按照流程审批：",
        f"用户输入：{user_input or '（空）'}",
        "AI运行的前三步操作：",
    ]
    if recent:
        for idx, row in enumerate(recent, start=1):
            lines.append(f"{idx}. {row.get('tool_name')}: {json.dumps(row.get('arguments') or {}, ensure_ascii=False)}")
    else:
        lines.append("（无）")
    lines += [
        f"当前工作目录：{('/workspace' if bool(getattr(getattr(web_terminal, 'container_session', None), 'mode', None) == 'docker') else str(getattr(getattr(web_terminal, 'context_manager', None), 'project_path', '')))}",
    ]
    if risk_markers:
        lines.append(f"当前操作中触发风险特征的内容：{', '.join([str(x) for x in risk_markers if str(x).strip()])}")
    lines += [
        "请审核当前操作：",
        f"{function_name}: {json.dumps(arguments or {}, ensure_ascii=False)}",
        "仅判断危险性和越权风险，不判断任务必要性。",
    ]
    return "\n".join(lines)


async def run_auto_approval(
    *,
    web_terminal: Any,
    username: str,
    approval_id: str,
    conversation_id: Optional[str],
    recent_tool_actions: List[Dict[str, Any]],
    function_name: str,
    arguments: Dict[str, Any],
    risk_markers: Optional[List[str]],
    sender,
) -> Dict[str, Any]:
    def _progress(evt: Dict[str, Any]) -> None:
        sender("auto_approval_progress", {"approval_id": approval_id, "progress": evt, "conversation_id": conversation_id})

    def _manual_takeover() -> Optional[Dict[str, Any]]:
        row = tool_approval_manager.get(approval_id)
        status = (row or {}).get("status")
        if status in {"approved", "rejected"}:
            return {"decision": status, "item": row, "source": "manual"}
        return None

    _progress({"stage": "start", "message": "自动审批开始"})
    payload = build_auto_approval_payload(
        web_terminal=web_terminal,
        recent_tool_actions=recent_tool_actions,
        function_name=function_name,
        arguments=arguments,
        risk_markers=risk_markers,
    )
    agent = ApprovalAgent(web_terminal=web_terminal)
    result = await agent.review(payload_text=payload, progress_cb=_progress, cancel_check=_manual_takeover)
    if not _manual_takeover():
        decided = tool_approval_manager.decide(
            approval_id=approval_id,
            username=username,
            decision=str(result.get("decision") or "rejected"),
            reason=str(result.get("reason") or "").strip(),
            decider="approval_agent",
        )
        out = {"decision": decided.get("status"), "item": decided, "source": "approval_agent"}
    else:
        out = _manual_takeover() or {"decision": "rejected", "reason": "人工接管失败"}
    _progress({"stage": "done", "message": "自动审批结束", "decision": (out or {}).get("decision")})
    return out
