from __future__ import annotations
from typing import Any, Dict


_VALID_USER_MESSAGE_SOURCES = {
    "user",
    "guidance",
    "notify",
    "presend",
    "sub_agent",
    "background_command",
    "goal",
    "goal_prompt",
    "goal_review",
    "goal_recovery",
    "compression",
    "compression_handoff",
    "permission",
    "sandbox",
    "skill",
    "workflow",
}

def _user_message_ui_defaults(source: str, *, auto_user_message_event: bool = False) -> Dict[str, Any]:
    normalized = str(source or "").strip().lower()
    if normalized in {"skill", "goal_prompt", "permission", "sandbox"}:
        return {"visibility": "hidden", "starts_work": False}
    if normalized in {"guidance", "notify"}:
        return {"visibility": "compact", "starts_work": False}
    # 后台完成通知 / 运行期压缩续接：本质属于当前这轮工作的延续，
    # 不应暂停上一轮计时器、也不应开启新的工作头与计时器。
    if normalized in {"sub_agent", "background_command", "compression", "compression_handoff"}:
        return {"visibility": "compact", "starts_work": False}
    # 目标审核续命：确实开启新一轮工作，保持 starts_work=True。
    if normalized in {"goal_review", "goal_recovery"}:
        return {"visibility": "compact", "starts_work": True}
    # 工作流激活/柔性通知（任务入口派发）：开启新一轮工作，显示工作头与计时器。
    # 运行期 inline 注入走 _runtime_message_ui_defaults（延续当前工作段，不受影响）。
    if normalized == "workflow":
        return {"visibility": "chat", "starts_work": True}
    if normalized == "presend":
        return {"visibility": "chat", "starts_work": True}
    if normalized == "goal":
        return {"visibility": "compact", "starts_work": True}
    return {"visibility": "chat", "starts_work": normalized == "user" and not auto_user_message_event}
